import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)
import llm  # noqa: E402
import usage  # noqa: E402


def capture(fn, *a, **k):
    buf = io.StringIO()
    with mock.patch('sys.stdout', buf):
        fn(*a, **k)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = os.path.join(self.tmp.name, 'ledger.jsonl')
        self.cdir = os.path.join(self.tmp.name, 'claude')
        os.makedirs(self.cdir)
        for p in (mock.patch.object(llm, 'LEDGER', self.ledger), mock.patch.object(usage, 'CLAUDE_DIR', self.cdir)):
            p.start()
            self.addCleanup(p.stop)

    def write_ledger(self, rows):
        with open(self.ledger, 'w') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')


class WeekTests(unittest.TestCase):
    def test_monday_start(self):
        self.assertEqual(usage.week('2026-11-16T10:00:00'), '2026-11-16')  # a Monday
        self.assertEqual(usage.week('2026-11-22T23:59:59'), '2026-11-16')  # Sunday
        self.assertEqual(usage.week('2026-11-23T00:00:00'), '2026-11-23')

    def test_crosses_month_and_year(self):
        self.assertEqual(usage.week('2026-01-01T00:00:00'), '2025-12-29')

    def test_price_table_has_fable_and_models(self):
        self.assertEqual(usage.PRICE['claude-opus-5-5']['in'], 4.0)
        self.assertEqual(usage.PRICE['claude-fable-5-2']['out'], 40.0)


class LedgerReportTests(Base):
    def test_no_ledger(self):
        out = capture(usage.ledger_report, '2026-01-01')
        self.assertIn('no ledger yet', out)

    def test_grouping_by_week_role_backend_model(self):
        self.write_ledger([
            {'ts': '2026-11-16T09:00:00', 'kind': 'call', 'task': 'bakeoff:e1', 'backend': 'anthropic', 'model': 'claude-opus-5-5',
             'outcome': 'ok', 'tokens_in': 1000, 'tokens_out': 500, 'cost': 1.25, 'list_cost': 1.25},
            {'ts': '2026-11-18T09:00:00', 'kind': 'call', 'task': 'bakeoff:e2', 'backend': 'anthropic', 'model': 'claude-opus-5-5',
             'outcome': 'error: HTTP 500', 'tokens_in': 0, 'tokens_out': 0},
            {'ts': '2026-11-22T09:00:00', 'kind': 'worker', 'task': 'retrieve:b', 'backend': 'claude-plan', 'model': 'opus',
             'outcome': 'ok', 'tokens_in': 2000, 'tokens_out': 100, 'cost': 0.0, 'list_cost': 3.0},
            {'ts': '2026-11-24T09:00:00', 'kind': 'worker', 'task': 'retrieve:c', 'backend': 'claude-plan', 'model': 'opus',
             'outcome': 'quota', 'tokens_in': None, 'tokens_out': None, 'cost': None},
        ])
        out = capture(usage.ledger_report, '2026-01-01')
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith('week of'))
        rows = [l for l in lines[1:] if l[:2] == '20']
        self.assertEqual(len(rows), 3)
        first = rows[0].split()
        # week, role, backend, model, runs, failed, tokens, paid, list
        self.assertEqual(first, ['2026-11-16', 'bakeoff', 'anthropic', 'claude-opus-5-5', '2', '1', '1,500', '1.25', '1.25'])
        second = rows[1].split()
        self.assertEqual(second[:2], ['2026-11-16', 'retrieve'])
        self.assertEqual(second[4:], ['1', '0', '2,100', '0.00', '3.00'])
        third = rows[2].split()
        self.assertEqual(third[0], '2026-11-23')
        self.assertEqual(third[4:6], ['1', '1'])  # quota outcome counts as failed
        self.assertIn('total paid $1.25', lines[-1])

    def test_since_filter_and_blank_lines_and_missing_task(self):
        with open(self.ledger, 'w') as f:
            f.write(json.dumps({'ts': '2026-10-01T00:00:00', 'kind': 'call', 'cost': 9, 'outcome': 'ok'}) + '\n\n')
            f.write(json.dumps({'ts': '2026-11-20T00:00:00', 'kind': 'call', 'cost': 2, 'outcome': 'ok'}) + '\n')
        out = capture(usage.ledger_report, '2026-11-01')
        self.assertIn('total paid $2.00', out)
        self.assertEqual(len([l for l in out.splitlines() if l[:2] == '20']), 1)
        self.assertIn(' call ', out)  # role falls back to the kind

    def test_list_cost_falls_back_to_cost(self):
        self.write_ledger([{'ts': '2026-11-16T00:00:00', 'kind': 'call', 'task': 'x:1', 'outcome': 'ok', 'cost': 0.5}])
        row = [l for l in capture(usage.ledger_report, '2026-01-01').splitlines() if l[:2] == '20'][0].split()
        self.assertEqual(row[-2:], ['0.50', '0.50'])

    def test_real_ledger_not_touched(self):
        self.assertNotEqual(llm.LEDGER, os.path.join(llm.ROOT, 'usage-ledger.jsonl'))


def asst(id, ts, model, inp=0, out=0, read=0, write=0):
    return {'type': 'assistant', 'timestamp': ts,
            'message': {'id': id, 'model': model, 'usage': {'input_tokens': inp, 'output_tokens': out,
                                                             'cache_read_input_tokens': read, 'cache_creation_input_tokens': write}}}


class ClaudeReportTests(Base):
    def write(self, rel, rows, extra=''):
        path = os.path.join(self.cdir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            for r in rows:
                f.write(json.dumps(r) + '\n')
            f.write(extra)
        return path

    def test_cost_arithmetic_per_day_and_model(self):
        # opus-5-5: in 4, out 20, cached 0.2; cache write priced at 1.25 x in
        self.write('main.jsonl', [asst('m1', '2026-11-16T10:00:00Z', 'claude-opus-5-5', inp=1000000, out=100000, read=2000000, write=400000)])
        out = capture(usage.claude_report, '2026-01-01', False)
        expected = (2000000 * 0.2 + 400000 * 4 * 1.25 + 1000000 * 4 + 100000 * 20) / 1e6  # 0.4 + 2.0 + 4.0 + 2.0
        self.assertAlmostEqual(expected, 8.4)
        row = [l for l in out.splitlines() if l.startswith('2026-11-16')][0]
        self.assertIn('main', row)
        self.assertIn('8.40', row)
        self.assertIn('3,400,000', row)  # mean ctx = read + write + fresh
        total = [l for l in out.splitlines() if l.startswith('total')][0]
        self.assertIn('8.40', total)

    def test_duplicate_message_ids_counted_once(self):
        r = asst('dup', '2026-11-16T10:00:00Z', 'claude-opus-5-5', inp=1000000)
        self.write('main.jsonl', [r, r])
        out = capture(usage.claude_report, '2026-01-01', False)
        self.assertIn('4.00', [l for l in out.splitlines() if l.startswith('total')][0])
        self.assertEqual([l for l in out.splitlines() if l.startswith('2026-11-16')][0].split()[3], '1')

    def test_since_filter_synthetic_models_and_non_assistant_and_bad_lines(self):
        self.write('main.jsonl', [asst('a', '2026-10-01T10:00:00Z', 'claude-opus-5-5', inp=1000000),
                                  asst('b', '2026-11-16T10:00:00Z', '<synthetic>', inp=1000000),
                                  {'type': 'user', 'timestamp': '2026-11-16T10:00:00Z'},
                                  asst('c', '2026-11-16T10:00:00Z', 'claude-haiku-5-5', inp=1000000)], extra='not json\n')
        out = capture(usage.claude_report, '2026-11-01', False)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith('2026-')]), 1)
        self.assertIn('0.10', [l for l in out.splitlines() if l.startswith('total')][0])

    def test_unknown_model_costs_zero_and_fable_priced(self):
        self.write('main.jsonl', [asst('a', '2026-11-16T10:00:00Z', 'mystery-1', inp=1000000, out=1000000),
                                  asst('b', '2026-11-17T10:00:00Z', 'claude-fable-5-2', out=1000000)])
        out = capture(usage.claude_report, '2026-01-01', False)
        rows = {l.split()[0]: l for l in out.splitlines() if l.startswith('2026-')}
        self.assertIn('0.00', rows['2026-11-16'])
        self.assertIn('40.00', rows['2026-11-17'])

    def test_subagent_files_labelled_sub(self):
        self.write('main.jsonl', [asst('a', '2026-11-16T10:00:00Z', 'claude-haiku-5-5', inp=10)])
        self.write('sess/subagents/agent-1.jsonl', [asst('s', '2026-11-16T11:00:00Z', 'claude-haiku-5-5', inp=10)])
        out = capture(usage.claude_report, '2026-01-01', False)
        kinds = sorted(l.split()[1] for l in out.splitlines() if l.startswith('2026-'))
        self.assertEqual(kinds, ['main', 'sub'])

    def test_sessions_listing_sorted_by_cost(self):
        self.write('cheap.jsonl', [asst('a', '2026-11-16T10:00:00Z', 'claude-haiku-5-5', inp=1000000)])
        self.write('dear.jsonl', [asst('b', '2026-11-17T10:00:00Z', 'claude-opus-5-5', inp=1000000),
                                  asst('c', '2026-11-17T10:05:00Z', 'claude-opus-5-5', inp=3000000)])
        out = capture(usage.claude_report, '2026-01-01', True)
        tail = out.split('largest sessions')[1].splitlines()[1:]
        self.assertTrue(tail[0].split()[-1].startswith('dear'))
        self.assertTrue(tail[1].split()[-1].startswith('cheap'))
        parts = tail[0].split()
        self.assertEqual(parts[0], '16.00')
        self.assertEqual(parts[3], '2')           # turns
        self.assertEqual(parts[4], '2,000,000')   # mean context
        self.assertEqual(parts[5], '3,000,000')   # max context

    def test_empty_directory_prints_header_only(self):
        out = capture(usage.claude_report, '2026-01-01', True)
        self.assertTrue(out.startswith('day'))
        self.assertNotIn('total', out)


class CliTests(Base):
    def run_cli(self, argv):
        buf = io.StringIO()
        with mock.patch.object(llm, 'ROOT', self.tmp.name), mock.patch('sys.argv', ['usage.py'] + argv), \
                mock.patch('sys.stdout', buf):
            runpy.run_path(os.path.join(TOOLS, 'usage.py'), run_name='__main__')
        return buf.getvalue()

    def test_default_is_ledger_report(self):
        self.write_ledger([{'ts': '2026-11-16T00:00:00', 'kind': 'call', 'task': 'x:1', 'outcome': 'ok', 'cost': 1}])
        out = self.run_cli(['--since', '2026-11-01'])
        self.assertIn('total paid $1.00', out)

    def test_claude_flag_reads_transcripts_under_root(self):
        d = os.path.join(os.path.expanduser('~/.claude/projects/' + self.tmp.name.replace('/', '-')))
        # CLAUDE_DIR is recomputed from the patched llm.ROOT; make sure we never create anything in the real home.
        self.assertFalse(os.path.exists(d))
        out = self.run_cli(['--claude', '--since', '2026-11-01', '--sessions'])
        self.assertTrue(out.startswith('day'))
        self.assertIn('largest sessions', out)


if __name__ == '__main__':
    unittest.main()
