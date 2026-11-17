"""Tests for archion_export.py with the bridge client mocked; no browser, no network."""

import base64
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import archion_export as ax  # noqa: E402
import client  # noqa: E402

VIEWER = 'https://www.archion.de/de/viewer/churchRegister/abc?cHash=1'
IMAGE = 'https://img.archion.de/tiles/abc/thumb.jpg'
JPEG = b'\xff\xd8\xff\xe0' + b'x' * 2000


def good_state(**kw):
    state = dict(url=VIEWER, ready=True, bild='43', width=4000, height=3000, level=5, nativeLevel=5,
                 inAction=False, holderWidth=4000, holderHeight=3000, tileCount=20,
                 expectedTiles=20, image=IMAGE)
    state.update(kw)
    return state


def data_url(raw=JPEG):
    return 'data:image/jpeg;base64,' + base64.b64encode(raw).decode()


class ImageReadyTest(unittest.TestCase):
    def test_ready_state_matches(self):
        self.assertTrue(ax.image_ready(good_state(), 43))
        self.assertTrue(ax.image_ready(good_state(), '43'))

    def test_each_condition_blocks_readiness(self):
        for change in ({'ready': False}, {'bild': '44'}, {'inAction': True}, {'level': 4},
                       {'holderWidth': 3999}, {'holderHeight': 1}, {'tileCount': 0, 'expectedTiles': 0},
                       {'tileCount': 19}, {'tileCount': 21}):
            with self.subTest(change=change):
                self.assertFalse(ax.image_ready(good_state(**change), 43))

    def test_empty_state_is_not_ready(self):
        self.assertFalse(ax.image_ready({}, 43))
        self.assertFalse(ax.image_ready({'url': 'x', 'ready': False}, 43))

    def test_result_is_falsy_not_just_none_when_not_ready(self):
        self.assertFalse(bool(ax.image_ready({'ready': True}, 1)))


class WaitImageTest(unittest.TestCase):
    def test_returns_first_state_that_satisfies_predicate(self):
        states = [{'ready': False}, {'ready': True, 'n': 2}]
        with patch.object(client, 'evaluate', side_effect=states) as ev, patch.object(ax.time, 'sleep') as sleep:
            self.assertEqual(ax.wait_image(5, lambda s: s['ready']), states[1])
        self.assertEqual(ev.call_count, 2)
        self.assertEqual(ev.call_args.args[0], 5)
        sleep.assert_called_once()

    def test_times_out_with_last_state(self):
        clock = itertools.count(0, 10)
        with patch.object(client, 'evaluate', return_value={'ready': False}), \
                patch.object(ax.time, 'monotonic', side_effect=lambda: next(clock)), \
                patch.object(ax.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Image readiness timed out: {"ready": false}'):
                ax.wait_image(5, lambda s: False, timeout=25)

    def test_non_dict_state_is_an_error(self):
        with patch.object(client, 'evaluate', return_value='oops'):
            with self.assertRaisesRegex(RuntimeError, 'viewer state object'):
                ax.wait_image(5, lambda s: True)

    def test_predicate_checked_before_deadline(self):
        # A state that is ready on the first look is returned even with a zero timeout.
        with patch.object(client, 'evaluate', return_value={'ok': 1}):
            self.assertEqual(ax.wait_image(5, lambda s: True, timeout=0), {'ok': 1})


class FakeViewer:
    """Dispatches the scripts export() sends, like a page would."""

    def __init__(self, states, progress=None, result=None, rendered=True):
        self.states = list(states)
        self.progress = list(progress or [{'status': 'complete', 'loaded': 20, 'total': 20}])
        self.result = result
        self.clicks = []
        self.scripts = []
        self.last_state = None

    def __call__(self, page, script, timeout=20):
        self.scripts.append(script)
        if script == ax.STATE:
            if len(self.states) > 1:
                self.last_state = self.states.pop(0)
            else:
                self.last_state = self.states[0]
            return self.last_state
        if 'Bild unavailable' in script:
            return True
        if script.startswith('() => {document.querySelector('):
            self.clicks.append('.zoom-in' if '.zoom-in' in script else '.zoom-out')
            return True
        if script == ax.RENDER:
            return {'status': 'loading', 'total': 20}
        if 'status:j.status' in script:
            return self.progress.pop(0) if len(self.progress) > 1 else self.progress[0]
        if 'archionExportJob.result' in script:
            return self.result
        if "status='cancelled'" in script:
            return True
        raise AssertionError('unexpected script: ' + script[:80])


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / 'sub' / 'export.jpg'
        self.patches = [patch.object(ax.time, 'sleep')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_export(self, viewer, bild=43, output=None):
        with patch.object(client, 'evaluate', side_effect=viewer):
            return ax.export(129, bild, output or self.out)

    def viewer(self, **kw):
        result = kw.pop('result', {'width': 4000, 'height': 3000, 'tileCount': 20, 'image': IMAGE,
                                   'data': data_url()})
        states = kw.pop('states', [good_state()])
        return FakeViewer(states, result=result, **kw)

    def test_success_writes_jpeg_and_manifest(self):
        info = self.run_export(self.viewer())
        self.assertEqual(self.out.read_bytes(), JPEG)
        manifest = json.loads(self.out.with_suffix('.json').read_text())
        self.assertNotIn('data', manifest)
        self.assertEqual(manifest['bild'], '43')
        self.assertEqual(manifest['source'], VIEWER)
        self.assertEqual(manifest['image'], IMAGE)
        self.assertIn('native-resolution', manifest['method'])
        self.assertEqual(info['file'], str(self.out))
        self.assertEqual(info['bytes'], len(JPEG))
        self.assertNotIn('data', info)

    def test_manifest_ends_with_newline(self):
        self.run_export(self.viewer())
        self.assertTrue(self.out.with_suffix('.json').read_text().endswith('}\n'))

    def test_existing_output_is_never_overwritten(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_bytes(b'old')
        viewer = self.viewer()
        with self.assertRaises(FileExistsError):
            self.run_export(viewer)
        self.assertEqual(self.out.read_bytes(), b'old')
        self.assertEqual(viewer.scripts, [])  # refused before touching the browser

    def test_existing_manifest_blocks_export(self):
        self.out.parent.mkdir(parents=True)
        self.out.with_suffix('.json').write_text('{}')
        with self.assertRaises(FileExistsError):
            self.run_export(self.viewer())
        self.assertFalse(self.out.exists())

    def test_output_may_be_a_string(self):
        self.run_export(self.viewer(), output=str(self.out))
        self.assertTrue(self.out.exists())

    def test_rejects_a_tab_that_is_not_a_register_viewer(self):
        with self.assertRaisesRegex(RuntimeError, 'not an Archion register viewer'):
            self.run_export(self.viewer(states=[good_state(url='https://www.archion.de/de/suche')]))
        self.assertFalse(self.out.exists())

    def test_rejects_non_dict_initial_state(self):
        with patch.object(client, 'evaluate', return_value=None):
            with self.assertRaises(RuntimeError):
                ax.export(129, 43, self.out)

    def test_selects_the_requested_bild_by_json_quoted_text(self):
        viewer = self.viewer()
        self.run_export(viewer, bild=43)
        select = [s for s in viewer.scripts if 'Bild unavailable' in s][0]
        self.assertIn('x.textContent.trim()==="43"', select)

    def test_zooms_in_one_level_at_a_time_until_native(self):
        states = [good_state(level=3), good_state(level=3), good_state(level=4, tileCount=5, expectedTiles=5),
                  good_state(level=5)]
        viewer = self.viewer(states=states)
        self.run_export(viewer)
        self.assertEqual(viewer.clicks, ['.zoom-in', '.zoom-in'])

    def test_zooms_out_when_above_native(self):
        states = [good_state(level=7), good_state(level=7), good_state(level=6),
                  good_state(level=5)]
        viewer = self.viewer(states=states)
        self.run_export(viewer)
        self.assertEqual(viewer.clicks, ['.zoom-out', '.zoom-out'])

    def test_failed_tile_export_raises_and_writes_nothing(self):
        viewer = self.viewer(progress=[{'status': 'failed', 'error': 'Tile load failed'}])
        with self.assertRaisesRegex(RuntimeError, 'Tile export failed.*Tile load failed'):
            self.run_export(viewer)
        self.assertFalse(self.out.exists())

    def test_progress_is_polled_until_complete(self):
        viewer = self.viewer(progress=[{'status': 'loading', 'loaded': 3, 'total': 20},
                                       {'status': 'loading', 'loaded': 9, 'total': 20},
                                       {'status': 'complete', 'loaded': 20, 'total': 20}])
        self.run_export(viewer)
        self.assertTrue(self.out.exists())

    def test_timeout_cancels_the_job(self):
        viewer = self.viewer(progress=[{'status': 'loading', 'loaded': 1, 'total': 20}])
        clock = itertools.count(0, 100)
        with patch.object(ax.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(RuntimeError, 'Tile export timed out'):
                self.run_export(viewer)
        self.assertTrue(any("status='cancelled'" in s for s in viewer.scripts))
        self.assertFalse(self.out.exists())

    def test_result_for_a_different_image_is_refused(self):
        viewer = self.viewer(result={'image': 'https://img.archion.de/other.jpg', 'data': data_url()})
        with self.assertRaisesRegex(RuntimeError, 'does not match the selected image'):
            self.run_export(viewer)
        self.assertFalse(self.out.exists())

    def test_non_dict_result_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'does not match'):
            self.run_export(self.viewer(result=None))

    def test_non_jpeg_data_is_refused(self):
        viewer = self.viewer(result={'image': IMAGE, 'data': data_url(b'\x89PNG' + b'x' * 2000)})
        with self.assertRaisesRegex(RuntimeError, 'not a usable JPEG'):
            self.run_export(viewer)
        self.assertFalse(self.out.exists())

    def test_tiny_jpeg_is_refused(self):
        viewer = self.viewer(result={'image': IMAGE, 'data': data_url(b'\xff\xd8' + b'x' * 10)})
        with self.assertRaisesRegex(RuntimeError, 'not a usable JPEG'):
            self.run_export(viewer)

    def test_invalid_base64_is_refused(self):
        viewer = self.viewer(result={'image': IMAGE, 'data': 'data:image/jpeg;base64,@@@@'})
        with self.assertRaises(ValueError):
            self.run_export(viewer)
        self.assertFalse(self.out.exists())

    def test_creates_missing_parent_directories(self):
        self.run_export(self.viewer())
        self.assertTrue(self.out.parent.is_dir())


if __name__ == '__main__':
    unittest.main()
