import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import decode_b64  # noqa: E402

PAYLOAD = bytes(range(256)) * 3
B64 = base64.b64encode(PAYLOAD).decode()


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, 'in.txt')
        self.dst = os.path.join(self.tmp.name, 'out.bin')

    def run_main(self, text):
        open(self.src, 'w', encoding='utf-8').write(text)
        buf = io.StringIO()
        with mock.patch('sys.argv', ['decode_b64.py', self.src, self.dst]), mock.patch('sys.stdout', buf):
            decode_b64.main()
        return buf.getvalue()

    def assertDecoded(self):
        self.assertEqual(open(self.dst, 'rb').read(), PAYLOAD)

    def test_plain_json_string(self):
        out = self.run_main(json.dumps(B64))
        self.assertDecoded()
        self.assertEqual(out.strip(), f'Decoded {len(PAYLOAD)} bytes to {self.dst}')  # decoded length, not base64 length

    def test_surrounding_whitespace(self):
        self.run_main('\n  ' + json.dumps(B64) + '  \n')
        self.assertDecoded()

    def test_data_uri_prefix_stripped(self):
        self.run_main(json.dumps('data:image/png;base64,' + B64))
        self.assertDecoded()

    def test_fenced_json_block_with_prose(self):
        self.run_main('Here is the output:\n```json\n' + json.dumps(B64) + '\n```\nThat is all.')
        self.assertDecoded()

    def test_result_marker(self):
        self.run_main('Some tool header\nResult:\n' + json.dumps(B64) + '\n')
        self.assertDecoded()

    def test_fence_takes_precedence_over_result_marker(self):
        self.run_main('Result:\n```json\n' + json.dumps(B64) + '\n```')
        self.assertDecoded()

    def test_unicode_text_in_file_ok(self):
        self.run_main('Résultat é\nResult:\n' + json.dumps(B64))
        self.assertDecoded()

    def test_invalid_json_raises(self):
        with self.assertRaises(ValueError):
            self.run_main('not json at all')
        self.assertFalse(os.path.exists(self.dst))

    def test_unclosed_fence_raises(self):
        with self.assertRaises(ValueError):
            self.run_main('```json\n' + json.dumps(B64))

    def test_invalid_base64_raises_and_writes_nothing(self):
        with self.assertRaises(Exception):
            self.run_main(json.dumps('@@@not base64@@@'))
        self.assertFalse(os.path.exists(self.dst))

    def test_missing_source_file(self):
        with mock.patch('sys.argv', ['decode_b64.py', os.path.join(self.tmp.name, 'nope'), self.dst]):
            with self.assertRaises(FileNotFoundError):
                decode_b64.main()

    def test_missing_arguments(self):
        with mock.patch('sys.argv', ['decode_b64.py']):
            with self.assertRaises(IndexError):
                decode_b64.main()

    def test_command_line_subprocess(self):
        open(self.src, 'w').write(json.dumps(B64))
        p = subprocess.run([sys.executable, os.path.join(TOOLS, 'decode_b64.py'), self.src, self.dst],
                           capture_output=True, text=True, cwd=self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f'Decoded {len(PAYLOAD)} bytes', p.stdout)
        self.assertDecoded()


if __name__ == '__main__':
    unittest.main()
