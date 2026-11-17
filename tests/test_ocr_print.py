import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ocr_print  # noqa: E402

LANGS = 'List of available languages in "/opt/tessdata" (2):\neng\nosd\n'
PAGE = 'Heading line\nJohann Müller died\nunrelated\nMr. SCHMIDT of Ort\nlast line\n'


def cp(stdout='', stderr='', code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class FoldTests(unittest.TestCase):
    def test_folds_case_and_accents(self):
        self.assertEqual(ocr_print.fold('MÜLLER'), 'muller')
        self.assertEqual(ocr_print.fold('Kowalczyński'), 'kowalczynski')
        self.assertEqual(ocr_print.fold('Étienne Çelik'), 'etienne celik')

    def test_plain_ascii_unchanged(self):
        self.assertEqual(ocr_print.fold('abc 123'), 'abc 123')

    def test_eszett_is_not_expanded(self):
        # fold() only removes combining marks, so "ß" stays; --find Gross will not match "Groß".
        self.assertEqual(ocr_print.fold('Groß'), 'groß')


class MainBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.image = os.path.join(self.tmp.name, 'page.png')
        open(self.image, 'wb').write(b'x')

    def run_main(self, argv, langs=LANGS, ocr=None):
        ocr = ocr or cp(PAGE)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return cp(langs) if cmd[1] == '--list-langs' else ocr
        buf = io.StringIO()
        code = None
        with mock.patch('subprocess.run', side_effect=fake_run), mock.patch('sys.argv', ['ocr_print.py'] + argv), \
                mock.patch('sys.stdout', buf):
            try:
                ocr_print.main()
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue(), calls


class MainTests(MainBase):
    def test_prints_whole_text_without_find(self):
        code, out, calls = self.run_main([self.image])
        self.assertIsNone(code)
        self.assertEqual(out, PAGE + '\n')
        self.assertEqual(calls[1], ['tesseract', self.image, 'stdout', '-l', 'eng', '--psm', '3'])

    def test_lang_and_psm_passed(self):
        langs = 'List of available languages in "/x" (3):\neng\ndeu\nfra\n'
        _, _, calls = self.run_main([self.image, '--lang', 'deu+eng', '--psm', '6'], langs=langs)
        self.assertEqual(calls[1][3:], ['-l', 'deu+eng', '--psm', '6'])

    def test_missing_language_exits_before_ocr(self):
        code, out, calls = self.run_main([self.image, '--lang', 'deu'])
        self.assertIn('Tesseract language not installed', code)
        self.assertIn("['deu']", code)
        self.assertEqual(len(calls), 1)

    def test_missing_one_of_combined_languages(self):
        code, _, _ = self.run_main([self.image, '--lang', 'eng+fra'])
        self.assertIn("['fra']", code)

    def test_tesseract_failure(self):
        code, _, _ = self.run_main([self.image], ocr=cp('', 'Error: cannot read image', 1))
        self.assertIn('tesseract failed: Error: cannot read image', code)

    def test_find_matches_with_context_and_folding(self):
        code, out, _ = self.run_main([self.image, '--find', 'muller, schmidt'])
        lines = out.splitlines()
        self.assertEqual(lines[0], 'line 2: Heading line / Johann Müller died / unrelated')
        self.assertEqual(lines[1], 'line 4: unrelated / Mr. SCHMIDT of Ort / last line')
        self.assertIn('2 line(s) matched', lines[2])
        self.assertIn("['muller', 'schmidt']", lines[2])
        self.assertIn('in 5 lines of machine text', lines[2])
        self.assertIn('No match is not a negative result', lines[2])

    def test_find_accented_query_matches_plain_text(self):
        _, out, _ = self.run_main([self.image, '--find', 'MÜLLER'])
        self.assertIn('line 2:', out)

    def test_find_first_and_last_line_context_clipped(self):
        _, out, _ = self.run_main([self.image, '--find', 'heading,last'])
        lines = out.splitlines()
        self.assertEqual(lines[0], 'line 1: Heading line / Johann Müller died')
        self.assertEqual(lines[1], 'line 5: Mr. SCHMIDT of Ort / last line')

    def test_blank_context_lines_dropped(self):
        _, out, _ = self.run_main([self.image, '--find', 'beta'], ocr=cp('alpha\nbeta\n\ngamma\n'))
        self.assertEqual(out.splitlines()[0], 'line 2: alpha / beta')  # the blank line after the match is dropped

    def test_no_match(self):
        _, out, _ = self.run_main([self.image, '--find', 'zzz'])
        self.assertTrue(out.startswith('0 line(s) matched'))

    def test_blank_find_entries_ignored_means_print_all(self):
        _, out, _ = self.run_main([self.image, '--find', ' , ,'])
        self.assertEqual(out, PAGE + '\n')

    def test_save_writes_beside_image(self):
        _, out, _ = self.run_main([self.image, '--save'])
        saved = os.path.join(self.tmp.name, 'page.ocr.txt')
        self.assertIn(f'saved {saved}', out)
        text = open(saved).read()
        self.assertTrue(text.startswith('Tesseract draft (eng) of page.png; machine text, not checked against the image.\n\n'))
        self.assertTrue(text.endswith(PAGE))

    def test_save_with_find_still_searches(self):
        _, out, _ = self.run_main([self.image, '--save', '--find', 'schmidt'])
        self.assertIn('saved', out)
        self.assertIn('1 line(s) matched', out)

    def test_save_replaces_only_last_extension(self):
        img = os.path.join(self.tmp.name, 'a.b.jpg')
        open(img, 'w').close()
        self.run_main([img, '--save'])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, 'a.b.ocr.txt')))


if __name__ == '__main__':
    unittest.main()
