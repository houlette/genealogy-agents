"""Protocol regression tests without Chrome: python3 tools/browser_bridge/test_daemon.py."""

import io
import json
import unittest
from unittest.mock import Mock, patch

import daemon

# The tests use 'Claude' as the profile name; a project sets its own in project.json.
daemon.MARKER_VALUE = 'Claude'
daemon.PROFILE = 'Claude'


class ProtocolTest(unittest.TestCase):
    def tearDown(self):
        daemon.PENDING_RESPONSES.clear()
        daemon.MCP_STDERR.clear()

    def test_root_request_does_not_consume_matching_outbound_response_id(self):
        # Server and client request IDs are separate namespaces.
        import queue
        response = queue.Queue()
        daemon.PENDING_RESPONSES[7] = response
        proc = Mock(stdout=io.StringIO(
            json.dumps({'jsonrpc': '2.0', 'id': 7, 'method': 'roots/list'}) + '\n' +
            json.dumps({'jsonrpc': '2.0', 'id': 7, 'result': {'ok': True}}) + '\n'))
        with patch.object(daemon, 'MCP_PROC', proc), patch.object(daemon, '_send') as send:
            daemon.reader_thread()
        self.assertEqual(send.call_args.args[0]['result']['roots'][0]['uri'],
                         daemon.WORKSPACE.as_uri())
        self.assertEqual(response.get_nowait()['result'], {'ok': True})
        self.assertTrue(response.empty())

    def test_timeout_cancels_pending_call_and_keeps_connection_diagnostic(self):
        daemon.MCP_STDERR.append('Chrome connection denied')
        with patch.object(daemon, 'MCP_PROC', Mock(poll=Mock(return_value=None))), \
                patch.object(daemon, '_send') as send:
            with self.assertRaisesRegex(TimeoutError, 'Chrome connection denied'):
                daemon.call_tool('list_pages', {}, timeout=0)
        request, cancellation = [c.args[0] for c in send.call_args_list]
        self.assertEqual(cancellation['method'], 'notifications/cancelled')
        self.assertEqual(cancellation['params']['requestId'], request['id'])
        self.assertFalse(daemon.PENDING_RESPONSES)

    def test_stderr_is_preserved_and_logged(self):
        with patch.object(daemon, 'MCP_PROC', Mock(stderr=io.StringIO('connection detail\n'))), \
                patch.object(daemon, 'log') as log:
            daemon.stderr_thread()
        self.assertEqual(list(daemon.MCP_STDERR), ['connection detail'])
        log.assert_called_once_with('MCP stderr: connection detail')


class ProfileBoundaryTest(unittest.TestCase):
    TARGET = 'https://www.familysearch.org/ark:/61903/3:1:ABC'
    NEW_PAGE = {'content': [{'type': 'text', 'text':
                            '7: Example Domain (https://example.com/) [selected]'}]}

    def test_wrong_profile_never_loads_research_site(self):
        with patch.object(daemon, 'PROFILE_CHECK', True), \
                patch.object(daemon, 'CLAIMS', Mock()) as claims, \
                patch.object(daemon, 'list_pages', return_value=[]), \
                patch.object(daemon, 'call_tool', return_value=self.NEW_PAGE) as call, \
                patch.object(daemon, 'evaluate_script', side_effect=[True, '']) as evaluate, \
                patch.object(daemon.time, 'sleep'):
            with self.assertRaises(daemon.WrongProfile):
                daemon.open_page('sol', self.TARGET)
        self.assertEqual(call.call_args_list[0].args[1]['url'], daemon.MARKER_URL)
        self.assertFalse(any(self.TARGET in c.args[1] for c in evaluate.call_args_list))
        self.assertEqual(call.call_args_list[-1].args, ('close_page', {'pageId': 7}))
        claims.own_page.assert_not_called()

    def test_research_navigation_follows_successful_marker_check(self):
        sequence = []

        def evaluate(page, script):
            if 'localStorage' in script:
                sequence.append('profile accepted')
                return 'Claude'
            if self.TARGET in script:
                sequence.append('research navigation')
            return True

        with patch.object(daemon, 'PROFILE_CHECK', True), \
                patch.object(daemon, 'CLAIMS', Mock()), \
                patch.object(daemon, 'list_pages', return_value=[]), \
                patch.object(daemon, 'call_tool', return_value=self.NEW_PAGE) as call, \
                patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon.time, 'sleep'):
            self.assertEqual(daemon.open_page('sol', self.TARGET), 7)
        self.assertEqual(call.call_args_list[0].args[1]['url'], daemon.MARKER_URL)
        self.assertEqual(sequence, ['profile accepted', 'research navigation'])


class AnchorTest(unittest.TestCase):
    TARGET = 'https://www.familysearch.org/ark:/61903/3:1:ABC'

    def tearDown(self):
        daemon.ANCHOR['id'] = None

    def run_open(self, pages_after_spawn):
        """An anchor (tab 3) exists; window.open from it makes whatever list_pages reports next."""
        state = {'pages': [{'id': 3, 'title': 'Example Domain', 'url': daemon.MARKER_URL + '#bridge-anchor-x'}], 'log': []}

        def evaluate(page, script):
            state['log'].append((page, script))
            if 'window.open' in script:
                target = script.split('window.open("')[1].split('"')[0]
                state['pages'] = state['pages'] + pages_after_spawn(target)
                return True
            if 'localStorage' in script:
                return 'Claude'
            return True

        claims = Mock()
        claims.page_owner.return_value = None
        with patch.object(daemon, 'PROFILE_CHECK', True), \
                patch.object(daemon, 'CLAIMS', claims), \
                patch.object(daemon, 'list_pages', side_effect=lambda: list(state['pages'])), \
                patch.object(daemon, 'call_tool', return_value=ProfileBoundaryTest.NEW_PAGE) as call, \
                patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon.time, 'sleep'):
            page = daemon.open_page('sol', self.TARGET)
        return page, call, state, claims

    def test_tab_is_opened_from_the_anchor_without_new_page(self):
        page, call, state, claims = self.run_open(lambda target: [{'id': 9, 'title': '', 'url': target}])
        self.assertEqual(page, 9)
        self.assertFalse(any(c.args[0] == 'new_page' for c in call.call_args_list))
        opened = [(p, s) for p, s in state['log'] if 'window.open' in s]
        self.assertEqual([p for p, _ in opened], [3])
        self.assertEqual(daemon.ANCHOR['id'], 3)
        # The marker is still read in the new tab before the research site loads.
        scripts_in_9 = [s for p, s in state['log'] if p == 9]
        self.assertTrue(any('localStorage' in s for s in scripts_in_9))
        self.assertIn(self.TARGET, scripts_in_9[-1])
        claims.own_page.assert_called_once_with('sol', 9)

    def test_blocked_popup_falls_back_to_new_page(self):
        page, call, state, claims = self.run_open(lambda target: [])
        self.assertEqual(page, 7)
        self.assertTrue(any(c.args[0] == 'new_page' for c in call.call_args_list))

    def test_first_accepted_tab_plants_an_anchor(self):
        state = {'pages': []}

        def evaluate(page, script):
            if 'window.open' in script:
                state['pages'] = [{'id': 8, 'title': '', 'url': script.split('window.open("')[1].split('"')[0]}]
                return True
            return 'Claude' if 'localStorage' in script else True

        with patch.object(daemon, 'PROFILE_CHECK', True), \
                patch.object(daemon, 'CLAIMS', Mock()), \
                patch.object(daemon, 'list_pages', side_effect=lambda: list(state['pages'])), \
                patch.object(daemon, 'call_tool', return_value=ProfileBoundaryTest.NEW_PAGE), \
                patch.object(daemon, 'evaluate_script', side_effect=evaluate), \
                patch.object(daemon.time, 'sleep'):
            self.assertEqual(daemon.open_page('sol', self.TARGET), 7)
        self.assertEqual(daemon.ANCHOR['id'], 8)
        self.assertIn(daemon.ANCHOR_TAG, state['pages'][0]['url'])


if __name__ == '__main__':
    unittest.main()
