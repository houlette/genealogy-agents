import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

if Image is not None:
    import crop_image


@unittest.skipIf(Image is None, 'Pillow missing')
class CropTests(unittest.TestCase):
    W, H = 200, 100

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = os.path.join(self.tmp.name, 'src.png')
        # Left half red, right half blue, so a crop's colour shows where it came from.
        im = Image.new('RGB', (self.W, self.H), (255, 0, 0))
        im.paste((0, 0, 255), (self.W // 2, 0, self.W, self.H))
        im.save(self.src)
        self.dst = os.path.join(self.tmp.name, 'out', 'nested', 'crop.png')

    def run_main(self, *args, dst=None):
        buf = io.StringIO()
        with mock.patch('sys.argv', ['crop_image.py', self.src, dst or self.dst] + list(args)), mock.patch('sys.stdout', buf):
            crop_image.main()
        return buf.getvalue()

    def result(self, dst=None):
        with Image.open(dst or self.dst) as im:
            return im.size, im.convert('RGB').getpixel((0, 0))

    def test_box_pixels(self):
        out = self.run_main('--box', '10', '20', '60', '70')
        self.assertEqual(self.result()[0], (50, 50))
        self.assertEqual(self.result()[1], (255, 0, 0))
        self.assertIn('(50x50 px, cropped from 200x100 px)', out)
        self.assertIn(self.dst, out)

    def test_box_takes_right_part(self):
        self.run_main('--box', '150', '0', '200', '100')
        self.assertEqual(self.result(), ((50, 100), (0, 0, 255)))

    def test_rel_fractions(self):
        self.run_main('--rel', '0.5', '0.25', '1.0', '0.75')
        self.assertEqual(self.result(), ((100, 50), (0, 0, 255)))

    def test_rel_truncates_to_int(self):
        self.run_main('--rel', '0.1', '0.1', '0.5', '0.5')
        self.assertEqual(self.result()[0], (80, 40))  # left 20, right 100; top 10, bottom 50

    def test_creates_parent_directories(self):
        self.assertFalse(os.path.exists(os.path.dirname(self.dst)))
        self.run_main('--box', '0', '0', '10', '10')
        self.assertTrue(os.path.exists(self.dst))

    def test_box_clamped_to_image(self):
        self.run_main('--box', '-50', '-50', '5000', '5000')
        self.assertEqual(self.result()[0], (self.W, self.H))

    def test_box_partly_outside(self):
        self.run_main('--box', '190', '90', '400', '400')
        self.assertEqual(self.result()[0], (10, 10))

    def test_box_entirely_outside_gives_one_pixel_inside_image(self):
        self.run_main('--box', '500', '500', '600', '600')
        size, px = self.result()
        self.assertEqual(size, (1, 1))
        self.assertEqual(px, (0, 0, 255))  # the bottom-right pixel of the source, not padding

    def test_inverted_box_gives_one_pixel(self):
        self.run_main('--box', '100', '50', '20', '10')
        self.assertEqual(self.result()[0], (1, 1))

    def test_zero_size_box_gives_one_pixel(self):
        self.run_main('--box', '10', '10', '10', '10')
        self.assertEqual(self.result()[0], (1, 1))

    def test_jpeg_output(self):
        dst = os.path.join(self.tmp.name, 'c.jpg')
        self.run_main('--box', '0', '0', '40', '40', dst=dst)
        with Image.open(dst) as im:
            self.assertEqual((im.format, im.size), ('JPEG', (40, 40)))

    def test_source_not_modified(self):
        before = open(self.src, 'rb').read()
        self.run_main('--box', '0', '0', '40', '40')
        self.assertEqual(open(self.src, 'rb').read(), before)

    def test_missing_source(self):
        os.remove(self.src)
        with self.assertRaises(SystemExit) as cm:
            self.run_main('--box', '0', '0', '1', '1')
        self.assertIn('Source image not found', str(cm.exception))
        self.assertFalse(os.path.exists(self.dst))

    def test_requires_box_or_rel(self):
        with mock.patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as cm:
            self.run_main()
        self.assertEqual(cm.exception.code, 2)

    def test_box_and_rel_mutually_exclusive(self):
        with mock.patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as cm:
            self.run_main('--box', '0', '0', '1', '1', '--rel', '0', '0', '1', '1')
        self.assertEqual(cm.exception.code, 2)

    def test_box_wrong_arity_and_non_integer(self):
        for args in (['--box', '1', '2', '3'], ['--box', 'a', 'b', 'c', 'd'], ['--box', '1.5', '0', '10', '10']):
            with mock.patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as cm:
                self.run_main(*args)
            self.assertEqual(cm.exception.code, 2, args)

    def test_command_line_subprocess(self):
        p = subprocess.run([sys.executable, os.path.join(TOOLS, 'crop_image.py'), self.src, self.dst, '--rel', '0', '0', '0.5', '1'],
                           capture_output=True, text=True, cwd=self.tmp.name)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Saved crop:', p.stdout)
        self.assertEqual(self.result(), ((100, 100), (255, 0, 0)))

    def test_command_line_missing_source_exit_status(self):
        p = subprocess.run([sys.executable, os.path.join(TOOLS, 'crop_image.py'), os.path.join(self.tmp.name, 'none.png'),
                            self.dst, '--box', '0', '0', '1', '1'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('Source image not found', p.stderr)


if __name__ == '__main__':
    unittest.main()
