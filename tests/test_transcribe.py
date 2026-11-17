import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import llm  # noqa: E402
import transcribe  # noqa: E402

BLIND = {'entry_type': 'Marriage', 'dates': [{'what': 'marriage', 'as_written': '5. May 1730', 'normalized': '1730-05-05'}],
         'persons': [{'role': 'groom', 'name_as_written': 'Johann Georg Schmidt', 'name_normalized': 'Johann Georg Schmidt'}],
         'transcription': 'line one\nline two'}


def reply(obj, cost=0.01):
    return {'text': 'sure:\n' + json.dumps(obj), 'cost': cost, 'usage': {}, 'model': 'x', 'stop': 'end'}


class NormTests(unittest.TestCase):
    def test_accents_case_eszett_punctuation(self):
        self.assertEqual(transcribe.norm('Müller'), 'muller')
        self.assertEqual(transcribe.norm('GROSS'), transcribe.norm('Groß'))
        self.assertEqual(transcribe.norm('Kowalczyński'), 'kowalczynski')
        self.assertEqual(transcribe.norm('  Jo-hann, [?] Schmidt. '), 'jo hann schmidt')  # runs of spaces collapse

    def test_non_string_and_empty(self):
        self.assertEqual(transcribe.norm(1730), '1730')
        self.assertEqual(transcribe.norm(None), 'none')
        self.assertEqual(transcribe.norm(''), '')


class ReadTests(unittest.TestCase):
    def test_blind_only_when_no_expect(self):
        with mock.patch.object(llm, 'call', return_value=reply(BLIND, 0.05)) as c:
            r = transcribe.read('opus', 'crop.jpg', page='page.jpg', register='KB', place='Ort', year=1730,
                                language='German', task='t')
        self.assertEqual(c.call_count, 1)
        self.assertEqual(r['blind'], BLIND)
        self.assertIsNone(r['informed'])
        self.assertEqual(r['errors'], [])
        self.assertAlmostEqual(r['cost'], 0.05)
        kw = c.call_args.kwargs
        self.assertEqual(kw['images'], ['crop.jpg', 'page.jpg'])
        self.assertEqual(kw['task'], 't:blind')
        prompt = c.call_args.args[2]
        self.assertIn('register: KB; place: Ort; year: 1730; language: German', prompt)
        self.assertIn('whole page', prompt)
        self.assertNotIn('expects', prompt)

    def test_blind_prompt_defaults_and_focus(self):
        with mock.patch.object(llm, 'call', return_value=reply(BLIND)) as c:
            transcribe.read('opus', 'crop.jpg', focus='second entry')
        prompt = c.call_args.args[2]
        self.assertIn('register: not given; place: not given; year: not given; language: not given', prompt)
        self.assertIn('Entry to read: second entry', prompt)
        self.assertEqual(c.call_args.kwargs['images'], ['crop.jpg'])

    def test_blind_then_informed_flow(self):
        informed = {'expected': [{'item': 'Peter Schmidt', 'found': 'yes', 'as_written': 'Petri'}], 'revisions': []}
        seq = [reply(BLIND, 0.05), reply(informed, 0.02)]
        with mock.patch.object(llm, 'call', side_effect=seq) as c:
            r = transcribe.read('opus', 'crop.jpg', page='page.jpg', expect='groom X; father Peter Schmidt ; ', task='tt')
        self.assertEqual(c.call_count, 2)
        self.assertEqual(r['informed'], informed)
        self.assertAlmostEqual(r['cost'], 0.07)
        first, second = c.call_args_list
        self.assertEqual(first.kwargs['task'], 'tt:blind')
        self.assertEqual(second.kwargs['task'], 'tt:informed')
        self.assertEqual(second.kwargs['images'], ['crop.jpg'])  # informed pass: crop only
        p2 = second.args[2]
        self.assertIn('- groom X\n- father Peter Schmidt', p2)
        self.assertIn('Johann Georg Schmidt', p2)  # its own blind reading is shown
        # the blind prompt must not contain the expectations
        self.assertNotIn('Peter Schmidt', first.args[2])

    def test_expect_as_list(self):
        seq = [reply(BLIND), reply({'expected': []})]
        with mock.patch.object(llm, 'call', side_effect=seq) as c:
            transcribe.read('opus', 'c.jpg', expect=['a', 'b'])
        self.assertIn('- a\n- b', c.call_args.args[2])

    def test_blind_failure_returns_early(self):
        with mock.patch.object(llm, 'call', side_effect=llm.LLMError('boom')) as c:
            r = transcribe.read('opus', 'c.jpg', expect='x')
        self.assertEqual(c.call_count, 1)
        self.assertIsNone(r['blind'])
        self.assertEqual(r['errors'], ['blind: boom'])

    def test_blind_unparseable_reply_is_an_error(self):
        with mock.patch.object(llm, 'call', return_value={'text': 'no json', 'cost': 0.1}):
            r = transcribe.read('opus', 'c.jpg')
        self.assertIsNone(r['blind'])
        self.assertTrue(r['errors'][0].startswith('blind: no JSON'))

    def test_informed_failure_keeps_blind(self):
        seq = [reply(BLIND, 0.05), llm.LLMError('rate limited')]
        with mock.patch.object(llm, 'call', side_effect=seq):
            r = transcribe.read('opus', 'c.jpg', expect='x')
        self.assertEqual(r['blind'], BLIND)
        self.assertIsNone(r['informed'])
        self.assertEqual(r['errors'], ['informed: rate limited'])
        self.assertAlmostEqual(r['cost'], 0.05)


def rd(model, blind=None, informed=None):
    return {'model': model, 'blind': blind, 'informed': informed}


class DifferencesTests(unittest.TestCase):
    def person(self, name, role='groom'):
        return {'role': role, 'name_as_written': name}

    def test_identical_readings_no_differences(self):
        a = {'entry_type': 'Marriage', 'dates': [{'normalized': '1730-05-05'}], 'persons': [self.person('Müller')]}
        b = {'entry_type': 'marriage', 'dates': [{'normalized': '1730-05-05'}], 'persons': [self.person('muller')]}
        self.assertEqual(transcribe.differences([rd('a', a), rd('b', b)]), [])

    def test_entry_type_and_dates_differ(self):
        a = {'entry_type': 'baptism', 'dates': [{'normalized': '1730-05-05'}], 'persons': []}
        b = {'entry_type': 'burial', 'dates': [{'as_written': '5 May'}], 'persons': []}
        lines = transcribe.differences([rd('a', a), rd('b', b)])
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith('entry type:'))
        self.assertTrue(lines[1].startswith('dates:'))

    def test_person_read_by_one_model_only(self):
        a = {'persons': [self.person('Anna'), self.person('Peter', 'father')]}
        b = {'persons': [self.person('Anna')]}
        lines = transcribe.differences([rd('a', a), rd('b', b)])
        self.assertEqual(lines, ['person "peter" (father): read by a only'])

    def test_name_normalized_preferred_over_as_written(self):
        a = {'persons': [{'role': 'x', 'name_normalized': 'Anna', 'name_as_written': 'Annä'}]}
        b = {'persons': [{'role': 'x', 'name_as_written': 'Anna'}]}
        self.assertEqual(transcribe.differences([rd('a', a), rd('b', b)]), [])

    def test_informed_non_yes_is_reported(self):
        a = {'persons': []}
        inf = {'expected': [{'item': 'Peter', 'found': 'yes'}, {'item': 'Paul', 'found': 'no', 'as_written': 'Pauli'}]}
        lines = transcribe.differences([rd('a', a, inf)])
        self.assertEqual(lines, ['a: expected "Paul" -> no; sees "Pauli"'])

    def test_failed_readings_ignored(self):
        a = {'entry_type': 'marriage', 'persons': []}
        self.assertEqual(transcribe.differences([rd('a', a), rd('b', None)]), [])
        self.assertEqual(transcribe.differences([]), [])


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_main(self, argv, side_effect):
        buf = io.StringIO()
        with mock.patch.object(llm, 'call', side_effect=side_effect), mock.patch('sys.argv', ['transcribe.py'] + argv), \
                mock.patch('sys.stdout', buf):
            try:
                transcribe.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_two_models_with_differences_and_out_file(self):
        other = dict(BLIND, entry_type='burial')
        out = os.path.join(self.tmp.name, 'r.json')
        code, text = self.run_main(['crop.jpg', '--models', 'opus,argon', '--out', out, '--year', '1730'],
                                   [reply(BLIND), reply(other)])
        self.assertEqual(code, 0)
        self.assertIn('== opus', text)
        self.assertIn('== argon', text)
        self.assertIn('== differences ==', text)
        self.assertIn('entry type', text)
        saved = json.load(open(out))
        self.assertEqual([r['model'] for r in saved['readings']], ['opus', 'argon'])
        self.assertEqual(saved['crop'], 'crop.jpg')
        self.assertTrue(saved['differences'])
        self.assertEqual(saved['context']['year'], '1730')

    def test_no_differences_message(self):
        code, text = self.run_main(['crop.jpg', '--models', 'opus,argon'], [reply(BLIND), reply(BLIND)])
        self.assertEqual(code, 0)
        self.assertIn('no differences in entry type, dates or names', text)

    def test_error_exit_code_and_message(self):
        code, text = self.run_main(['crop.jpg', '--models', 'opus'], [llm.LLMError('down')])
        self.assertEqual(code, 1)
        self.assertIn('FAILED blind: down', text)

    def test_expect_passes_through_to_second_call(self):
        informed = {'expected': [{'item': 'X', 'found': 'no', 'as_written': 'Y'}]}
        code, text = self.run_main(['crop.jpg', '--models', 'opus', '--expect', 'X'], [reply(BLIND), reply(informed)])
        self.assertEqual(code, 0)
        self.assertIn('expected "X" -> no', text)


if __name__ == '__main__':
    unittest.main()
