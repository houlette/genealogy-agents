import fcntl
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm  # noqa: E402
import transcribe  # noqa: E402
import bakeoff  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None


def entry(id='e1', **kw):
    e = {'id': id, 'crop': f'crops/{id}.png', 'register': 'KB', 'place': 'Ort', 'year': 1730, 'language': 'German',
         'script': 'kurrent', 'key': ['Müller', '1730', 'Anna|Anne'], 'expect': ['groom Müller']}
    e.update(kw)
    return e


def reading(blind=None, informed=None, cost=0.01, errors=()):
    return {'model': 'm', 'blind': blind, 'informed': informed, 'cost': cost, 'errors': list(errors)}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.dir = os.path.join(t, 'tools', 'bakeoff')
        self.results = os.path.join(self.dir, 'results')
        os.makedirs(self.dir)
        for p in (mock.patch.object(bakeoff, 'ROOT', t), mock.patch.object(bakeoff, 'DIR', self.dir),
                  mock.patch.object(bakeoff, 'RESULTS', self.results),
                  mock.patch.object(bakeoff, 'wait_for_network'), mock.patch.object(bakeoff, 'network_up', return_value=True),
                  mock.patch.object(llm, 'LEDGER', os.path.join(t, 'ledger.jsonl')), mock.patch('time.sleep')):
            p.start()
            self.addCleanup(p.stop)

    def state(self, budget=None):
        return {'lock': threading.Lock(), 'spent': 0.0, 'budget': budget}


class TextOfAndFoundTests(unittest.TestCase):
    def test_text_of_flattens_and_skips_uncertain(self):
        obj = {'a': 'x', 'uncertain': ['hidden'], 'b': [{'c': 'y'}, None, 3], 'd': None}
        t = bakeoff.text_of(obj)
        self.assertIn('x', t)
        self.assertIn('y', t)
        self.assertIn('3', t)
        self.assertNotIn('hidden', t)
        self.assertNotIn('None', t)

    def test_text_of_skip_override(self):
        self.assertIn('hidden', bakeoff.text_of({'uncertain': ['hidden']}, skip=()))

    def test_found_accents_and_case(self):
        hay = transcribe.norm('Johann MÜLLER of Ort')
        self.assertTrue(bakeoff.found('Müller', hay))
        self.assertTrue(bakeoff.found('muller', hay))
        self.assertTrue(bakeoff.found('Groß', transcribe.norm('Gross')))

    def test_found_alternatives(self):
        hay = transcribe.norm('Anne Müller')
        self.assertTrue(bakeoff.found('Anna|Anne', hay))
        self.assertFalse(bakeoff.found('Anna|Maria', hay))
        self.assertTrue(bakeoff.found('|Anne|', hay))  # empty alternatives ignored

    def test_found_numbers_are_whole_tokens(self):
        self.assertTrue(bakeoff.found('1730', transcribe.norm('Anno 1730.')))
        self.assertTrue(bakeoff.found('1730', transcribe.norm('17.30')) is False)
        self.assertFalse(bakeoff.found('173', transcribe.norm('year 1730')))
        self.assertFalse(bakeoff.found('30', transcribe.norm('17301')))
        self.assertTrue(bakeoff.found('5', transcribe.norm('on 5 May')))
        self.assertFalse(bakeoff.found('5', transcribe.norm('on 15 May')))

    def test_found_ignores_punctuation_and_spacing_runs(self):
        self.assertTrue(bakeoff.found('Müller Johann', transcribe.norm('Müller, Johann')))
        self.assertTrue(bakeoff.found('Johann  Georg', transcribe.norm('Johann [?] Georg')))

    def test_found_words_match_as_substrings(self):
        self.assertTrue(bakeoff.found('Schmid', transcribe.norm('Schmidt')))

    def test_found_empty_key(self):
        self.assertFalse(bakeoff.found('', 'anything'))
        self.assertFalse(bakeoff.found('?!', 'anything'))


class ScoreTests(unittest.TestCase):
    def test_no_blind_returns_none(self):
        self.assertIsNone(bakeoff.score(entry(), reading(blind=None)))
        self.assertIsNone(bakeoff.score(entry(), {'blind': {}}))

    def test_blind_scoring(self):
        r = reading(blind={'persons': [{'name_as_written': 'Johann Müller'}], 'dates': [{'as_written': 'Anno 1730'}]})
        s = bakeoff.score(entry(), r)
        self.assertEqual(s['blind'], ['Müller', '1730'])
        self.assertEqual(s['informed'], ['Müller', '1730'])
        self.assertIsNone(s['decoy'])

    def test_uncertain_field_ignored_in_blind(self):
        r = reading(blind={'persons': [], 'uncertain': ['Müller 1730 Anna']})
        self.assertEqual(bakeoff.score(entry(), r)['blind'], [])

    def test_informed_counts_confirmed_and_revised(self):
        r = reading(blind={'persons': []},
                    informed={'expected': [{'item': 'x', 'found': 'yes', 'as_written': 'Müller'},
                                           {'item': 'y', 'found': 'no', 'as_written': '1730'},
                                           {'item': 'z', 'found': 'uncertain', 'as_written': 'Anna'}],
                              'revisions': [{'field': 'f', 'was': 'a', 'now': 'Anne'}]})
        s = bakeoff.score(entry(), r)
        self.assertEqual(s['blind'], [])
        self.assertEqual(s['informed'], ['Müller', 'Anna|Anne'])  # "no" and "uncertain" confirmations do not count

    def test_decoy_answer_recorded(self):
        e = entry(decoy='groom Peter Fake')
        informed = {'expected': [{'item': 'groom Müller', 'found': 'yes', 'as_written': 'Müller'},
                                 {'item': 'Groom  peter FAKE', 'found': 'yes', 'as_written': 'Fake'}]}
        s = bakeoff.score(e, reading(blind={'persons': []}, informed=informed))
        self.assertEqual(s['decoy'], 'yes')

    def test_decoy_unanswered_and_rejected(self):
        e = entry(decoy='groom Peter Fake')
        self.assertIsNone(bakeoff.score(e, reading(blind={'persons': []}, informed={'expected': []}))['decoy'])
        informed = {'expected': [{'item': 'groom Peter Fake', 'found': 'no', 'as_written': ''}]}
        self.assertEqual(bakeoff.score(e, reading(blind={'persons': []}, informed=informed))['decoy'], 'no')

    def test_decoy_text_does_not_inflate_blind_score(self):
        # A "yes" on the decoy with as_written text must still count only if it matches a key.
        e = entry(decoy='groom Peter Fake', key=['Fake'])
        informed = {'expected': [{'item': 'groom Peter Fake', 'found': 'no', 'as_written': 'Fake'}]}
        s = bakeoff.score(e, reading(blind={'persons': []}, informed=informed))
        self.assertEqual(s['informed'], [])


class ResultPathAndSaveTests(Base):
    def test_result_path(self):
        self.assertEqual(bakeoff.result_path(entry('abc'), 'opus'), os.path.join(self.results, 'abc__opus.json'))

    def test_save_atomic_and_unicode(self):
        os.makedirs(self.results)
        p = os.path.join(self.results, 'x.json')
        bakeoff.save(p, {'name': 'Müller'})
        self.assertEqual(json.load(open(p, encoding='utf-8')), {'name': 'Müller'})
        self.assertIn('Müller', open(p, encoding='utf-8').read())
        self.assertEqual(os.listdir(self.results), ['x.json'])  # no .tmp left


class RunModelTests(Base):
    def setUp(self):
        super().setUp()
        os.makedirs(self.results)

    def run_model(self, entries, model='opus', budget=None, side_effect=None, return_value=None):
        st = self.state(budget)
        with mock.patch.object(transcribe, 'read', side_effect=side_effect, return_value=return_value) as rd, \
                mock.patch('sys.stdout', io.StringIO()):
            bakeoff.run_model(entries, model, 'high', st)
        return rd, st

    def test_saves_result_and_passes_arguments(self):
        e = entry('e1', page='pages/e1.png', focus='second', decoy='fake')
        rd, st = self.run_model([e], return_value=reading({'persons': []}, cost=0.05))
        self.assertTrue(os.path.exists(bakeoff.result_path(e, 'opus')))
        a = rd.call_args
        self.assertEqual(a.args[0], 'opus')
        self.assertEqual(a.args[1], os.path.join(self.tmp.name, 'crops/e1.png'))
        self.assertEqual(a.args[2], os.path.join(self.tmp.name, 'pages/e1.png'))
        self.assertEqual(a.args[3:7], ('KB', 'Ort', 1730, 'German'))
        self.assertEqual(a.args[7], ['groom Müller', 'fake'])  # decoy appended to expectations
        self.assertEqual(a.args[8:10], ('second', 'high'))
        self.assertEqual(a.kwargs['task'], 'bakeoff:e1')
        self.assertAlmostEqual(st['spent'], 0.05)

    def test_no_page_passes_none_and_no_decoy(self):
        rd, _ = self.run_model([entry()], return_value=reading({'persons': []}))
        self.assertIsNone(rd.call_args.args[2])
        self.assertEqual(rd.call_args.args[7], ['groom Müller'])

    def test_resume_skips_finished_pairs(self):
        e1, e2 = entry('e1'), entry('e2')
        bakeoff.save(bakeoff.result_path(e1, 'opus'), reading({'persons': []}))
        rd, _ = self.run_model([e1, e2], return_value=reading({'persons': []}))
        self.assertEqual(rd.call_count, 1)
        self.assertEqual(rd.call_args.kwargs['task'], 'bakeoff:e2')

    def test_budget_stops_before_next_reading(self):
        entries = [entry('e1'), entry('e2'), entry('e3')]
        rd, st = self.run_model(entries, budget=0.05, return_value=reading({'persons': []}, cost=0.06))
        self.assertEqual(rd.call_count, 1)
        self.assertFalse(os.path.exists(bakeoff.result_path(entries[1], 'opus')))

    def test_budget_none_means_unlimited(self):
        rd, _ = self.run_model([entry('e1'), entry('e2')], return_value=reading({'persons': []}, cost=1000))
        self.assertEqual(rd.call_count, 2)

    def test_failure_retried_once_then_no_file_when_no_blind(self):
        bad = reading(None, errors=['blind: HTTP 500'])
        rd, _ = self.run_model([entry()], return_value=bad)
        self.assertEqual(rd.call_count, bakeoff.MAX_FAILURES)
        self.assertFalse(os.path.exists(bakeoff.result_path(entry(), 'opus')))

    def test_failure_then_success_saves(self):
        seq = [reading(None, errors=['blind: boom']), reading({'persons': []})]
        rd, _ = self.run_model([entry()], side_effect=seq)
        self.assertEqual(rd.call_count, 2)
        self.assertTrue(os.path.exists(bakeoff.result_path(entry(), 'opus')))

    def test_informed_failure_keeps_blind_reading_after_retries(self):
        partial = reading({'persons': [{'name_as_written': 'Müller'}]}, errors=['informed: rate limited'])
        rd, _ = self.run_model([entry()], return_value=partial)
        self.assertEqual(rd.call_count, 2)
        saved = json.load(open(bakeoff.result_path(entry(), 'opus')))
        self.assertEqual(saved['errors'], ['informed: rate limited'])
        self.assertIsNotNone(saved['blind'])

    def test_connection_errors_wait_without_counting_as_failures(self):
        conn = reading(None, errors=['blind: connection error: dropped'])
        seq = [conn, conn, conn, reading({'persons': []})]
        with mock.patch.object(bakeoff, 'wait_for_network') as w:
            rd, _ = self.run_model([entry()], side_effect=seq)
        self.assertEqual(rd.call_count, 4)
        self.assertEqual(w.call_count, 3)
        self.assertTrue(os.path.exists(bakeoff.result_path(entry(), 'opus')))

    def test_connection_waits_are_limited(self):
        conn = reading(None, errors=['blind: connection error: dropped'])
        with mock.patch.object(bakeoff, 'wait_for_network') as w:
            rd, _ = self.run_model([entry()], return_value=conn)
        self.assertEqual(w.call_count, bakeoff.MAX_NET_WAITS)
        self.assertEqual(rd.call_count, bakeoff.MAX_NET_WAITS + bakeoff.MAX_FAILURES)
        self.assertFalse(os.path.exists(bakeoff.result_path(entry(), 'opus')))

    def test_raw_oserror_treated_as_connection_error(self):
        seq = [ConnectionResetError('reset'), reading({'persons': []})]
        with mock.patch.object(bakeoff, 'wait_for_network') as w:
            rd, _ = self.run_model([entry()], side_effect=seq)
        self.assertEqual(w.call_count, 1)
        self.assertTrue(os.path.exists(bakeoff.result_path(entry(), 'opus')))

    def test_other_exception_is_an_unexpected_failure_not_a_wait(self):
        with mock.patch.object(bakeoff, 'wait_for_network') as w:
            rd, _ = self.run_model([entry()], side_effect=ValueError('weird'))
        w.assert_not_called()
        self.assertEqual(rd.call_count, bakeoff.MAX_FAILURES)
        self.assertFalse(os.path.exists(bakeoff.result_path(entry(), 'opus')))


class NetworkTests(unittest.TestCase):
    def test_network_up_true_false(self):
        # Use the real function; mock the socket call so nothing leaves the machine.
        real = bakeoff.__dict__['network_up']
        sock = mock.Mock()
        with mock.patch('socket.create_connection', return_value=sock) as cc:
            self.assertTrue(real.__wrapped__() if hasattr(real, '__wrapped__') else real())
        cc.assert_called_once_with(('openrouter.ai', 443), timeout=5)
        sock.close.assert_called_once()
        with mock.patch('socket.create_connection', side_effect=OSError('down')):
            self.assertFalse(real())

    def test_wait_for_network_polls_until_up(self):
        with mock.patch.object(bakeoff, 'network_up', side_effect=[False, False, True]) as up, mock.patch('time.sleep') as sl:
            bakeoff.wait_for_network()
        self.assertEqual(up.call_count, 3)
        self.assertEqual([c.args[0] for c in sl.call_args_list], [30, 30, 10])


class RunTests(Base):
    def test_threads_per_model_and_lock(self):
        entries = [entry('e1'), entry('e2')]
        with mock.patch.object(transcribe, 'read', return_value=reading({'persons': []}, cost=0.1)) as rd, \
                mock.patch('sys.stdout', io.StringIO()) as out:
            bakeoff.run(entries, ['opus', 'argon'], 'high')
        self.assertEqual(rd.call_count, 4)
        for m in ('opus', 'argon'):
            for e in entries:
                self.assertTrue(os.path.exists(bakeoff.result_path(e, m)))
        self.assertIn('run finished: $0.40 spent in this run', out.getvalue())

    def test_second_run_does_nothing(self):
        entries = [entry('e1')]
        with mock.patch.object(transcribe, 'read', return_value=reading({'persons': []})) as rd, mock.patch('sys.stdout', io.StringIO()):
            bakeoff.run(entries, ['opus'], 'high')
            bakeoff.run(entries, ['opus'], 'high')
        self.assertEqual(rd.call_count, 1)

    def test_refuses_when_lock_is_held(self):
        os.makedirs(self.results)
        holder = open(os.path.join(self.dir, 'run.lock'), 'w')
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mock.patch.object(transcribe, 'read') as rd:
            with self.assertRaises(SystemExit) as cm:
                bakeoff.run([entry()], ['opus'], 'high')
        self.assertIn('another bake-off run is in progress', str(cm.exception))
        rd.assert_not_called()

    def test_budget_shared_across_models(self):
        entries = [entry(f'e{i}') for i in range(6)]
        with mock.patch.object(transcribe, 'read', return_value=reading({'persons': []}, cost=1.0)) as rd, \
                mock.patch('sys.stdout', io.StringIO()):
            bakeoff.run(entries, ['opus', 'argon'], 'high', budget=2.0)
        self.assertLess(rd.call_count, 12)
        self.assertGreaterEqual(rd.call_count, 2)


class ReportTests(Base):
    def setUp(self):
        super().setUp()
        os.makedirs(self.results)

    def put(self, e, model, **kw):
        bakeoff.save(bakeoff.result_path(e, model), reading(**kw))

    def report(self, entries, models):
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            bakeoff.report(entries, models)
        return buf.getvalue()

    def test_no_results(self):
        self.assertIn('no results yet', self.report([entry()], ['opus']))
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'summary.md')))

    def test_table_scores_ranking_and_summary_file(self):
        e1 = entry('e1', script='kurrent', decoy='fake')
        e2 = entry('e2', script='latin', key=['Anna'], expect=[])
        good = {'persons': [{'name_as_written': 'Müller'}], 'dates': [{'as_written': '1730'}, {'as_written': 'Anna'}]}
        weak = {'persons': [{'name_as_written': 'Müller'}]}
        self.put(e1, 'opus', blind=good, informed={'expected': [{'item': 'fake', 'found': 'yes', 'as_written': 'x'}]}, cost=0.10)
        self.put(e2, 'opus', blind=good, cost=0.20)
        self.put(e1, 'haiku', blind=weak, informed={'expected': [{'item': 'fake', 'found': 'no', 'as_written': ''}]}, cost=0.01)
        self.put(e2, 'haiku', blind={'persons': []}, cost=0.01)
        out = self.report([e1, e2], ['haiku', 'opus'])
        lines = out.splitlines()
        rows = [l for l in lines if l.startswith('| opus') or l.startswith('| haiku')]
        self.assertTrue(rows[0].startswith('| opus (claude-opus-5-5)'))  # ranked by blind score
        opus = [c.strip() for c in rows[0].strip('|').split('|')]
        # model, entries, blind, informed, decoys, cost/entry, kurrent, latin
        self.assertEqual(opus[1:], ['2', '100%', '100%', '1 of 1', '$0.1500', '100%', '100%'])
        haiku = [c.strip() for c in rows[1].strip('|').split('|')]
        self.assertEqual(haiku[1:5], ['2', '25%', '25%', '0 of 1'])  # 1 of 4 keys: Müller
        self.assertEqual(haiku[6:], ['33%', '0%'])
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'summary.md')))
        self.assertEqual(open(os.path.join(self.dir, 'summary.md')).read() + '\n', out)

    def test_unfound_keys_listed_only_when_two_models_read_the_entry(self):
        e1 = entry('e1', key=['Müller', 'Zzyzx'])
        e2 = entry('e2', key=['Nobody'])
        self.put(e1, 'opus', blind={'persons': [{'name_as_written': 'Müller'}]})
        self.put(e1, 'haiku', blind={'persons': []})
        self.put(e2, 'opus', blind={'persons': []})  # only one model: not listed
        out = self.report([e1, e2], ['opus', 'haiku'])
        self.assertIn('- e1: "Zzyzx"', out)
        self.assertNotIn('Nobody', out)
        self.assertNotIn('- e1: "Müller"', out)

    def test_none_found_message(self):
        e1 = entry('e1', key=['Müller'])
        self.put(e1, 'opus', blind={'persons': [{'name_as_written': 'Müller'}]})
        self.put(e1, 'haiku', blind={'persons': [{'name_as_written': 'Müller'}]})
        self.assertIn('- none', self.report([e1], ['opus', 'haiku']))

    def test_failed_readings_without_blind_are_skipped(self):
        e1 = entry('e1')
        self.put(e1, 'opus', blind=None, errors=['blind: x'])
        self.assertIn('no results yet', self.report([e1], ['opus']))

    def test_missing_files_skipped(self):
        e1, e2 = entry('e1'), entry('e2')
        self.put(e1, 'opus', blind={'persons': []})
        out = self.report([e1, e2], ['opus', 'haiku'])
        self.assertIn('| opus (claude-opus-5-5) | 1 |', out)
        self.assertNotIn('| haiku', out)


@unittest.skipIf(Image is None, 'Pillow missing')
class EstimateTests(Base):
    def test_estimate_arithmetic(self):
        os.makedirs(os.path.join(self.tmp.name, 'crops'))
        Image.new('RGB', (750, 100), 'white').save(os.path.join(self.tmp.name, 'crops', 'e1.png'))
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            bakeoff.estimate([entry('e1')], ['haiku'])
        toks = 750 * 100 / 750  # 100 tokens, no downscale
        tin = (toks + 700) + (toks + 1800)
        spec = llm.MODELS['haiku']
        cost = (tin * spec['in'] + 6000 * spec['out']) / 1e6
        self.assertIn(f'about ${cost:.2f}', buf.getvalue())
        self.assertIn('for 1 entries', buf.getvalue())

    def test_downscaling_and_page_counted(self):
        os.makedirs(os.path.join(self.tmp.name, 'crops'))
        Image.new('RGB', (4000, 1000), 'white').save(os.path.join(self.tmp.name, 'crops', 'e1.png'))
        Image.new('RGB', (4000, 1000), 'white').save(os.path.join(self.tmp.name, 'crops', 'p.png'))
        e = entry('e1', page='crops/p.png')
        buf = io.StringIO()
        with mock.patch('sys.stdout', buf):
            bakeoff.estimate([e], ['opus'])
        toks = 2 * (2000 * 500 / 750)
        tin = (toks + 700) + (toks + 1800)
        spec = llm.MODELS['opus']
        self.assertIn(f'about ${(tin * spec["in"] + 6000 * spec["out"]) / 1e6:.2f}', buf.getvalue())


class MainTests(Base):
    def setUp(self):
        super().setUp()
        self.entries = [entry('e1'), entry('e2', skip='crop shows another entry'), entry('e3')]
        json.dump(self.entries, open(os.path.join(self.dir, 'entries.json'), 'w'))

    def run_main(self, argv):
        buf = io.StringIO()
        code = None
        with mock.patch('sys.argv', ['bakeoff.py'] + argv), mock.patch('sys.stdout', buf), \
                mock.patch.object(bakeoff, 'run') as run, mock.patch.object(bakeoff, 'report') as rep, \
                mock.patch.object(bakeoff, 'estimate') as est:
            try:
                bakeoff.main()
            except SystemExit as e:
                code = e.code
        return code, run, rep, est

    def test_default_skips_skipped_entries(self):
        _, run, rep, _ = self.run_main(['--models', 'opus,haiku'])
        ids = [e['id'] for e in run.call_args.args[0]]
        self.assertEqual(ids, ['e1', 'e3'])
        self.assertEqual(run.call_args.args[1:], (['opus', 'haiku'], 'high', None))
        rep.assert_called_once()

    def test_named_entries_include_skipped(self):
        _, run, _, _ = self.run_main(['--models', 'opus', '--entries', 'e2,e3'])
        self.assertEqual([e['id'] for e in run.call_args.args[0]], ['e2', 'e3'])

    def test_limit_and_budget_and_effort(self):
        _, run, _, _ = self.run_main(['--models', 'opus', '--limit', '1', '--budget', '2.5', '--effort', 'low'])
        self.assertEqual([e['id'] for e in run.call_args.args[0]], ['e1'])
        self.assertEqual(run.call_args.args[2:], ('low', 2.5))

    def test_report_only_does_not_run(self):
        _, run, rep, _ = self.run_main(['--models', 'opus', '--report'])
        run.assert_not_called()
        rep.assert_called_once()

    def test_estimate_only(self):
        _, run, rep, est = self.run_main(['--models', 'opus', '--estimate'])
        est.assert_called_once()
        run.assert_not_called()
        rep.assert_not_called()

    def test_unknown_model_exits(self):
        code, run, _, _ = self.run_main(['--models', 'opus,notamodel'])
        self.assertIn('unknown models', str(code))
        run.assert_not_called()

    def test_default_models_all_exist(self):
        for m in bakeoff.DEFAULT_MODELS.split(','):
            self.assertIn(m, llm.MODELS)

    def test_trailing_comma_in_models_ignored(self):
        _, run, _, _ = self.run_main(['--models', 'opus,'])
        self.assertEqual(run.call_args.args[1], ['opus'])



class MissingTestSetTests(unittest.TestCase):
    def test_missing_entries_file_exits_with_a_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(bakeoff, 'DIR', tmp), mock.patch.object(sys, 'argv', ['bakeoff.py', '--estimate']):
                with self.assertRaises(SystemExit) as cm:
                    bakeoff.main()
        self.assertIn('no test set at', str(cm.exception))
        self.assertIn('templates/tools/bakeoff/entries.json', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
