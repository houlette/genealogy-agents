"""Tests for client.py with urllib mocked (no daemon, no network):
python3 -m unittest discover -s tools/browser_bridge -t tools/browser_bridge"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import client  # noqa: E402


class FakeResponse(io.BytesIO):
    """What urlopen returns: a readable body usable as a context manager."""

    def __init__(self, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode('utf-8')
        super().__init__(body)


def http_error(code, body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
    return urllib.error.HTTPError('http://127.0.0.1:1/x', code, 'err', {}, io.BytesIO(raw))


class ClientCase(unittest.TestCase):
    def setUp(self):
        self._url, self._agent = client.URL, client.AGENT
        client.URL = 'http://127.0.0.1:9999'
        client.AGENT = None

    def tearDown(self):
        client.URL, client.AGENT = self._url, self._agent

    def call(self, fn, reply, *args, **kwargs):
        """Run fn with urlopen returning `reply`; return (result, urlopen mock)."""
        with patch.object(client.urllib.request, 'urlopen',
                          return_value=FakeResponse(reply)) as urlopen:
            result = fn(*args, **kwargs)
        return result, urlopen

    @staticmethod
    def sent(urlopen):
        req = urlopen.call_args.args[0]
        body = json.loads(req.data) if req.data else None
        return req, body


class RequestShapeTest(ClientCase):
    def test_post_url_method_headers_and_body(self):
        _, urlopen = self.call(client._post, {'ok': 1}, '/claim', {'site': 'x'})
        req, body = self.sent(urlopen)
        self.assertEqual(req.full_url, 'http://127.0.0.1:9999/claim')
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.get_header('Content-type'), 'application/json')
        self.assertEqual(body, {'site': 'x'})

    def test_get_has_no_body_and_uses_get(self):
        _, urlopen = self.call(client.claims, {'sites': {}})
        req, body = self.sent(urlopen)
        self.assertEqual(req.full_url, 'http://127.0.0.1:9999/claims')
        self.assertEqual(req.get_method(), 'GET')
        self.assertIsNone(body)

    def test_agent_from_set_agent_is_added_to_every_post(self):
        client.set_agent('gemini')
        _, urlopen = self.call(client._post, {}, '/open', {'url': 'u'})
        self.assertEqual(self.sent(urlopen)[1], {'url': 'u', 'agent': 'gemini'})

    def test_no_agent_means_no_agent_field(self):
        _, urlopen = self.call(client._post, {}, '/open', {'url': 'u'})
        self.assertNotIn('agent', self.sent(urlopen)[1])

    def test_explicit_agent_in_payload_is_kept(self):
        client.set_agent('gemini')
        payload = {'agent': 'other'}
        _, urlopen = self.call(client._post, {}, '/open', payload)
        self.assertEqual(self.sent(urlopen)[1]['agent'], 'other')
        self.assertEqual(payload, {'agent': 'other'})

    def test_post_does_not_mutate_callers_payload(self):
        client.set_agent('gemini')
        payload = {'url': 'u'}
        self.call(client._post, {}, '/open', payload)
        self.assertEqual(payload, {'url': 'u'})

    def test_timeout_is_passed_to_urlopen(self):
        _, urlopen = self.call(client._post, {}, '/x', {}, timeout=7)
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 7)

    def test_non_ascii_body_is_utf8(self):
        _, urlopen = self.call(client._post, {}, '/x', {'q': 'Kowalczyński'})
        self.assertIn('Kowalczyński', json.loads(self.sent(urlopen)[0].data.decode('utf-8'))['q'])


class AgentAndPortTest(unittest.TestCase):
    """URL and AGENT are fixed at import, so check them in a fresh interpreter."""

    def probe(self, env_extra=None, project=None):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items()
                   if k not in ('BRIDGE_PORT', 'BRIDGE_AGENT', 'GENEALOGY_PROJECT')}
            env['GENEALOGY_PROJECT'] = tmp
            if project is not None:
                with open(os.path.join(tmp, 'project.json'), 'w') as f:
                    json.dump(project, f)
            env.update(env_extra or {})
            code = ('import sys; sys.path.insert(0, %r); import client; '
                    'print(client.URL); print(client.AGENT)' % HERE)
            out = subprocess.run([sys.executable, '-c', code], env=env, cwd=tmp,
                                 capture_output=True, text=True, check=True).stdout.split('\n')
            return out[0], out[1]

    def test_defaults(self):
        self.assertEqual(self.probe(), ('http://127.0.0.1:9333', 'None'))

    def test_port_from_project_json(self):
        self.assertEqual(self.probe(project={'bridge': {'port': 9444}})[0], 'http://127.0.0.1:9444')

    def test_bridge_port_env_beats_project_json(self):
        url, _ = self.probe({'BRIDGE_PORT': '9555'}, project={'bridge': {'port': 9444}})
        self.assertEqual(url, 'http://127.0.0.1:9555')

    def test_agent_from_environment(self):
        self.assertEqual(self.probe({'BRIDGE_AGENT': 'claude-sub-1'})[1], 'claude-sub-1')

    def test_empty_agent_environment_is_none(self):
        self.assertEqual(self.probe({'BRIDGE_AGENT': ''})[1], 'None')


class ErrorMappingTest(ClientCase):
    def raises(self, exc, error, **kw):
        with patch.object(client.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(exc) as cm:
                client._post('/x', {}, **kw)
        return cm.exception

    def test_409_is_claim_refused_with_daemon_message(self):
        e = self.raises(client.ClaimRefused, http_error(409, {'error': 'Claim refused: familysearch is held by a'}))
        self.assertEqual(str(e), 'Claim refused: familysearch is held by a')

    def test_claim_refused_is_a_runtime_error(self):
        self.assertTrue(issubclass(client.ClaimRefused, RuntimeError))

    def test_500_is_runtime_error_with_code_and_message(self):
        e = self.raises(RuntimeError, http_error(500, {'error': 'boom'}))
        self.assertNotIsInstance(e, client.ClaimRefused)
        self.assertEqual(str(e), 'Browser bridge HTTP 500: boom')

    def test_non_json_error_body_is_kept(self):
        e = self.raises(RuntimeError, http_error(502, b'<html>bad gateway</html>'))
        self.assertIn('502', str(e))
        self.assertIn('bad gateway', str(e))

    def test_json_body_without_error_key_is_kept_whole(self):
        e = self.raises(RuntimeError, http_error(500, {'detail': 'x'}))
        self.assertIn('detail', str(e))

    def test_json_list_body_does_not_crash_the_mapper(self):
        e = self.raises(RuntimeError, http_error(500, [1, 2]))
        self.assertIn('500', str(e))

    def test_409_with_plain_text_body(self):
        e = self.raises(client.ClaimRefused, http_error(409, b'tab 3 belongs to b'))
        self.assertEqual(str(e), 'tab 3 belongs to b')

    def test_invalid_utf8_error_body_is_decoded_with_replacement(self):
        e = self.raises(RuntimeError, http_error(500, b'bad \xff byte'))
        self.assertIn('bad', str(e))

    def test_error_key_in_a_200_reply_raises(self):
        with patch.object(client.urllib.request, 'urlopen', return_value=FakeResponse({'error': 'nope'})):
            with self.assertRaisesRegex(RuntimeError, 'nope'):
                client._post('/x', {})

    def test_timeout_propagates(self):
        self.raises(TimeoutError, TimeoutError('timed out'), timeout=1)

    def test_connection_refused_propagates_as_urlerror(self):
        self.raises(urllib.error.URLError, urllib.error.URLError('refused'))

    def test_http_error_is_chained(self):
        e = self.raises(client.ClaimRefused, http_error(409, {'error': 'x'}))
        self.assertIsInstance(e.__cause__, urllib.error.HTTPError)


class IsRunningTest(ClientCase):
    def test_true_when_daemon_says_ok(self):
        result, urlopen = self.call(client.is_running, {'status': 'ok', 'version': 2})
        self.assertTrue(result)
        self.assertEqual(urlopen.call_args.args[0], 'http://127.0.0.1:9999/ping')
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 2)

    def test_false_when_status_is_not_ok(self):
        self.assertFalse(self.call(client.is_running, {'status': 'busy'})[0])

    def test_false_when_daemon_is_down(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=urllib.error.URLError('refused')):
            self.assertFalse(client.is_running())

    def test_false_on_timeout(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=TimeoutError()):
            self.assertFalse(client.is_running())

    def test_false_on_garbage_reply(self):
        self.assertFalse(self.call(client.is_running, b'not json')[0])

    def test_false_on_http_error(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=http_error(500, {'error': 'x'})):
            self.assertFalse(client.is_running())


class ClaimFunctionsTest(ClientCase):
    def test_claim_returns_page_id(self):
        client.set_agent('a')
        page, urlopen = self.call(client.claim, {'status': 'claimed', 'pageId': 12}, 'familysearch')
        req, body = self.sent(urlopen)
        self.assertEqual(page, 12)
        self.assertTrue(req.full_url.endswith('/claim'))
        self.assertEqual(body, {'site': 'familysearch', 'background': True, 'agent': 'a'})
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 120)

    def test_claim_with_url_and_foreground(self):
        _, urlopen = self.call(client.claim, {'pageId': 1}, 'ancestry', url='https://www.ancestry.com/x', background=False)
        body = self.sent(urlopen)[1]
        self.assertEqual(body['url'], 'https://www.ancestry.com/x')
        self.assertFalse(body['background'])

    def test_claim_refused_surfaces(self):
        with patch.object(client.urllib.request, 'urlopen', side_effect=http_error(409, {'error': 'held'})):
            with self.assertRaises(client.ClaimRefused):
                client.claim('familysearch')

    def test_open_tab(self):
        page, urlopen = self.call(client.open_tab, {'pageId': 4}, 'https://x.org/', background=False)
        self.assertEqual(page, 4)
        self.assertEqual(self.sent(urlopen)[1], {'url': 'https://x.org/', 'background': False})

    def test_release_all_closes_tabs_by_default(self):
        _, urlopen = self.call(client.release, {'status': 'released'})
        req, body = self.sent(urlopen)
        self.assertTrue(req.full_url.endswith('/release'))
        self.assertEqual(body, {'closeTabs': True})

    def test_release_one_site_without_closing(self):
        _, urlopen = self.call(client.release, {'status': 'released'}, 'ancestry', close_tabs=False)
        self.assertEqual(self.sent(urlopen)[1], {'closeTabs': False, 'site': 'ancestry'})

    def test_claims_returns_snapshot(self):
        snap = {'sites': {'familysearch': {'agent': 'a'}}}
        self.assertEqual(self.call(client.claims, snap)[0], snap)


PAGES = [
    {'id': 1, 'title': 'Home', 'url': 'https://www.familysearch.org/', 'owner': 'a'},
    {'id': 2, 'title': 'Search', 'url': 'https://www.ancestry.com/search', 'owner': 'b'},
    {'id': 3, 'title': 'Free', 'url': 'https://example.com/', 'owner': None},
]


class PagesTest(ClientCase):
    def test_get_pages_all(self):
        pages, urlopen = self.call(client.get_pages, {'pages': PAGES})
        self.assertEqual(pages, PAGES)
        self.assertEqual(urlopen.call_args.args[0].full_url, 'http://127.0.0.1:9999/pages')

    def test_get_pages_mine_adds_quoted_agent(self):
        client.set_agent('claude sub/1')
        _, urlopen = self.call(client.get_pages, {'pages': []}, mine=True)
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith('/pages?agent=claude%20sub/1'))

    def test_get_pages_mine_without_agent_has_no_filter(self):
        _, urlopen = self.call(client.get_pages, {'pages': []}, mine=True)
        self.assertTrue(urlopen.call_args.args[0].full_url.endswith('/pages'))

    def test_find_page_matches_url_or_title_case_insensitively_among_own_tabs(self):
        client.set_agent('a')
        with patch.object(client, 'get_pages', return_value=PAGES):
            self.assertEqual(client.find_page('FAMILYSEARCH'), 1)
            self.assertEqual(client.find_page('home'), 1)

    def test_find_page_ignores_other_agents_tabs(self):
        client.set_agent('a')
        with patch.object(client, 'get_pages', return_value=PAGES):
            with self.assertRaisesRegex(ValueError, "No tab matching 'ancestry' owned by a"):
                client.find_page('ancestry')

    def test_find_page_without_agent_sees_only_unowned_tabs(self):
        with patch.object(client, 'get_pages', return_value=PAGES):
            self.assertEqual(client.find_page('example'), 3)
            with self.assertRaises(ValueError) as cm:
                client.find_page('familysearch')
        self.assertNotIn('owned by', str(cm.exception))

    def test_page_id_passes_ints_and_resolves_strings(self):
        self.assertEqual(client._page_id(5), 5)
        with patch.object(client, 'find_page', return_value=9) as find:
            self.assertEqual(client._page_id('ancestry'), 9)
        find.assert_called_once_with('ancestry')


class PageActionsTest(ClientCase):
    def test_evaluate_returns_result_and_pads_timeout(self):
        result, urlopen = self.call(client.evaluate, {'result': {'a': 1}}, 3, '() => 1', timeout=10)
        req, body = self.sent(urlopen)
        self.assertEqual(result, {'a': 1})
        self.assertTrue(req.full_url.endswith('/evaluate'))
        self.assertEqual(body, {'pageId': 3, 'function': '() => 1', 'timeout': 10})
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 85)

    def test_evaluate_default_timeout(self):
        _, urlopen = self.call(client.evaluate, {'result': None}, 3, '() => 1')
        self.assertEqual(self.sent(urlopen)[1]['timeout'], 20)
        self.assertEqual(urlopen.call_args.kwargs['timeout'], 95)

    def test_evaluate_resolves_page_name(self):
        with patch.object(client, 'find_page', return_value=8):
            _, urlopen = self.call(client.evaluate, {'result': 1}, 'ancestry', '() => 1')
        self.assertEqual(self.sent(urlopen)[1]['pageId'], 8)

    def test_navigate_waits(self):
        with patch.object(client.time, 'sleep') as sleep:
            res, urlopen = self.call(client.navigate, {'status': 'navigated'}, 3, 'https://x.org/', wait_sec=4)
        sleep.assert_called_once_with(4)
        self.assertEqual(self.sent(urlopen)[1], {'pageId': 3, 'url': 'https://x.org/'})
        self.assertEqual(res['status'], 'navigated')

    def test_navigate_with_zero_wait_does_not_sleep(self):
        with patch.object(client.time, 'sleep') as sleep:
            self.call(client.navigate, {'status': 'navigated'}, 3, 'https://x.org/', wait_sec=0)
        sleep.assert_not_called()

    def test_screenshot(self):
        _, urlopen = self.call(client.screenshot, {'status': 'screenshot_taken'}, 3, '/tmp/x.png', full_page=True)
        req, body = self.sent(urlopen)
        self.assertTrue(req.full_url.endswith('/screenshot'))
        self.assertEqual(body, {'pageId': 3, 'filePath': '/tmp/x.png', 'fullPage': True})

    def test_download_fs_stringifies_image_number_and_defaults_to_familysearch_tab(self):
        with patch.object(client, 'find_page', return_value=6) as find:
            _, urlopen = self.call(client.download_fs, {'status': 'saved'}, 39, 'a.jpg')
        find.assert_called_once_with('familysearch')
        req, body = self.sent(urlopen)
        self.assertTrue(req.full_url.endswith('/download_fs'))
        self.assertEqual(body, {'pageId': 6, 'imageNumber': '39', 'destFilename': 'a.jpg'})

    def test_download_file(self):
        _, urlopen = self.call(client.download_file, {'status': 'saved'}, 2, 'https://x/y.jpg', '/tmp/y.jpg')
        req, body = self.sent(urlopen)
        self.assertTrue(req.full_url.endswith('/download_file'))
        self.assertEqual(body, {'pageId': 2, 'url': 'https://x/y.jpg', 'destPath': '/tmp/y.jpg'})


if __name__ == '__main__':
    unittest.main()
