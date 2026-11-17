"""Tests for ancestry_fetch.py (a script) with the bridge client mocked; no browser, no network."""

import contextlib
import io
import os
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import client  # noqa: E402

SCRIPT = os.path.join(HERE, 'ancestry_fetch.py')


class AncestryFetchTest(unittest.TestCase):
    def setUp(self):
        self._agent = client.AGENT
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, 'nested', 'out')

    def tearDown(self):
        client.AGENT = self._agent
        self.tmp.cleanup()

    def run_script(self, argv, env_agent='tester', evaluate=None, download=None):
        env = {k: v for k, v in os.environ.items() if k != 'BRIDGE_AGENT'}
        if env_agent is not None:
            env['BRIDGE_AGENT'] = env_agent
        stdout = io.StringIO()
        with patch.object(sys, 'argv', ['ancestry_fetch.py'] + argv), \
                patch.dict(os.environ, env, clear=True), \
                patch.object(client, 'evaluate', side_effect=evaluate) as ev, \
                patch.object(client, 'download_file', side_effect=download) as dl, \
                patch('time.sleep') as sleep, \
                contextlib.redirect_stdout(stdout):
            runpy.run_path(SCRIPT, run_name='__main__')
        return stdout.getvalue(), ev, dl, sleep

    def test_saves_each_image_and_prints_status(self):
        urls = ['https://www.ancestry.com/dl/1', 'https://www.ancestry.com/dl/2']
        out, ev, dl, sleep = self.run_script(
            ['--page', '7', '--db', '9061', '--out', self.out, 'IMG_1', 'IMG_2'],
            evaluate=urls, download=[{'status': 'saved', 'size': 100}, {'status': 'saved', 'size': 200}])
        self.assertEqual(out.splitlines(), ['IMG_1 saved 100', 'IMG_2 saved 200'])
        self.assertTrue(os.path.isdir(self.out))
        self.assertEqual(client.AGENT, 'tester')
        first = dl.call_args_list[0].args
        self.assertEqual(first[0], 7)
        self.assertEqual(first[1], urls[0])
        self.assertEqual(first[2], os.path.abspath(os.path.join(self.out, 'IMG_1.jpg')))
        self.assertEqual(dl.call_args_list[1].args[2], os.path.abspath(os.path.join(self.out, 'IMG_2.jpg')))

    def test_javascript_asks_token_endpoint_for_this_db_and_image(self):
        _, ev, _, _ = self.run_script(['--page', '7', '--db', '9061', '--out', self.out, 'IMG_1'],
                                      evaluate=['https://x/y'], download=[{'status': 'saved', 'size': 1}])
        js = ev.call_args.args[1]
        self.assertIn('dbId=9061&imageId=IMG_1', js)
        self.assertIn('/imageviewer/api/media/token', js)
        self.assertEqual(ev.call_args.args[0], 7)
        self.assertEqual(ev.call_args.kwargs['timeout'], 30)

    def test_pause_between_images_only(self):
        _, _, _, sleep = self.run_script(
            ['--page', '7', '--db', '1', '--out', self.out, '--pause', '2.5', 'A', 'B', 'C'],
            evaluate=['https://x/a', 'https://x/b', 'https://x/c'],
            download=[{'status': 'saved', 'size': 1}] * 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.5, 2.5])

    def test_default_pause_is_five_seconds(self):
        _, _, _, sleep = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A', 'B'],
                                         evaluate=['https://x/a', 'https://x/b'],
                                         download=[{'status': 'saved', 'size': 1}] * 2)
        sleep.assert_called_once_with(5)

    def test_http_failure_is_reported_and_not_downloaded(self):
        out, _, dl, _ = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A'],
                                        evaluate=['HTTP 401'])
        self.assertEqual(out.strip(), 'A FAILED: HTTP 401')
        dl.assert_not_called()

    def test_missing_download_url_is_reported(self):
        out, _, dl, _ = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A'],
                                        evaluate=['no imageDownloadUrl'])
        self.assertIn('FAILED: no imageDownloadUrl', out)
        dl.assert_not_called()

    def test_failure_does_not_stop_later_images(self):
        out, _, dl, _ = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A', 'B'],
                                        evaluate=['HTTP 500', 'https://x/b'],
                                        download=[{'status': 'saved', 'size': 5}])
        self.assertEqual(out.splitlines(), ['A FAILED: HTTP 500', 'B saved 5'])
        self.assertEqual(dl.call_count, 1)

    def test_quoted_url_is_unquoted(self):
        _, _, dl, _ = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A'],
                                      evaluate=['"https://x/q"'], download=[{'status': 'saved', 'size': 1}])
        self.assertEqual(dl.call_args.args[1], 'https://x/q')

    def test_non_https_url_is_refused(self):
        out, _, dl, _ = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A'],
                                        evaluate=['http://insecure/x'])
        self.assertIn('FAILED', out)
        dl.assert_not_called()

    def test_non_dict_download_result_is_printed_with_blank_size(self):
        out, _, _, _ = self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A'],
                                       evaluate=['https://x/a'], download=['done'])
        self.assertEqual(out.strip(), 'A done')

    def test_requires_bridge_agent(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_script(['--page', '7', '--db', '1', '--out', self.out, 'A'], env_agent=None)
        self.assertEqual(cm.exception.code, 'set BRIDGE_AGENT')
        self.assertFalse(os.path.exists(self.out))

    def test_requires_page_db_out_and_images(self):
        for argv in (['--db', '1', '--out', 'o', 'A'], ['--page', '1', '--out', 'o', 'A'],
                     ['--page', '1', '--db', '1', 'A'], ['--page', '1', '--db', '1', '--out', 'o']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    self.run_script(argv)
                self.assertEqual(cm.exception.code, 2)

    def test_page_must_be_an_integer(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_script(['--page', 'abc', '--db', '1', '--out', self.out, 'A'])


if __name__ == '__main__':
    unittest.main()
