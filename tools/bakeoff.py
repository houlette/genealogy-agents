#!/usr/bin/env python3
"""Score models on reading this project's own records (the handwriting bake-off).

    python3 tools/bakeoff.py --estimate                      # cost estimate, no calls
    python3 tools/bakeoff.py --models opus,argon --limit 3   # a small trial
    python3 tools/bakeoff.py                                 # every model, every entry
    python3 tools/bakeoff.py --report                        # rebuild the summary from saved results

The test set is tools/bakeoff/entries.json. Each entry has a crop, neutral
context, a list of key fields from the project's reference reading, the names
the tree expects, and sometimes a decoy (a statement the entry does not
support). Each model reads the entry blind and then informed
(tools/transcribe.py). Scores:

- blind: share of key fields found in the blind reading.
- informed: the same, also counting what the model confirms as written in the
  second pass or revises.
- decoys accepted: how often the model said "yes" to the decoy. This measures
  how far expected names pull a reading.

Results are saved per entry and model under tools/bakeoff/results/, and a
finished pair is not run again, so an interrupted run can be resumed by
running the same command again. The models run side by side, one thread each.
A lost connection (the laptop slept or changed network) makes the run wait for
the network and repeat that reading; any other failure is tried twice.
--budget stops the run from starting new readings past a dollar amount.

Limits: matching is loose (case, accents and umlauts ignored; a number matches
as a whole token anywhere in the reading), so scores are approximate. Most
reference readings were made by one agent and are not checked here. The report
lists the key fields that no model found: check those against the image before
trusting the ranking, because the key may be the thing that is wrong.
"""

import argparse
import collections
import fcntl
import json
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402
import transcribe  # noqa: E402

ROOT = llm.ROOT
DIR = os.path.join(ROOT, 'tools', 'bakeoff')
RESULTS = os.path.join(DIR, 'results')
DEFAULT_MODELS = 'opus,sonnet,haiku,argon,carbon,flash,sol,terra,kimi,qwen'


def text_of(obj, skip=('uncertain',)):
    if isinstance(obj, dict):
        return ' '.join(text_of(v) for k, v in obj.items() if k not in skip)
    if isinstance(obj, list):
        return ' '.join(text_of(v) for v in obj)
    return '' if obj is None else str(obj)


def found(alternatives, haystack):
    for alt in alternatives.split('|'):
        n = transcribe.norm(alt)
        if not n:
            continue
        if n.isdigit():
            if re.search(rf'(?<![0-9]){n}(?![0-9])', haystack):
                return True
        elif n in haystack:
            return True
    return False


def score(entry, reading):
    if not reading.get('blind'):
        return None
    blind = transcribe.norm(text_of(reading['blind']))
    informed = reading.get('informed') or {}
    confirmed = ' '.join(str(x.get('as_written', '')) for x in informed.get('expected', []) if x.get('found') == 'yes')
    revised = ' '.join(str(x.get('now', '')) for x in informed.get('revisions', []))
    both = blind + ' ' + transcribe.norm(confirmed + ' ' + revised)
    decoy = None
    if entry.get('decoy'):
        d = transcribe.norm(entry['decoy'])
        for x in informed.get('expected', []):
            if transcribe.norm(x.get('item', '')) == d:
                decoy = x.get('found')
    return {'blind': [k for k in entry['key'] if found(k, blind)],
            'informed': [k for k in entry['key'] if found(k, both)],
            'decoy': decoy}


def result_path(entry, model):
    return os.path.join(RESULTS, f'{entry["id"]}__{model}.json')


MAX_FAILURES = 2      # tries per entry and model for a failure that is not a lost connection
MAX_NET_WAITS = 8   # lost connections tolerated per entry and model


def network_up():
    try:
        socket.create_connection(('openrouter.ai', 443), timeout=5).close()
        return True
    except OSError:
        return False


def wait_for_network():
    """Block until the network is back (the laptop slept, or changed network)."""
    while not network_up():
        time.sleep(30)
    time.sleep(10)


def save(path, r):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # never leaves a half-written result behind


def run_model(entries, model, effort, state):
    for entry in entries:
        path = result_path(entry, model)
        if os.path.exists(path):
            continue
        expect = entry['expect'] + ([entry['decoy']] if entry.get('decoy') else [])
        failures = waits = 0
        while True:
            with state['lock']:
                if state['budget'] is not None and state['spent'] >= state['budget']:
                    print(f'{model:8} stopped: budget of ${state["budget"]:.2f} reached', flush=True)
                    return
            try:
                r = transcribe.read(model, os.path.join(ROOT, entry['crop']),
                                    os.path.join(ROOT, entry['page']) if entry.get('page') else None,
                                    entry['register'], entry['place'], entry['year'], entry['language'],
                                    expect, entry.get('focus', ''), effort, task=f'bakeoff:{entry["id"]}')
            except Exception as e:  # a stream cut by sleep can surface as a raw transport error
                net = isinstance(e, OSError) or type(e).__module__.split('.')[0] in ('httpx', 'httpcore', 'requests', 'urllib3')
                r = {'model': model, 'blind': None, 'informed': None, 'cost': 0.0,
                     'errors': [f'{"connection error" if net else "unexpected error"}: {type(e).__name__}: {e}']}
            with state['lock']:
                state['spent'] += r['cost']
            status = 'FAILED ' + '; '.join(r['errors']) if r['errors'] else 'ok'
            print(f'{time.strftime("%H:%M:%S")} {entry["id"]:22} {model:8} ${r["cost"]:.4f} {status[:100]}', flush=True)
            if not r['errors']:
                save(path, r)
                break
            if all('connection error' in e for e in r['errors']) and waits < MAX_NET_WAITS:
                # Sleep or a dropped network, not the model's fault: wait and
                # run the pair again without counting it as a failure.
                waits += 1
                wait_for_network()
                continue
            failures += 1
            if failures >= MAX_FAILURES:
                if r['blind'] is not None:
                    save(path, r)  # blind reading without the informed pass
                break  # otherwise no result file: a failed call is not a result, and the next run retries it


def run(entries, models, effort, budget=None):
    """One thread per model, each working through the entries in order.

    Safe to interrupt (sleep, lost network, power loss): finished pairs are
    kept, and running the same command again does only what is missing.
    """
    os.makedirs(RESULTS, exist_ok=True)
    lock_file = open(os.path.join(DIR, 'run.lock'), 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit('another bake-off run is in progress (tools/bakeoff/run.lock is held)')
    state = {'lock': threading.Lock(), 'spent': 0.0, 'budget': budget}
    threads = [threading.Thread(target=run_model, args=(entries, m, effort, state)) for m in models]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f'run finished: ${state["spent"]:.2f} spent in this run', flush=True)


def report(entries, models):
    rows = collections.defaultdict(lambda: collections.Counter())
    by_script = collections.defaultdict(lambda: collections.Counter())
    hits = collections.defaultdict(set)
    seen = collections.Counter()
    for entry in entries:
        for model in models:
            path = result_path(entry, model)
            if not os.path.exists(path):
                continue
            reading = json.load(open(path))
            s = score(entry, reading)
            if not s:
                continue
            seen[entry['id']] += 1
            r = rows[model]
            r['entries'] += 1
            r['keys'] += len(entry['key'])
            r['blind'] += len(s['blind'])
            r['informed'] += len(s['informed'])
            r['cost'] += reading['cost']
            if entry.get('decoy'):
                r['decoys'] += 1
                r['decoy_yes'] += s['decoy'] == 'yes'
                r['decoy_unanswered'] += s['decoy'] is None
            b = by_script[(model, entry['script'])]
            b['keys'] += len(entry['key'])
            b['blind'] += len(s['blind'])
            for k in s['informed']:
                hits[(entry['id'], k)].add(model)
    if not rows:
        print('no results yet')
        return
    scripts = sorted({e['script'] for e in entries})
    pct = lambda a, b: f'{100 * a / b:.0f}%' if b else '-'
    out = ['Handwriting bake-off results. Scores are approximate; see the limits in `tools/bakeoff.py`.', '',
           '| Model | Entries | Blind | Informed | Decoys accepted | Cost per entry | ' + ' | '.join(scripts) + ' |',
           '|---|---|---|---|---|---|' + '---|' * len(scripts)]
    for model in sorted(rows, key=lambda m: -rows[m]['blind'] / max(rows[m]['keys'], 1)):
        r = rows[model]
        cells = [pct(by_script[(model, s)]['blind'], by_script[(model, s)]['keys']) for s in scripts]
        out.append(f'| {model} ({llm.MODELS[model]["id"]}) | {r["entries"]} | {pct(r["blind"], r["keys"])} | '
                   f'{pct(r["informed"], r["keys"])} | {r["decoy_yes"]} of {r["decoys"]} | '
                   f'${r["cost"] / r["entries"]:.4f} | ' + ' | '.join(cells) + ' |')
    out += ['', 'The script columns give the blind score for that script.', '',
            'Key fields that no model found (check the key against the image before trusting the ranking):', '']
    none = [(e['id'], k) for e in entries if seen[e['id']] >= 2 for k in e['key'] if not hits[(e['id'], k)]]
    out += [f'- {i}: "{k}"' for i, k in none] or ['- none']
    text = '\n'.join(out) + '\n'
    open(os.path.join(DIR, 'summary.md'), 'w').write(text)
    print(text)


def estimate(entries, models):
    from PIL import Image
    total = 0
    print('Estimate only: image tokens are guessed at pixels/750 after downscaling, with 3,000 output tokens per pass.')
    for model in models:
        spec = llm.MODELS[model]
        cost = 0
        for e in entries:
            toks = 0
            for p in [e['crop']] + ([e['page']] if e.get('page') else []):
                w, h = Image.open(os.path.join(ROOT, p)).size
                f = min(1, 2000 / max(w, h))
                toks += w * f * h * f / 750
            tin = (toks + 700) + (toks + 1800)
            cost += (tin * spec['in'] + 6000 * spec['out']) / 1e6
        total += cost
        print(f'{model:8} {spec["id"]:28} about ${cost:.2f}')
    print(f'total about ${total:.2f} for {len(entries)} entries')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--models', default=DEFAULT_MODELS)
    ap.add_argument('--entries', help='comma-separated entry ids (default: all)')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--effort', default='high')
    ap.add_argument('--estimate', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--budget', type=float, help='stop starting new readings once this run has spent this many dollars')
    a = ap.parse_args()
    path = os.path.join(DIR, 'entries.json')
    if not os.path.exists(path):
        sys.exit(f'no test set at {path}; see the example in templates/tools/bakeoff/entries.json')
    with open(path, encoding='utf-8') as f:
        entries = json.load(f)
    if a.entries:
        entries = [e for e in entries if e['id'] in a.entries.split(',')]
    else:
        # An entry with a "skip" reason (for example, its crop was found not to
        # show the keyed entry) is left out of runs and scores unless named.
        entries = [e for e in entries if not e.get('skip')]
    if a.limit:
        entries = entries[:a.limit]
    models = [m for m in a.models.split(',') if m]
    unknown = [m for m in models if m not in llm.MODELS]
    if unknown:
        sys.exit(f'unknown models: {unknown}; see tools/models.json')
    if a.estimate:
        return estimate(entries, models)
    if not a.report:
        run(entries, models, a.effort, a.budget)
    report(entries, models)


if __name__ == '__main__':
    main()
