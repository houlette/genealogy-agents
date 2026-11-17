"""Tests for daemon.py's HTTP handlers and helpers (no Chrome, no MCP process).

BridgeHandler runs in a ThreadingHTTPServer on 127.0.0.1, port 0, with
call_tool, list_pages and evaluate_script patched. Downloads go to temp folders.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import client  # noqa: E402
import daemon  # noqa: E402
from claims import Claims  # noqa: E402

# As in test_daemon.py: the tests use 'Claude' as the profile name.
daemon.MARKER_VALUE = 'Claude'
daemon.PROFILE = 'Claude'

FS = 'https://www.familysearch.org/ark:/61903/3:1:ABC'
ANC = 'https://www.ancestry.com/search/'
JPEG = b'\xff\xd8\xff\xe0' + b'j' * 500


def page(pid, url, title='T'):
    return {'id': pid, 'title': title, 'url': url}


class HttpCase(unittest.TestCase):
    """Starts a real handler on a free local port; each test gets fresh claims."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.claims = Claims(None)
        self.pages = []
        patches = [
            patch.object(daemon, 'CLAIMS', self.claims),
            patch.object(daemon, 'list_pages', side_effect=lambda: [dict(p) for p in self.pages]),
            patch.object(daemon, 'log'),
            patch.object(daemon, 'PROFILE_CHECK', False),
            patch.object(daemon, 'BACKOFF_UNTIL', 0.0),
            patch.object(daemon, 'CHROME_STATE', 'not yet connected'),
            patch.object(daemon, 'SOURCES', os.path.join(self.tmp.name, 'sources')),
            patch.object(daemon, 'DOWNLOADS', os.path.join(self.tmp.name, 'downloads')),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(daemon.SOURCES)
        os.makedirs(daemon.DOWNLOADS)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), daemon.BridgeHandler)
        self.server.daemon_threads = True
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read()), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read()), e.headers

    def get(self, path):
        return self.request(path)

    def post(self, path, payload):
        return self.request(path, payload)


class GetEndpointsTest(HttpCase):
    def test_ping(self):
        code, body, headers = self.get('/ping')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['version'], 2)
        self.assertEqual(body['chrome'], 'not yet connected')
        self.assertEqual(body['retry_in_s'], 0)
        self.assertFalse(body['keeping_mac_awake'])

    def test_ping_reports_backoff_countdown(self):
        with patch.object(daemon, 'BACKOFF_UNTIL', time.time() + 60):
            body = self.get('/ping')[1]
        self.assertTrue(55 <= body['retry_in_s'] <= 60)

    def test_ping_reports_caffeinate_running(self):
        with patch.object(daemon, 'CAFFEINATE', Mock(poll=Mock(return_value=None))):
            self.assertTrue(self.get('/ping')[1]['keeping_mac_awake'])
        with patch.object(daemon, 'CAFFEINATE', Mock(poll=Mock(return_value=0))):
            self.assertFalse(self.get('/ping')[1]['keeping_mac_awake'])

    def test_ping_works_with_chrome_down(self):
        with patch.object(daemon, 'list_pages', side_effect=RuntimeError('down')):
            self.assertEqual(self.get('/ping')[0], 200)

    def test_pages_adds_owner_site_and_holder(self):
        self.pages = [page(1, FS), page(2, ANC), page(3, 'about:blank')]
        self.claims.own_page('a', 1)
        self.claims.claim_site('a', 'familysearch')
        code, body, _ = self.get('/pages')
        self.assertEqual(code, 200)
        by_id = {p['id']: p for p in body['pages']}
        self.assertEqual((by_id[1]['owner'], by_id[1]['site'], by_id[1]['site_holder']), ('a', 'familysearch', 'a'))
        self.assertEqual((by_id[2]['owner'], by_id[2]['site'], by_id[2]['site_holder']), (None, 'ancestry', None))
        self.assertEqual((by_id[3]['owner'], by_id[3]['site'], by_id[3]['site_holder']), (None, None, None))

    def test_pages_filtered_by_agent(self):
        self.pages = [page(1, FS), page(2, ANC)]
        self.claims.own_page('a', 1)
        self.claims.own_page('b', 2)
        self.assertEqual([p['id'] for p in self.get('/pages?agent=a')[1]['pages']], [1])
        self.assertEqual(self.get('/pages?agent=nobody')[1]['pages'], [])

    def test_pages_marks_spare_tabs(self):
        self.pages = [page(1, daemon.MARKER_URL)]
        self.claims.pool[1] = 1.0
        self.assertEqual(self.get('/pages')[1]['pages'][0]['owner'], '(bridge spare tab)')

    def test_pages_error_is_500_with_message(self):
        with patch.object(daemon, 'list_pages', side_effect=RuntimeError('chrome gone')):
            code, body, _ = self.get('/pages')
        self.assertEqual((code, body), (500, {'error': 'chrome gone'}))

    def test_claims_snapshot(self):
        self.claims.claim_site('a', 'familysearch')
        self.claims.own_page('a', 4)
        body = self.get('/claims')[1]
        self.assertEqual(body['sites']['familysearch']['agent'], 'a')
        self.assertEqual(body['pages']['4']['agent'], 'a')
        self.assertEqual(body['spare_tabs'], [])
        self.assertIn('ttl_seconds', body)

    def test_unknown_get_is_404(self):
        code, body, _ = self.get('/nope')
        self.assertEqual((code, body), (404, {'error': 'Not found'}))

    def test_unknown_post_is_404(self):
        code, body, _ = self.post('/nope', {})
        self.assertEqual((code, body), (404, {'error': 'Not found'}))

    def test_content_length_matches_body(self):
        req = urllib.request.Request(self.base + '/claims')
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            self.assertEqual(int(r.headers['Content-Length']), len(raw))


class ClaimEndpointTest(HttpCase):
    def setUp(self):
        super().setUp()
        opened = patch.object(daemon, 'open_page', return_value=11)
        self.open_page = opened.start()
        self.addCleanup(opened.stop)

    def test_claim_opens_home_url_for_a_site_name(self):
        code, body, _ = self.post('/claim', {'agent': 'a', 'site': 'familysearch'})
        self.assertEqual((code, body), (200, {'status': 'claimed', 'site': 'familysearch', 'pageId': 11}))
        self.open_page.assert_called_once_with('a', 'https://www.familysearch.org/', True)
        self.assertEqual(self.claims.holder('familysearch'), 'a')

    def test_claim_with_url_and_background_flag(self):
        self.post('/claim', {'agent': 'a', 'site': 'ancestry', 'url': ANC, 'background': False})
        self.open_page.assert_called_once_with('a', ANC, False)

    def test_claim_from_url_alone(self):
        body = self.post('/claim', {'agent': 'a', 'url': FS})[1]
        self.assertEqual(body['site'], 'familysearch')

    def test_claim_unknown_site_uses_https_domain(self):
        self.post('/claim', {'agent': 'a', 'site': 'example.org'})
        self.assertEqual(self.open_page.call_args.args[1], 'https://example.org/')

    def test_claim_needs_agent(self):
        code, body, _ = self.post('/claim', {'site': 'familysearch'})
        self.assertEqual(code, 409)
        self.assertIn('needs an agent name', body['error'])
        self.open_page.assert_not_called()

    def test_claim_refused_when_another_agent_holds_site(self):
        self.claims.claim_site('b', 'familysearch')
        code, body, _ = self.post('/claim', {'agent': 'a', 'site': 'familysearch'})
        self.assertEqual(code, 409)
        self.assertTrue(body['error'].startswith('Claim refused:'))
        self.assertIn('held by b', body['error'])
        self.open_page.assert_not_called()

    def test_claim_by_group_member_domain_is_refused_too(self):
        self.claims.claim_site('b', 'ancestry')
        code, _, _ = self.post('/claim', {'agent': 'a', 'url': 'https://www.fold3.com/x'})
        self.assertEqual(code, 409)

    def test_claim_url_on_a_different_site_is_rejected(self):
        code, body, _ = self.post('/claim', {'agent': 'a', 'site': 'familysearch', 'url': ANC})
        self.assertEqual(code, 500)
        self.assertIn('is not on site familysearch', body['error'])
        self.assertIsNone(self.claims.holder('familysearch'))

    def test_claim_without_site_or_url_is_rejected(self):
        code, body, _ = self.post('/claim', {'agent': 'a'})
        self.assertEqual(code, 500)
        self.assertIn('give a site', body['error'])

    def test_failed_open_releases_a_new_claim(self):
        self.open_page.side_effect = daemon.ChromeNotConnected('Chrome is not connected')
        code, body, _ = self.post('/claim', {'agent': 'a', 'site': 'familysearch'})
        self.assertEqual(code, 500)
        self.assertIn('Chrome is not connected', body['error'])
        self.assertIsNone(self.claims.holder('familysearch'))

    def test_failed_open_keeps_a_claim_the_agent_already_held(self):
        self.claims.claim_site('a', 'familysearch')
        self.open_page.side_effect = RuntimeError('boom')
        self.post('/claim', {'agent': 'a', 'site': 'familysearch'})
        self.assertEqual(self.claims.holder('familysearch'), 'a')

    def test_wrong_profile_error_is_500_not_409(self):
        self.open_page.side_effect = daemon.WrongProfile('marker missing')
        self.assertEqual(self.post('/claim', {'agent': 'a', 'site': 'familysearch'})[0], 500)

    def test_open_claims_free_site_and_opens(self):
        code, body, _ = self.post('/open', {'agent': 'a', 'url': ANC})
        self.assertEqual((code, body), (200, {'status': 'opened', 'site': 'ancestry', 'pageId': 11}))
        self.assertEqual(self.claims.holder('ancestry'), 'a')

    def test_open_refused_on_held_site(self):
        self.claims.claim_site('b', 'ancestry')
        self.assertEqual(self.post('/open', {'agent': 'a', 'url': ANC})[0], 409)

    def test_open_needs_agent(self):
        self.assertEqual(self.post('/open', {'url': ANC})[0], 409)

    def test_open_unclaimable_url_skips_claim(self):
        body = self.post('/open', {'agent': 'a', 'url': 'about:blank'})[1]
        self.assertIsNone(body['site'])
        self.assertEqual(self.claims.snapshot()['sites'], {})

    def test_malformed_json_is_answered_not_dropped(self):
        req = urllib.request.Request(self.base + '/claim', data=b'{not json', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn('Bad request', json.loads(cm.exception.read())['error'])

    def test_non_object_json_body_is_400(self):
        req = urllib.request.Request(self.base + '/claim', data=b'[1, 2]', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)


class ReleaseEndpointTest(HttpCase):
    def setUp(self):
        super().setUp()
        closer = patch.object(daemon, 'call_tool', return_value={})
        self.call_tool = closer.start()
        self.addCleanup(closer.stop)

    def setup_agent(self):
        self.pages = [page(1, FS), page(2, ANC), page(3, FS)]
        self.claims.claim_site('a', 'familysearch')
        self.claims.claim_site('a', 'ancestry')
        for pid in (1, 2):
            self.claims.own_page('a', pid)
        self.claims.own_page('b', 3)

    def test_release_all_closes_agents_tabs_only(self):
        self.setup_agent()
        code, body, _ = self.post('/release', {'agent': 'a'})
        self.assertEqual(code, 200)
        self.assertEqual(sorted(body['sites']), ['ancestry', 'familysearch'])
        self.assertEqual(sorted(body['closedTabs']), [1, 2])
        self.assertEqual(body['spareTabs'], [])
        closed = sorted(c.args[1]['pageId'] for c in self.call_tool.call_args_list if c.args[0] == 'close_page')
        self.assertEqual(closed, [1, 2])
        self.assertEqual(self.claims.snapshot()['sites'], {})
        self.assertEqual(list(self.claims.snapshot()['pages']), ['3'])

    def test_release_one_site_closes_only_tabs_on_it(self):
        self.setup_agent()
        body = self.post('/release', {'agent': 'a', 'site': 'ancestry'})[1]
        self.assertEqual(body['sites'], ['ancestry'])
        self.assertEqual(body['closedTabs'], [2])
        self.assertEqual(self.claims.holder('familysearch'), 'a')
        self.assertIn('1', self.claims.snapshot()['pages'])

    def test_release_accepts_domain_spelling(self):
        self.setup_agent()
        body = self.post('/release', {'agent': 'a', 'site': 'www.ancestry.com'})[1]
        self.assertEqual(body['sites'], ['ancestry'])

    def test_release_without_closing_tabs(self):
        self.setup_agent()
        body = self.post('/release', {'agent': 'a', 'closeTabs': False})[1]
        self.assertEqual(body['closedTabs'], [])
        self.call_tool.assert_not_called()

    def test_close_failure_is_ignored(self):
        self.setup_agent()
        self.call_tool.side_effect = RuntimeError('already closed')
        code, body, _ = self.post('/release', {'agent': 'a'})
        self.assertEqual(code, 200)
        self.assertEqual(body['closedTabs'], [])

    def test_release_site_survives_list_pages_failure(self):
        self.setup_agent()
        with patch.object(daemon, 'list_pages', side_effect=RuntimeError('chrome down')):
            code, body, _ = self.post('/release', {'agent': 'a', 'site': 'ancestry'})
        self.assertEqual(code, 200)
        self.assertEqual(body['sites'], ['ancestry'])
        self.assertEqual(body['closedTabs'], [])

    def test_release_needs_agent(self):
        self.assertEqual(self.post('/release', {})[0], 409)

    def test_release_by_agent_with_nothing_is_ok(self):
        body = self.post('/release', {'agent': 'ghost'})[1]
        self.assertEqual((body['sites'], body['closedTabs']), ([], []))

    def test_release_keeps_tab_as_spare_when_pool_has_room(self):
        self.setup_agent()
        with patch.object(daemon, 'make_spare', side_effect=lambda agent, pid: self.claims.pool_put(agent, pid, 3)):
            body = self.post('/release', {'agent': 'a'})[1]
        self.assertEqual(sorted(body['spareTabs']), [1, 2])
        self.assertEqual(body['closedTabs'], [])
        self.assertEqual(self.claims.snapshot()['spare_tabs'], [1, 2])


class PageEndpointsTest(HttpCase):
    def setUp(self):
        super().setUp()
        self.pages = [page(1, FS), page(2, ANC)]
        self.claims.own_page('a', 1)
        self.claims.claim_site('a', 'familysearch')
        self.claims.own_page('b', 2)
        self.claims.claim_site('b', 'ancestry')

    def test_evaluate_returns_result(self):
        with patch.object(daemon, 'evaluate_script', return_value={'x': 1}) as ev:
            code, body, _ = self.post('/evaluate', {'agent': 'a', 'pageId': 1, 'function': '() => 1', 'timeout': 9})
        self.assertEqual((code, body), (200, {'result': {'x': 1}}))
        ev.assert_called_once_with(1, '() => 1', timeout=9)

    def test_evaluate_refused_on_another_agents_tab(self):
        with patch.object(daemon, 'evaluate_script') as ev:
            code, body, _ = self.post('/evaluate', {'agent': 'a', 'pageId': 2, 'function': '() => 1'})
        self.assertEqual(code, 409)
        self.assertIn('belongs to b', body['error'])
        ev.assert_not_called()

    def test_evaluate_refused_for_nameless_caller_on_owned_tab(self):
        with patch.object(daemon, 'evaluate_script'):
            self.assertEqual(self.post('/evaluate', {'pageId': 1, 'function': '() => 1'})[0], 409)

    def test_evaluate_needs_page_id(self):
        code, body, _ = self.post('/evaluate', {'agent': 'a', 'function': '() => 1'})
        self.assertEqual(code, 500)
        self.assertIn('pageId', body['error'])

    def test_evaluate_finds_page_by_name_among_own_tabs(self):
        with patch.object(daemon, 'evaluate_script', return_value=True) as ev:
            code, _, _ = self.post('/evaluate', {'agent': 'a', 'page': 'familysearch', 'function': '() => 1'})
        self.assertEqual(code, 200)
        self.assertEqual(ev.call_args.args[0], 1)

    def test_evaluate_name_lookup_does_not_find_other_agents_tab(self):
        code, body, _ = self.post('/evaluate', {'agent': 'a', 'page': 'ancestry', 'function': '() => 1'})
        self.assertEqual(code, 500)
        self.assertIn("No tab matching 'ancestry' owned by a", body['error'])

    def test_evaluate_unknown_page_id(self):
        code, body, _ = self.post('/evaluate', {'agent': 'a', 'pageId': 99, 'function': '() => 1'})
        self.assertEqual(code, 500)
        self.assertIn('No open tab with pageId 99', body['error'])

    def test_evaluate_string_page_id_is_converted(self):
        with patch.object(daemon, 'evaluate_script', return_value=1) as ev:
            self.post('/evaluate', {'agent': 'a', 'pageId': '1', 'function': '() => 1'})
        self.assertEqual(ev.call_args.args[0], 1)

    def test_evaluate_adopts_an_unowned_tab(self):
        self.pages.append(page(5, 'https://example.com/'))
        with patch.object(daemon, 'evaluate_script', return_value=1):
            self.post('/evaluate', {'agent': 'a', 'pageId': 5, 'function': '() => 1'})
        self.assertEqual(self.claims.page_owner(5), 'a')

    def test_evaluate_refused_on_spare_tab(self):
        self.pages.append(page(6, daemon.MARKER_URL))
        self.claims.pool[6] = 1.0
        code, body, _ = self.post('/evaluate', {'agent': 'a', 'pageId': 6, 'function': '() => 1'})
        self.assertEqual(code, 409)
        self.assertIn('spare tab', body['error'])

    def test_navigate_checks_target_site(self):
        with patch.object(daemon, 'evaluate_script', return_value=True) as ev:
            code, body, _ = self.post('/navigate', {'agent': 'a', 'pageId': 1, 'url': 'https://www.familysearch.org/x'})
        self.assertEqual((code, body['status']), (200, 'navigated'))
        self.assertIn('window.location.href = "https://www.familysearch.org/x"', ev.call_args.args[1])

    def test_navigate_to_site_held_by_another_agent_is_refused(self):
        with patch.object(daemon, 'evaluate_script') as ev:
            code, body, _ = self.post('/navigate', {'agent': 'a', 'pageId': 1, 'url': ANC})
        self.assertEqual(code, 409)
        self.assertIn('ancestry is held by b', body['error'])
        ev.assert_not_called()

    def test_navigate_url_is_json_escaped(self):
        with patch.object(daemon, 'evaluate_script', return_value=True) as ev:
            self.post('/navigate', {'agent': 'a', 'pageId': 1, 'url': 'https://www.familysearch.org/"; alert(1); "'})
        self.assertIn('\\"; alert(1); \\"', ev.call_args.args[1])

    def test_screenshot_passes_options(self):
        with patch.object(daemon, 'call_tool', return_value={'ok': 1}) as ct:
            code, body, _ = self.post('/screenshot', {'agent': 'a', 'pageId': 1, 'filePath': '/t/x.png', 'fullPage': True})
        self.assertEqual((code, body['status']), (200, 'screenshot_taken'))
        ct.assert_called_once_with('take_screenshot', {'pageId': 1, 'filePath': '/t/x.png', 'fullPage': True})

    def test_screenshot_refused_on_other_agents_tab(self):
        with patch.object(daemon, 'call_tool') as ct:
            self.assertEqual(self.post('/screenshot', {'agent': 'a', 'pageId': 2})[0], 409)
        ct.assert_not_called()


class CallToolEndpointTest(HttpCase):
    def setUp(self):
        super().setUp()
        self.pages = [page(1, FS), page(2, ANC)]
        self.claims.own_page('a', 1)
        self.claims.own_page('b', 2)
        patcher = patch.object(daemon, 'call_tool', return_value={'content': []})
        self.call_tool = patcher.start()
        self.addCleanup(patcher.stop)

    def test_new_page_and_select_page_are_blocked(self):
        for name in ('new_page', 'select_page'):
            with self.subTest(name=name):
                code, body, _ = self.post('/call_tool', {'agent': 'a', 'name': name, 'args': {}})
                self.assertEqual(code, 409)
                self.assertIn('use /claim or /open', body['error'])
        self.call_tool.assert_not_called()

    def test_tool_without_page_id_is_refused(self):
        code, body, _ = self.post('/call_tool', {'agent': 'a', 'name': 'take_snapshot'})
        self.assertEqual(code, 409)
        self.assertIn('needs a pageId', body['error'])

    def test_list_pages_needs_no_page_id(self):
        code, body, _ = self.post('/call_tool', {'agent': 'a', 'name': 'list_pages', 'timeout': 12})
        self.assertEqual((code, body), (200, {'result': {'content': []}}))
        self.call_tool.assert_called_once_with('list_pages', {}, timeout=12)

    def test_page_id_is_checked_and_timeout_defaults_to_30(self):
        code, _, _ = self.post('/call_tool', {'agent': 'a', 'name': 'take_snapshot', 'args': {'pageId': 1}})
        self.assertEqual(code, 200)
        self.call_tool.assert_called_once_with('take_snapshot', {'pageId': 1}, timeout=30)

    def test_other_agents_tab_is_refused(self):
        code, _, _ = self.post('/call_tool', {'agent': 'a', 'name': 'take_snapshot', 'args': {'pageId': 2}})
        self.assertEqual(code, 409)
        self.call_tool.assert_not_called()

    def test_close_page_forgets_the_tab(self):
        self.post('/call_tool', {'agent': 'a', 'name': 'close_page', 'args': {'pageId': 1}})
        self.assertIsNone(self.claims.page_owner(1))

    def test_navigate_through_call_tool_checks_target_url(self):
        self.claims.claim_site('b', 'ancestry')
        code, _, _ = self.post('/call_tool', {'agent': 'a', 'name': 'navigate_page',
                                              'args': {'pageId': 1, 'url': ANC}})
        self.assertEqual(code, 409)


class ChromeBackoffTest(HttpCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(daemon, 'OWNER', 'Pat Example')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_message_names_the_owner_and_says_it_is_not_a_result(self):
        with patch.object(daemon, 'BACKOFF_UNTIL', time.time() + 90):
            with self.assertRaises(daemon.ChromeNotConnected) as cm:
                daemon.call_tool('list_pages', {})
        message = str(cm.exception)
        self.assertIn('Chrome is not connected', message)
        self.assertIn('Ask Pat Example to click Allow', message)
        self.assertRegex(message, r'try once more in (89|90) s')
        self.assertIn('failed retrieval, not a search result', message)

    def test_backoff_blocks_without_calling_chrome(self):
        with patch.object(daemon, 'BACKOFF_UNTIL', time.time() + 90), \
                patch.object(daemon, '_call_tool') as inner:
            with self.assertRaises(daemon.ChromeNotConnected):
                daemon.call_tool('take_snapshot', {})
        inner.assert_not_called()

    def test_http_reply_carries_the_message(self):
        self.pages = None  # list_pages is not reached
        with patch.object(daemon, 'list_pages', wraps=lambda: daemon.call_tool('list_pages', {}, timeout=65)), \
                patch.object(daemon, 'BACKOFF_UNTIL', time.time() + 90):
            code, body, _ = self.get('/pages')
        self.assertEqual(code, 500)
        self.assertIn('Ask Pat Example to click Allow', body['error'])

    def test_expired_backoff_allows_calls_and_marks_connected(self):
        with patch.object(daemon, 'BACKOFF_UNTIL', time.time() - 1), \
                patch.object(daemon, '_call_tool', return_value={'ok': 1}):
            self.assertEqual(daemon.call_tool('list_pages', {}), {'ok': 1})
        self.assertEqual(daemon.CHROME_STATE, 'connected')

    def test_timeout_on_list_pages_starts_backoff(self):
        with patch.object(daemon, 'CONNECT_BACKOFF', 50), \
                patch.object(daemon, '_call_tool', side_effect=TimeoutError('slow')):
            with self.assertRaises(TimeoutError):
                daemon.call_tool('list_pages', {})
            self.assertTrue(40 < daemon.BACKOFF_UNTIL - time.time() <= 50)
            self.assertIn('waiting for approval', daemon.CHROME_STATE)

    def test_timeout_on_other_tool_does_not_start_backoff(self):
        with patch.object(daemon, '_call_tool', side_effect=TimeoutError('slow')):
            with self.assertRaises(TimeoutError):
                daemon.call_tool('take_screenshot', {})
        self.assertEqual(daemon.BACKOFF_UNTIL, 0.0)

    def test_connection_failure_text_starts_backoff_for_any_tool(self):
        for text in daemon.CONNECT_FAILURE_MARKS:
            with self.subTest(text=text), patch.object(daemon, 'BACKOFF_UNTIL', 0.0), \
                    patch.object(daemon, '_call_tool', side_effect=RuntimeError('Tool failed: ' + text)):
                with self.assertRaises(RuntimeError):
                    daemon.call_tool('take_screenshot', {})
                self.assertGreater(daemon.BACKOFF_UNTIL, time.time())

    def test_ordinary_tool_error_does_not_start_backoff(self):
        with patch.object(daemon, '_call_tool', side_effect=RuntimeError('Tool failed: selector not found')):
            with self.assertRaises(RuntimeError):
                daemon.call_tool('list_pages', {})
        self.assertEqual(daemon.BACKOFF_UNTIL, 0.0)


class ConfigSubprocessTest(unittest.TestCase):
    """OWNER, PROFILE, PROFILE_CHECK and DOWNLOADS come from project.json at import."""

    def probe(self, project=None, env_extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items()
                   if k not in ('GENEALOGY_PROJECT', 'BRIDGE_PROFILE_CHECK')}
            env['GENEALOGY_PROJECT'] = tmp
            env.update(env_extra or {})
            if project is not None:
                with open(os.path.join(tmp, 'project.json'), 'w') as f:
                    json.dump(project, f)
            code = ('import sys, json; sys.path.insert(0, %r); import daemon; '
                    'print(json.dumps([daemon.OWNER, daemon.PROFILE, daemon.PROFILE_CHECK, '
                    'daemon.DOWNLOADS, daemon.MARKER_VALUE]))' % HERE)
            out = subprocess.run([sys.executable, '-c', code], env=env, cwd=tmp,
                                 capture_output=True, text=True, check=True).stdout
            return json.loads(out), tmp

    def test_no_project_json_means_no_profile_check(self):
        (owner, profile, check, downloads, marker), _ = self.probe()
        self.assertEqual(owner, 'the project owner')
        self.assertIsNone(profile)
        self.assertIs(check, False)
        self.assertEqual(marker, '')
        self.assertEqual(downloads, os.path.expanduser('~/Downloads'))

    def test_null_profile_keeps_check_off(self):
        self.assertIs(self.probe({'bridge': {'profile': None}})[0][2], False)

    def test_empty_profile_keeps_check_off(self):
        self.assertIs(self.probe({'bridge': {'profile': ''}})[0][2], False)

    def test_profile_turns_check_on(self):
        (_, profile, check, _, marker), _ = self.probe({'bridge': {'profile': 'Research'}})
        self.assertEqual((profile, check, marker), ('Research', True, 'Research'))

    def test_env_switch_turns_check_off_even_with_profile(self):
        result, _ = self.probe({'bridge': {'profile': 'Research'}}, {'BRIDGE_PROFILE_CHECK': '0'})
        self.assertIs(result[2], False)

    def test_owner_and_downloads_from_project_json(self):
        result, tmp = self.probe({'owner': 'Pat Example', 'bridge': {'downloads': '/some/where'}})
        self.assertEqual((result[0], result[3]), ('Pat Example', '/some/where'))


class OpenPageWithoutProfileTest(unittest.TestCase):
    def test_profile_check_off_loads_target_directly_and_skips_marker(self):
        claims = Mock()
        new_page = {'content': [{'type': 'text', 'text': '4: Example (https://x.org/) [selected]'}]}
        with patch.object(daemon, 'PROFILE_CHECK', False), patch.object(daemon, 'CLAIMS', claims), \
                patch.object(daemon, 'list_pages', return_value=[]), \
                patch.object(daemon, 'call_tool', return_value=new_page) as call, \
                patch.object(daemon, 'evaluate_script') as evaluate:
            self.assertEqual(daemon.open_page('a', 'https://x.org/'), 4)
        self.assertEqual(call.call_args.args[1]['url'], 'https://x.org/')
        evaluate.assert_not_called()
        claims.own_page.assert_called_once_with('a', 4)

    def test_failed_open_closes_new_unowned_tab_on_same_site(self):
        claims = Mock()
        claims.page_owner.return_value = None
        after = [page(1, 'https://other.org/'), page(2, 'https://www.familysearch.org/leftover'),
                 page(3, 'https://example.com/')]
        listings = [[page(1, 'https://other.org/')], after]
        with patch.object(daemon, 'PROFILE_CHECK', False), patch.object(daemon, 'CLAIMS', claims), \
                patch.object(daemon, 'list_pages', side_effect=listings), \
                patch.object(daemon, 'log'), \
                patch.object(daemon, 'call_tool', side_effect=[RuntimeError('load timeout'), {}]) as call:
            with self.assertRaisesRegex(RuntimeError, 'load timeout'):
                daemon.open_page('a', FS)
        self.assertEqual(call.call_args_list[-1].args, ('close_page', {'pageId': 2}))
        claims.own_page.assert_not_called()

    def test_new_page_response_without_one_selected_tab_is_an_error(self):
        text = '4: A (https://a.org/)\n5: B (https://b.org/)'
        with patch.object(daemon, 'PROFILE_CHECK', False), patch.object(daemon, 'CLAIMS', Mock()), \
                patch.object(daemon, 'list_pages', return_value=[]), \
                patch.object(daemon, 'call_tool', return_value={'content': [{'text': text}]}):
            with self.assertRaisesRegex(RuntimeError, 'Could not identify the new tab'):
                daemon.open_page('a', 'https://x.org/')


class HelperFunctionsTest(unittest.TestCase):
    def test_parse_pages(self):
        text = ('## Pages\n'
                '1: Home (https://www.familysearch.org/)\n'
                '22: Title (with parens) (https://a.org/x?y=1) [selected]\n'
                'x: not a page (https://nope)\n'
                '3: no url here\n')
        self.assertEqual(daemon.parse_pages(text), [
            {'id': 1, 'title': 'Home', 'url': 'https://www.familysearch.org/', 'selected': False},
            {'id': 22, 'title': 'Title (with parens)', 'url': 'https://a.org/x?y=1', 'selected': True},
        ])

    def test_parse_pages_url_with_parentheses(self):
        text = ('4: Foo (bar) - Wikipedia (https://en.wikipedia.org/wiki/Foo_(bar))\n'
                '5:  (about:blank)\n')
        self.assertEqual(daemon.parse_pages(text), [
            {'id': 4, 'title': 'Foo (bar) - Wikipedia', 'url': 'https://en.wikipedia.org/wiki/Foo_(bar)',
             'selected': False},
            {'id': 5, 'title': '', 'url': 'about:blank', 'selected': False},
        ])

    def test_parse_pages_empty(self):
        self.assertEqual(daemon.parse_pages(''), [])

    def test_list_pages_strips_selected_flag(self):
        res = {'content': [{'text': '5: A (https://a.org/) [selected]'}]}
        with patch.object(daemon, 'call_tool', return_value=res) as call:
            self.assertEqual(daemon.list_pages(), [{'id': 5, 'title': 'A', 'url': 'https://a.org/'}])
        self.assertEqual(call.call_args.kwargs['timeout'], 65)

    def test_page_url(self):
        with patch.object(daemon, 'list_pages', return_value=[page(1, 'https://a.org/')]):
            self.assertEqual(daemon.page_url(1), 'https://a.org/')
            with self.assertRaisesRegex(ValueError, 'No open tab with pageId 2'):
                daemon.page_url(2)

    def test_find_page_is_per_agent(self):
        claims = Mock()
        claims.page_owner.side_effect = lambda pid: {1: 'a', 2: None}[pid]
        pages = [page(1, 'https://www.familysearch.org/'), page(2, 'https://www.familysearch.org/b', 'Other')]
        with patch.object(daemon, 'CLAIMS', claims), patch.object(daemon, 'list_pages', return_value=pages):
            self.assertEqual(daemon.find_page('FamilySearch', 'a'), 1)
            self.assertEqual(daemon.find_page('familysearch'), 2)
            with self.assertRaisesRegex(ValueError, 'owned by a'):
                daemon.find_page('nothing', 'a')

    def evaluate(self, js, text='plain'):
        with patch.object(daemon, 'call_tool', return_value={'content': [{'text': text}]}) as call:
            result = daemon.evaluate_script(3, js)
        return result, call.call_args

    def test_evaluate_script_wraps_bare_expressions(self):
        _, call = self.evaluate('  document.title  ')
        self.assertEqual(call.args[1]['function'], '() => { return (document.title); }')
        self.assertEqual(call.args[1]['pageId'], 3)
        self.assertIs(call.args[1]['waitForStableDom'], False)

    def test_evaluate_script_leaves_functions_alone(self):
        for js in ('() => 1', 'async () => 1', 'function f() {}'):
            with self.subTest(js=js):
                self.assertEqual(self.evaluate(js)[1].args[1]['function'], js)

    def test_evaluate_script_parses_json_block(self):
        self.assertEqual(self.evaluate('() => 1', 'Result\n```json\n{"a": [1, 2]}\n```\n')[0], {'a': [1, 2]})

    def test_evaluate_script_returns_unparseable_block_as_text(self):
        self.assertEqual(self.evaluate('() => 1', '```json\nnot json\n```')[0], 'not json')

    def test_evaluate_script_returns_plain_text(self):
        self.assertEqual(self.evaluate('() => 1', 'hello')[0], 'hello')

    def test_evaluate_script_passes_timeout(self):
        with patch.object(daemon, 'call_tool', return_value={'content': [{'text': 'x'}]}) as call:
            daemon.evaluate_script(3, '() => 1', timeout=44)
        self.assertEqual(call.call_args.kwargs['timeout'], 44)

    def test_call_tool_raises_on_error_and_is_error_results(self):
        class Q:
            def __init__(self, reply):
                self.reply = reply

            def get(self, timeout=None):
                return self.reply

        for reply, match in (({'error': {'code': 1}}, 'Tool error'),
                             ({'result': {'isError': True, 'content': [{'text': 'bad'}, {'text': 'worse'}]}},
                              'Tool failed: bad\nworse')):
            with self.subTest(reply=reply), patch.object(daemon, '_send'), \
                    patch.object(daemon.queue, 'Queue', return_value=Q(reply)):
                with self.assertRaisesRegex(RuntimeError, match):
                    daemon._call_tool('x', {})
        self.assertFalse(daemon.PENDING_RESPONSES)


class FetchFsImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = os.path.join(self.tmp.name, 'img.jpg')

    def fetch(self, result):
        with patch.object(daemon, 'evaluate_script', return_value=result) as ev, patch.object(daemon, 'log') as log:
            out = daemon.fetch_fs_image(1, 39, self.dest)
        return out, ev, log

    def test_saves_jpeg_and_returns_size_and_ark(self):
        data = 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()
        (size, ark), ev, _ = self.fetch({'ark': '3:1:ABC', 'data': data, 'retried': False})
        self.assertEqual((size, ark), (len(JPEG), '3:1:ABC'))
        with open(self.dest, 'rb') as f:
            self.assertEqual(f.read(), JPEG)
        self.assertIn('const want = 39;', ev.call_args.args[1])
        self.assertEqual(ev.call_args.kwargs['timeout'], 90)

    def test_logs_when_retry_was_needed(self):
        data = 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()
        _, _, log = self.fetch({'ark': 'A', 'data': data, 'retried': True, 'used': 'B', 'note': 'html'})
        log.assert_called_once()

    def test_unexpected_results_raise_and_write_nothing(self):
        for result in (None, 'text', {'data': 'data:image/png;base64,AAAA'}, {'ark': 'A'}):
            with self.subTest(result=result), self.assertRaisesRegex(RuntimeError, 'unexpected result'):
                self.fetch(result)
        self.assertFalse(os.path.exists(self.dest))

    def test_non_jpeg_bytes_raise(self):
        data = 'data:image/jpeg;base64,' + base64.b64encode(b'<html>nope</html>').decode()
        with self.assertRaisesRegex(RuntimeError, 'not a JPEG'):
            self.fetch({'ark': 'A', 'data': data})
        self.assertFalse(os.path.exists(self.dest))

    def test_never_overwrites_existing_file(self):
        with open(self.dest, 'wb') as f:
            f.write(b'old')
        data = 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()
        with self.assertRaises(FileExistsError):
            self.fetch({'ark': 'A', 'data': data})
        with open(self.dest, 'rb') as f:
            self.assertEqual(f.read(), b'old')

    def test_image_number_is_forced_to_int(self):
        with patch.object(daemon, 'evaluate_script', return_value=None):
            with self.assertRaises(ValueError):
                daemon.fetch_fs_image(1, '1; alert(1)', self.dest)


class DownloadFsEndpointTest(HttpCase):
    """Path safety and the button fallback, with a temp sources and downloads folder."""

    def setUp(self):
        super().setUp()
        self.pages = [page(1, FS)]
        self.claims.own_page('a', 1)
        for p in (patch.object(daemon.time, 'sleep'),):
            p.start()
            self.addCleanup(p.stop)
        self.dest = os.path.join(daemon.SOURCES, 'out.jpg')

    def payload(self, **kw):
        base = {'agent': 'a', 'pageId': 1, 'imageNumber': '5', 'destFilename': 'out.jpg'}
        base.update(kw)
        return base

    def button_script(self, files=(), format_ok=True, button_found=True):
        """An evaluate_script stand-in: clicking the button drops `files` in the downloads folder."""
        calls = []

        def evaluate(page_id, script, timeout=20):
            calls.append(script)
            if 'download-image-button' in script:
                if not button_found:
                    raise RuntimeError('FamilySearch image download button not found')
                for name in files:
                    with open(os.path.join(daemon.DOWNLOADS, name), 'wb') as f:
                        f.write(JPEG)
                return True
            if 'JPG Only' in script:
                return format_ok
            return True

        evaluate.calls = calls
        return evaluate

    def test_fetch_success(self):
        def fetch(page_id, num, dest):
            with open(dest, 'xb') as f:
                f.write(JPEG)
            return len(JPEG), '3:1:ABC'

        with patch.object(daemon, 'evaluate_script', return_value=True), \
                patch.object(daemon, 'fetch_fs_image', side_effect=fetch) as fetch_mock:
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 200)
        self.assertEqual(body, {'status': 'saved', 'path': self.dest, 'size': len(JPEG),
                                'method': 'fetch', 'ark': '3:1:ABC'})
        self.assertEqual(fetch_mock.call_args.args[1], '5')

    def test_existing_destination_is_refused_before_touching_the_browser(self):
        with open(self.dest, 'wb') as f:
            f.write(b'precious')
        with patch.object(daemon, 'evaluate_script') as ev, patch.object(daemon, 'fetch_fs_image') as fetch:
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 500)
        self.assertIn('refusing to overwrite', body['error'])
        ev.assert_not_called()
        fetch.assert_not_called()
        with open(self.dest, 'rb') as f:
            self.assertEqual(f.read(), b'precious')

    def test_destination_path_components_are_stripped(self):
        for name in ('../../evil.jpg', '/etc/evil.jpg', 'sub/../evil.jpg'):
            with self.subTest(name=name):
                def fetch(page_id, num, dest):
                    self.assertEqual(os.path.dirname(dest), daemon.SOURCES)
                    with open(dest, 'xb') as f:
                        f.write(JPEG)
                    return 1, 'a'

                target = os.path.join(daemon.SOURCES, 'evil.jpg')
                if os.path.exists(target):
                    os.remove(target)
                with patch.object(daemon, 'evaluate_script', return_value=True), \
                        patch.object(daemon, 'fetch_fs_image', side_effect=fetch):
                    code, body, _ = self.post('/download_fs', self.payload(destFilename=name))
                self.assertEqual((code, body['path']), (200, target))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'evil.jpg')))

    def test_missing_dest_filename_is_an_error_not_a_crash(self):
        payload = self.payload()
        del payload['destFilename']
        with patch.object(daemon, 'evaluate_script', return_value=True):
            code, _, _ = self.post('/download_fs', payload)
        self.assertEqual(code, 500)

    def test_empty_dest_filename_is_refused(self):
        with patch.object(daemon, 'evaluate_script', return_value=True), \
                patch.object(daemon, 'fetch_fs_image') as fetch:
            code, _, _ = self.post('/download_fs', self.payload(destFilename=''))
        self.assertEqual(code, 500)
        fetch.assert_not_called()

    def test_defaults_to_the_agents_familysearch_tab(self):
        payload = self.payload()
        del payload['pageId']
        with patch.object(daemon, 'evaluate_script', return_value=True), \
                patch.object(daemon, 'fetch_fs_image', return_value=(1, 'a')) as fetch:
            code, _, _ = self.post('/download_fs', payload)
        self.assertEqual(code, 200)
        self.assertEqual(fetch.call_args.args[0], 1)

    def test_other_agents_tab_is_refused(self):
        self.claims.own_page('b', 1)
        with patch.object(daemon, 'evaluate_script') as ev:
            self.assertEqual(self.post('/download_fs', self.payload())[0], 409)
        ev.assert_not_called()

    def test_missing_image_number_control_is_an_error(self):
        with patch.object(daemon, 'evaluate_script', side_effect=RuntimeError('image-number control not found')):
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 500)
        self.assertIn('control not found', body['error'])

    def test_button_fallback_copies_the_single_new_file(self):
        evaluate = self.button_script(files=['download (1).jpg'])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('no token')):
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 200)
        self.assertEqual((body['status'], body['method'], body['size']), ('saved', 'button', len(JPEG)))
        with open(self.dest, 'rb') as f:
            self.assertEqual(f.read(), JPEG)
        # The original download stays in the downloads folder.
        self.assertEqual(os.listdir(daemon.DOWNLOADS), ['download (1).jpg'])

    def test_button_fallback_ignores_files_that_were_already_there(self):
        with open(os.path.join(daemon.DOWNLOADS, 'old.jpg'), 'wb') as f:
            f.write(b'old')
        evaluate = self.button_script(files=['new.jpg'])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            code, _, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 200)
        with open(self.dest, 'rb') as f:
            self.assertEqual(f.read(), JPEG)

    def test_button_fallback_only_looks_at_jpg_files(self):
        evaluate = self.button_script(files=['page.pdf', 'real.jpg'])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            code, _, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 200)

    def test_more_than_one_new_file_is_refused_and_nothing_saved(self):
        evaluate = self.button_script(files=['a.jpg', 'b.jpg'])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 500)
        self.assertIn('More than one new file', body['error'])
        self.assertFalse(os.path.exists(self.dest))

    def test_timeout_when_no_file_arrives(self):
        evaluate = self.button_script(files=[])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 500)
        self.assertIn('timed out', body['error'])
        self.assertFalse(os.path.exists(self.dest))
        # The format dialog was tried on each poll until it was confirmed.
        self.assertGreaterEqual(sum('JPG Only' in s for s in evaluate.calls), 1)

    def test_format_dialog_is_confirmed_once_it_succeeds(self):
        evaluate = self.button_script(files=[], format_ok=True)
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            self.post('/download_fs', self.payload())
        self.assertEqual(sum('JPG Only' in s for s in evaluate.calls), 1)

    def test_missing_button_is_an_error(self):
        evaluate = self.button_script(button_found=False)
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual(code, 500)
        self.assertIn('download button not found', body['error'])

    def test_partial_fetch_file_is_removed_before_the_fallback(self):
        def fetch(page_id, num, dest):
            with open(dest, 'xb') as f:
                f.write(b'partial')
            raise RuntimeError('died after writing')

        evaluate = self.button_script(files=['new.jpg'])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=fetch):
            code, body, _ = self.post('/download_fs', self.payload())
        self.assertEqual((code, body['method']), (200, 'button'))
        with open(self.dest, 'rb') as f:
            self.assertEqual(f.read(), JPEG)

    def test_download_lock_is_released_after_a_failure(self):
        evaluate = self.button_script(files=['a.jpg', 'b.jpg'])
        with patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon, 'fetch_fs_image', side_effect=RuntimeError('x')):
            self.post('/download_fs', self.payload())
        self.assertTrue(daemon.DOWNLOADS_LOCK.acquire(blocking=False))
        daemon.DOWNLOADS_LOCK.release()


class DownloadFileEndpointTest(HttpCase):
    def setUp(self):
        super().setUp()
        self.pages = [page(1, ANC)]
        self.claims.own_page('a', 1)

    def post_file(self, data_url, dest):
        with patch.object(daemon, 'evaluate_script', return_value=data_url) as ev:
            result = self.post('/download_file', {'agent': 'a', 'pageId': 1,
                                                  'url': 'https://www.ancestry.com/img?x=1', 'destPath': dest})
        return result, ev

    def test_saves_decoded_bytes_and_creates_folders(self):
        dest = os.path.join(self.tmp.name, 'a', 'b', 'x.jpg')
        (code, body, _), ev = self.post_file('data:image/jpeg;base64,' + base64.b64encode(JPEG).decode(), dest)
        self.assertEqual((code, body), (200, {'status': 'saved', 'path': dest, 'size': len(JPEG)}))
        with open(dest, 'rb') as f:
            self.assertEqual(f.read(), JPEG)
        self.assertIn('fetch("https://www.ancestry.com/img?x=1")', ev.call_args.args[1])
        self.assertEqual(ev.call_args.kwargs['timeout'], 30)

    def test_bare_filename_destination_is_saved_in_working_directory(self):
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        (code, body, _), _ = self.post_file('data:image/jpeg;base64,' + base64.b64encode(JPEG).decode(), 'bare.jpg')
        self.assertEqual(code, 200, body)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, 'bare.jpg')))

    def test_non_data_url_result_is_refused_and_nothing_written(self):
        dest = os.path.join(self.tmp.name, 'x.jpg')
        for bad in ('HTTP 401', {'error': 1}, None):
            with self.subTest(bad=bad):
                (code, body, _), _ = self.post_file(bad, dest)
                self.assertEqual(code, 500)
                self.assertIn('Expected data URL', body['error'])
        self.assertFalse(os.path.exists(dest))

    def test_url_is_json_escaped_in_page_script(self):
        with patch.object(daemon, 'evaluate_script', return_value='data:;base64,AAAA') as ev:
            self.post('/download_file', {'agent': 'a', 'pageId': 1, 'url': '"); evil(); ("',
                                         'destPath': os.path.join(self.tmp.name, 'z')})
        self.assertIn('fetch("\\"); evil(); (\\"")', ev.call_args.args[1])

    def test_other_agents_tab_is_refused(self):
        self.claims.own_page('b', 1)
        with patch.object(daemon, 'evaluate_script') as ev:
            code, _, _ = self.post('/download_file', {'agent': 'a', 'pageId': 1, 'url': 'u',
                                                      'destPath': os.path.join(self.tmp.name, 'z')})
        self.assertEqual(code, 409)
        ev.assert_not_called()


class ClientAgainstDaemonTest(HttpCase):
    """The real client talking to the real handler, as a smoke test of the protocol."""

    def setUp(self):
        super().setUp()
        for p in (patch.object(client, 'URL', self.base), patch.object(client, 'AGENT', None),
                  patch.object(daemon, 'open_page', return_value=21)):
            p.start()
            self.addCleanup(p.stop)

    def test_claim_conflict_maps_to_claim_refused(self):
        client.set_agent('a')
        self.assertEqual(client.claim('familysearch'), 21)
        client.set_agent('b')
        with self.assertRaisesRegex(client.ClaimRefused, 'held by a'):
            client.claim('familysearch')

    def test_is_running_and_claims(self):
        self.assertTrue(client.is_running())
        client.set_agent('a')
        client.claim('ancestry')
        self.assertEqual(client.claims()['sites']['ancestry']['agent'], 'a')

    def test_server_error_maps_to_runtime_error(self):
        with patch.object(daemon, 'list_pages', side_effect=RuntimeError('chrome gone')):
            with self.assertRaisesRegex(RuntimeError, r'HTTP 500: chrome gone'):
                client.get_pages()

    def test_release_round_trip(self):
        client.set_agent('a')
        client.claim('familysearch')
        with patch.object(daemon, 'call_tool', return_value={}):
            result = client.release('familysearch')
        self.assertEqual(result['sites'], ['familysearch'])
        client.set_agent('b')
        self.assertEqual(client.claim('familysearch'), 21)

    def test_is_running_false_when_nothing_listens(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            dead = f'http://127.0.0.1:{sock.getsockname()[1]}'
        with patch.object(client, 'URL', dead):
            self.assertFalse(client.is_running())


if __name__ == '__main__':
    unittest.main()
