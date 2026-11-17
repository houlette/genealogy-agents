#!/usr/bin/env python3
"""Report what the agents cost: the usage ledger, and Claude Code's own transcripts.

    python3 tools/usage.py                 # ledger: spend by week, role and model
    python3 tools/usage.py --claude        # Claude Code transcripts for this project, by day and model
    python3 tools/usage.py --claude --since 2026-11-12 --sessions

The ledger (usage-ledger.jsonl) gets one line per call from tools/llm.py and
per worker run from tools/dispatch.py. The --claude report reads the session
files Claude Code keeps under ~/.claude/projects and prices them at the list
prices in tools/models.json, so it shows what plan usage would have cost by
API. Mean context per turn is the figure to watch: a session costs roughly its
context size times its number of turns.
"""

import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402

CLAUDE_DIR = os.path.expanduser('~/.claude/projects/' + llm.ROOT.replace('/', '-'))
PRICE = {v['id']: v for v in llm.MODELS.values()}
PRICE['claude-fable-5-2'] = {'in': 8.0, 'out': 40.0, 'cached': 0.2}


def week(ts):
    import datetime
    d = datetime.date.fromisoformat(ts[:10])
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def ledger_report(since):
    if not os.path.exists(llm.LEDGER):
        print('no ledger yet:', llm.LEDGER)
        return
    rows = [json.loads(l) for l in open(llm.LEDGER) if l.strip()]
    rows = [r for r in rows if r['ts'][:10] >= since]
    agg = collections.defaultdict(collections.Counter)
    for r in rows:
        role = (r.get('task') or '').split(':')[0] or r.get('kind', '')
        a = agg[(week(r['ts']), role, r.get('backend', ''), r.get('model', ''))]
        a['runs'] += 1
        a['failed'] += not str(r.get('outcome', '')).startswith('ok')
        a['tokens'] += (r.get('tokens_in') or 0) + (r.get('tokens_out') or 0)
        a['cost'] += r.get('cost') or 0
        a['list'] += r.get('list_cost') or r.get('cost') or 0
    print(f'{"week of":11} {"role":12} {"backend":11} {"model":28} {"runs":>5} {"failed":>6} {"tokens":>11} {"paid $":>8} {"list $":>8}')
    for k in sorted(agg):
        a = agg[k]
        print(f'{k[0]:11} {k[1][:12]:12} {k[2][:11]:11} {k[3][:28]:28} {a["runs"]:5} {a["failed"]:6} {a["tokens"]:11,} {a["cost"]:8.2f} {a["list"]:8.2f}')
    print(f'total paid ${sum(a["cost"] for a in agg.values()):.2f}; "paid" is 0 for plan backends, "list" is the API list price of the same tokens')


def claude_report(since, sessions):
    files = sorted(set(glob.glob(CLAUDE_DIR + '/*.jsonl') + glob.glob(CLAUDE_DIR + '/**/*.jsonl', recursive=True)))
    by = collections.defaultdict(collections.Counter)
    per = []
    for f in files:
        sub = os.path.dirname(f) != CLAUDE_DIR
        seen, ctx, first, s = set(), [], None, collections.Counter()
        for line in open(f, errors='replace'):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get('type') != 'assistant':
                continue
            m = d.get('message') or {}
            ts = (d.get('timestamp') or '')[:10]
            if m.get('id') in seen or ts < since or (m.get('model') or '').startswith('<'):
                continue
            seen.add(m.get('id'))
            first = first or ts
            u = m.get('usage') or {}
            read, write = u.get('cache_read_input_tokens') or 0, u.get('cache_creation_input_tokens') or 0
            fresh, out = u.get('input_tokens') or 0, u.get('output_tokens') or 0
            p = PRICE.get(m.get('model'), {'in': 0, 'out': 0, 'cached': 0})
            cost = (read * p['cached'] + write * p['in'] * 1.25 + fresh * p['in'] + out * p['out']) / 1e6
            for key in ((ts, 'sub' if sub else 'main', m.get('model')),):
                c = by[key]
                c['turns'] += 1
                c['ctx'] += read + write + fresh
                c['out'] += out
                c['cost'] += cost
            ctx.append(read + write + fresh)
            s['out'] += out
            s['cost'] += cost
        if ctx:
            per.append((s['cost'], first, 'sub ' if sub else 'main', len(ctx), sum(ctx) // len(ctx), max(ctx), os.path.basename(f)[:20]))
    print(f'{"day":11} {"kind":5} {"model":20} {"turns":>6} {"mean ctx":>9} {"in M":>8} {"out M":>6} {"list $":>8}')
    tot = collections.Counter()
    for k in sorted(by):
        c = by[k]
        tot.update(c)
        print(f'{k[0]:11} {k[1]:5} {str(k[2])[:20]:20} {c["turns"]:6} {c["ctx"] // c["turns"]:9,} {c["ctx"] / 1e6:8.1f} {c["out"] / 1e6:6.2f} {c["cost"]:8.2f}')
    if tot['turns']:
        print(f'{"total":38} {tot["turns"]:6} {tot["ctx"] // tot["turns"]:9,} {tot["ctx"] / 1e6:8.1f} {tot["out"] / 1e6:6.2f} {tot["cost"]:8.2f}')
    if sessions:
        print('\nlargest sessions (list $, first day, kind, turns, mean ctx, max ctx, file)')
        for r in sorted(per, reverse=True)[:25]:
            print(f'{r[0]:8.2f} {r[1]} {r[2]} {r[3]:5} {r[4]:9,} {r[5]:9,} {r[6]}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--claude', action='store_true')
    ap.add_argument('--since', default='2026-01-01')
    ap.add_argument('--sessions', action='store_true')
    a = ap.parse_args()
    claude_report(a.since, a.sessions) if a.claude else ledger_report(a.since)
