import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm  # noqa: E402
import bakeoff  # noqa: E402
import bakeoff_sheet  # noqa: E402


def entry(id='e1', **kw):
    e = {'id': id, 'crop': f'sources/{id}.png', 'register': 'Kirchenbuch <1>', 'place': 'Ort', 'year': 1730,
         'language': 'German', 'script': 'kurrent', 'key': ['Müller', '1730', 'Anna|Anne'], 'expect': ['groom Müller', 'bride Anna'],
         'focus': 'first entry'}
    e.update(kw)
    return e


def call_result(fields, cost=0.02):
    return {'text': json.dumps({'fields': fields}), 'cost': cost, 'usage': {}, 'model': 'x', 'stop': 'end'}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.dir = os.path.join(t, 'bakeoff')
        self.results = os.path.join(self.dir, 'results')
        os.makedirs(self.results)
        self.boxes = os.path.join(self.dir, 'boxes.json')
        self.sheet = os.path.join(self.dir, 'crop-sheet.html')
        for p in (mock.patch.object(bakeoff_sheet, 'DIR', self.dir), mock.patch.object(bakeoff_sheet, 'BOXES', self.boxes),
                  mock.patch.object(bakeoff_sheet, 'SHEET', self.sheet), mock.patch.object(bakeoff, 'RESULTS', self.results),
                  mock.patch.object(bakeoff, 'DIR', self.dir), mock.patch.object(llm, 'ROOT', t),
                  mock.patch.object(llm, 'LEDGER', os.path.join(t, 'ledger.jsonl'))):
            p.start()
            self.addCleanup(p.stop)

    def html(self):
        return open(self.sheet, encoding='utf-8').read()


class LocateTests(Base):
    def test_prompt_and_parsing(self):
        e = entry()
        fields = [{'item': 'Müller', 'found': True, 'as_written': 'Mueller', 'box_2d': [100, 200, 300, 400]},
                  {'item': '1730', 'found': False, 'as_written': '', 'box_2d': None},
                  {'item': 'Anna|Anne', 'found': True, 'as_written': 'Anne', 'box_2d': [10, 10, 10, 50]}]  # degenerate box
        with mock.patch.object(llm, 'call', return_value=call_result(fields)) as c:
            r = bakeoff_sheet.locate(e, 'argon')
        a = c.call_args
        self.assertEqual(a.args[0], 'argon')
        self.assertIn('- Müller\n- 1730\n- Anna|Anne', a.args[2])
        self.assertIn('Kirchenbuch <1>, Ort, 1730, written in German', a.args[2])
        self.assertEqual(a.kwargs['images'], [os.path.join(self.tmp.name, 'sources/e1.png')])
        self.assertEqual(a.kwargs['task'], 'cropsheet:e1')
        self.assertEqual(r['crop'], e['crop'])
        self.assertEqual(r['model'], 'argon')
        self.assertEqual(r['cost'], 0.02)
        self.assertEqual(r['fields']['Müller'], {'found': True, 'as_written': 'Mueller', 'box': [100, 200, 300, 400]})
        self.assertEqual(r['fields']['1730'], {'found': False, 'as_written': '', 'box': None})
        self.assertEqual(r['fields']['Anna|Anne']['found'], False)  # ymax == ymin is rejected
        self.assertIsNone(r['fields']['Anna|Anne']['box'])

    def test_bad_boxes_rejected(self):
        bad = [[1, 2, 3], 'abc', [1, 2, 3, 'x'], [5, 5, 1, 9], [1, 9, 5, 5], None]
        for b in bad:
            fields = [{'item': 'Müller', 'found': True, 'box_2d': b}]
            with mock.patch.object(llm, 'call', return_value=call_result(fields)):
                r = bakeoff_sheet.locate(entry(key=['Müller']), 'argon')
            self.assertFalse(r['fields']['Müller']['found'], b)
            self.assertIsNone(r['fields']['Müller']['box'])

    def test_missing_item_is_not_found_and_float_boxes_ok(self):
        fields = [{'item': ' Müller ', 'found': True, 'as_written': None, 'box_2d': [0.5, 1.5, 2.5, 3.5]}]
        with mock.patch.object(llm, 'call', return_value=call_result(fields)):
            r = bakeoff_sheet.locate(entry(key=['Müller', 'Other']), 'argon')
        self.assertTrue(r['fields']['Müller']['found'])  # item text is stripped before matching
        self.assertEqual(r['fields']['Müller']['as_written'], '')
        self.assertEqual(r['fields']['Other'], {'found': False, 'as_written': '', 'box': None})

    def test_unparseable_reply_raises(self):
        with mock.patch.object(llm, 'call', return_value={'text': 'sorry', 'cost': 0}):
            with self.assertRaises(llm.LLMError):
                bakeoff_sheet.locate(entry(), 'argon')


class RunLocateTests(Base):
    def run_locate(self, entries, redo=(), side_effect=None):
        buf = io.StringIO()
        with mock.patch.object(bakeoff_sheet, 'locate', side_effect=side_effect) as loc, mock.patch('sys.stdout', buf):
            bakeoff_sheet.run_locate(entries, 'argon', set(redo))
        return loc, buf.getvalue()

    def located(self, e, model='argon'):
        return {'crop': e['crop'], 'model': model, 'cost': 0.03,
                'fields': {k: {'found': True, 'as_written': k, 'box': [1, 2, 3, 4]} for k in e['key']}}

    def test_new_entries_located_and_saved(self):
        es = [entry('e1'), entry('e2')]
        loc, out = self.run_locate(es, side_effect=lambda e, m: self.located(e, m))
        self.assertEqual(loc.call_count, 2)
        saved = json.load(open(self.boxes, encoding='utf-8'))
        self.assertEqual(set(saved), {'e1', 'e2'})
        self.assertIn('3 of 3 located', out)
        self.assertFalse(os.path.exists(self.boxes + '.tmp'))

    def test_up_to_date_entries_not_relocated_unless_redo(self):
        e = entry('e1')
        json.dump({'e1': self.located(e)}, open(self.boxes, 'w'))
        loc, _ = self.run_locate([e], side_effect=lambda e, m: self.located(e, m))
        loc.assert_not_called()
        loc, _ = self.run_locate([e], redo=['e1'], side_effect=lambda e, m: self.located(e, m))
        loc.assert_called_once()

    def test_stale_when_crop_or_key_changed(self):
        e = entry('e1')
        json.dump({'e1': self.located(e)}, open(self.boxes, 'w'))
        for changed in (dict(e, crop='sources/other.png'), dict(e, key=e['key'] + ['New'])):
            loc, _ = self.run_locate([changed], side_effect=lambda e, m: self.located(e, m))
            self.assertEqual(loc.call_count, 1)

    def test_failure_leaves_existing_boxes_and_prints_failed(self):
        e1, e2 = entry('e1'), entry('e2')

        def side(e, m):
            if e['id'] == 'e2':
                raise llm.LLMError('HTTP 500')
            return self.located(e, m)
        loc, out = self.run_locate([e1, e2], side_effect=side)
        saved = json.load(open(self.boxes))
        self.assertEqual(set(saved), {'e1'})
        self.assertIn('e2', out)
        self.assertIn('FAILED HTTP 500', out)


class BuildTests(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch('sys.stdout', io.StringIO())
        p.start()
        self.addCleanup(p.stop)

    def put_result(self, e, model, informed_keys):
        # A blind reading that contains exactly the given key texts.
        bakeoff.save(bakeoff.result_path(e, model), {'model': model, 'cost': 0.0, 'errors': [],
                                                     'blind': {'persons': [{'name_as_written': ' '.join(informed_keys)}]}, 'informed': None})

    def test_basic_page_without_boxes_or_results(self):
        bakeoff_sheet.build([entry('e1')])
        h = self.html()
        self.assertTrue(h.startswith('<!doctype html>'))
        self.assertIn('<title>Bake-off crop sheet</title>', h)
        self.assertIn('<section id="e1">', h)
        self.assertIn('Kirchenbuch &lt;1&gt;', h)  # escaped
        self.assertNotIn('Kirchenbuch <1>', h)
        self.assertIn('src="../../sources/e1.png"', h)
        self.assertIn('1. Müller · 0/0', h)
        self.assertIn('3. Anna|Anne · 0/0', h)
        self.assertNotIn('class="box"', h)
        self.assertIn('groom Müller; bride Anna', h)
        self.assertNotIn('Decoy:', h)
        self.assertIn('href="#e1"', h)

    def test_hit_counts_and_none_class(self):
        e = entry('e1')
        self.put_result(e, 'opus', ['Müller', '1730'])
        self.put_result(e, 'haiku', ['Müller'])
        bakeoff_sheet.build([e])
        h = self.html()
        self.assertIn('1. Müller · 2/2', h)
        self.assertIn('2. 1730 · 1/2', h)
        # key 3 found by nobody: red class "none"
        self.assertRegex(h, r'<span class="none"[^>]*>3\. Anna\|Anne · 0/2')
        self.assertRegex(h, r'<span class=""[^>]*>1\. Müller')

    def test_boxes_drawn_as_percentages_with_colors(self):
        e = entry('e1')
        json.dump({'e1': {'crop': e['crop'], 'model': 'argon', 'cost': 0, 'fields': {
            'Müller': {'found': True, 'as_written': 'Mueller "x"', 'box': [100, 200, 300, 450]},
            '1730': {'found': False, 'as_written': '', 'box': None},
            'Anna|Anne': {'found': True, 'as_written': 'Anne', 'box': [0, 0, 1000, 1000]}}}},
            open(self.boxes, 'w'))
        bakeoff_sheet.build([e])
        h = self.html()
        self.assertIn('top:10.0%;left:20.0%;height:20.0%;width:25.0%', h)
        self.assertIn(f'border-color:{bakeoff_sheet.COLORS[0]}', h)
        self.assertIn(f'border-color:{bakeoff_sheet.COLORS[2]}', h)
        self.assertEqual(h.count('class="box"'), 2)
        self.assertIn('box reads "Mueller &quot;x&quot;"', h)
        self.assertIn('not located', h)

    def test_stale_boxes_ignored_when_crop_changed(self):
        e = entry('e1')
        json.dump({'e1': {'crop': 'sources/old.png', 'fields': {'Müller': {'found': True, 'as_written': 'x', 'box': [1, 2, 3, 4]}}}},
                  open(self.boxes, 'w'))
        bakeoff_sheet.build([e])
        self.assertNotIn('class="box"', self.html())

    def test_decoy_status_and_notes_shown_and_escaped(self):
        e = entry('e1', decoy='groom <b>Fake</b>', key_status='Key unchecked', crop_note='crop & more')
        bakeoff_sheet.build([e])
        h = self.html()
        self.assertIn('<b>Decoy:</b> groom &lt;b&gt;Fake&lt;/b&gt;', h)
        self.assertIn('Key unchecked crop &amp; more', h)

    def test_numbering_and_multiple_entries(self):
        bakeoff_sheet.build([entry('e1'), entry('e2')])
        h = self.html()
        self.assertIn('<h2>1. e1</h2>', h)
        self.assertIn('<h2>2. e2</h2>', h)

    def test_color_wraps_for_many_keys(self):
        keys = [f'k{i}' for i in range(12)]
        bakeoff_sheet.build([entry('e1', key=keys)])
        self.assertIn(f'border-left-color:{bakeoff_sheet.COLORS[0]}">11. k10', self.html().replace('\n', ''))

    def test_prints_path(self):
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            bakeoff_sheet.build([entry()])
        self.assertIn(f'wrote {self.sheet}', buf.getvalue())


class MainTests(Base):
    def setUp(self):
        super().setUp()
        json.dump([entry('e1'), entry('e2')], open(os.path.join(self.dir, 'entries.json'), 'w'))

    def run_main(self, argv):
        with mock.patch('sys.argv', ['bakeoff_sheet.py'] + argv), mock.patch('sys.stdout', io.StringIO()), \
                mock.patch.object(bakeoff_sheet, 'run_locate') as rl:
            bakeoff_sheet.main()
        return rl

    def test_plain_run_builds_page_only(self):
        rl = self.run_main([])
        rl.assert_not_called()
        self.assertIn('<section id="e2">', self.html())

    def test_locate_flag_passes_model_and_redo(self):
        rl = self.run_main(['--locate', '--model', 'carbon', '--redo', 'e1,,e2'])
        self.assertEqual(rl.call_args.args[1:], ('carbon', {'e1', 'e2'}))
        self.assertEqual(len(rl.call_args.args[0]), 2)
        self.assertTrue(os.path.exists(self.sheet))

    def test_locate_default_model_and_empty_redo(self):
        rl = self.run_main(['--locate'])
        self.assertEqual(rl.call_args.args[1:], ('argon', set()))

    def test_models_constant_matches_bakeoff(self):
        self.assertEqual(bakeoff_sheet.MODELS, bakeoff.DEFAULT_MODELS.split(','))


if __name__ == '__main__':
    unittest.main()
