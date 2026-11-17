#!/usr/bin/env python3
"""Read one record entry with two or more models, blind first, and show where they differ.

    python3 tools/transcribe.py sources/1730-...-crop.jpg \
        --page sources/1730-...-full.jpg \
        --register "Kirchenbuch, marriages" --place Musterdorf \
        --year 1730 --language German \
        --expect "groom Johann Georg Schmidt; groom's father Peter Schmidt" \
        --models opus,argon --out sources/1730-...-readings.json

Pass 1 (blind): each model sees the crop, the page and only neutral facts
(register, place, year, language), and reads the key fields. Pass 2 (informed):
the model is shown its own blind reading and the names the tree expects, and
says for each whether the image supports it. A test of several
prompting methods (1 November 2026) found blind-fields-first the only one
that improved accuracy; it also keeps an expected name from being
read into an unclear word.

The output is a set of machine readings, not evidence. A reading goes into
tree.ged only after the checks in AGENTS.md ("Check across agents").
"""

import argparse
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402

SYSTEM = """You are a palaeographer reading one entry in a historical record for a genealogy project.
Read only what is written. Never supply a name, date or word from expectation or from what such records usually say.
Where a word is unclear, give your best reading and mark it with [?]; where it is illegible, write [illegible].
Keep the original spelling in transcriptions. Reply with one JSON object and nothing else."""

BLIND = """The first image is a crop of the entry to read.{page_note}
Neutral facts: register: {register}; place: {place}; year: {year}; language: {language}.
{focus}
Return JSON with these keys:
- "entry_type": baptism, birth, marriage, burial, death, census, will, deed, or other
- "dates": list of {{"what": ..., "as_written": ..., "normalized": "YYYY-MM-DD or partial"}}
- "persons": list of {{"role": ..., "name_as_written": ..., "name_normalized": ..., "age": ..., "occupation_or_status": ..., "residence": ..., "confidence": "high|medium|low"}}
- "relationships": list of short statements the entry itself makes, such as "X is son of Y"
- "transcription": the entry line by line, original spelling
- "uncertain": list of the words or fields you are least sure of, with the alternatives you considered"""

INFORMED = """The image is the same entry. This was your own blind reading of it:
{blind}

The project's tree expects the following. For each item, look at the image again and say whether the entry supports it. An expectation can be wrong: say "no" when the image shows something else, and "uncertain" when the word could be read either way.
{expect}

Return JSON with these keys:
- "expected": list of {{"item": ..., "found": "yes|no|uncertain", "as_written": the exact letters you see, "note": ...}}
- "revisions": list of {{"field": ..., "was": ..., "now": ..., "why": ...}} for any blind reading you change after the second look (empty if none)"""


def norm(s):
    s = unicodedata.normalize('NFKD', str(s).lower().replace('ß', 'ss'))
    return ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', ''.join(c for c in s if not unicodedata.combining(c))).split())


def read(model, crop, page=None, register='', place='', year='', language='', expect=None,
         focus='', effort='high', task=''):
    """Return {'model', 'blind', 'informed', 'cost', 'errors'} for one model."""
    images = [crop] + ([page] if page else [])
    page_note = ' The second image is the whole page, reduced, for context such as headings and neighbouring entries.' if page else ''
    result = {'model': model, 'blind': None, 'informed': None, 'cost': 0.0, 'errors': []}
    try:
        r = llm.call(model, SYSTEM, BLIND.format(page_note=page_note, register=register or 'not given',
                                                 place=place or 'not given', year=year or 'not given',
                                                 language=language or 'not given',
                                                 focus=f'Entry to read: {focus}' if focus else ''),
                     images=images, effort=effort, task=f'{task}:blind')
        result['cost'] += r['cost']
        result['blind'] = llm.parse_json(r['text'])
    except llm.LLMError as e:
        result['errors'].append(f'blind: {e}')
        return result
    if expect:
        items = expect if isinstance(expect, list) else [x.strip() for x in expect.split(';') if x.strip()]
        try:
            r = llm.call(model, SYSTEM, INFORMED.format(blind=json.dumps(result['blind'], ensure_ascii=False, indent=1),
                                                        expect='\n'.join(f'- {x}' for x in items)),
                         images=[crop], effort=effort, task=f'{task}:informed')
            result['cost'] += r['cost']
            result['informed'] = llm.parse_json(r['text'])
        except llm.LLMError as e:
            result['errors'].append(f'informed: {e}')
    return result


def differences(readings):
    """Lines describing where the blind readings disagree."""
    ok = [r for r in readings if r['blind']]
    lines = []
    types = {r['model']: norm(r['blind'].get('entry_type', '')) for r in ok}
    if len(set(types.values())) > 1:
        lines.append(f'entry type: {types}')
    dates = {r['model']: sorted({d.get('normalized') or d.get('as_written') or '' for d in r['blind'].get('dates', [])}) for r in ok}
    if len({tuple(v) for v in dates.values()}) > 1:
        lines.append(f'dates: {dates}')
    names = {r['model']: {norm(p.get('name_normalized') or p.get('name_as_written') or ''): p for p in r['blind'].get('persons', [])} for r in ok}
    every = set().union(*[set(v) for v in names.values()]) if names else set()
    for n in sorted(every):
        have = [m for m, v in names.items() if n in v]
        if len(have) < len(names):
            role = next(v[n].get('role') for v in names.values() if n in v)
            lines.append(f'person "{n}" ({role}): read by {", ".join(have)} only')
    for r in ok:
        for item in (r['informed'] or {}).get('expected', []):
            if item.get('found') != 'yes':
                lines.append(f'{r["model"]}: expected "{item.get("item")}" -> {item.get("found")}; sees "{item.get("as_written")}"')
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('crop')
    ap.add_argument('--page')
    ap.add_argument('--register', default='')
    ap.add_argument('--place', default='')
    ap.add_argument('--year', default='')
    ap.add_argument('--language', default='')
    ap.add_argument('--focus', default='', help='which entry on the crop, if it shows more than one')
    ap.add_argument('--expect', default='', help='names and facts the tree expects, separated by semicolons')
    ap.add_argument('--models', default='opus,argon')
    ap.add_argument('--effort', default='high')
    ap.add_argument('--out', help='write the full readings here as JSON')
    a = ap.parse_args()
    readings = [read(m, a.crop, a.page, a.register, a.place, a.year, a.language, a.expect, a.focus, a.effort,
                     task=f'transcribe:{os.path.basename(a.crop)}') for m in a.models.split(',')]
    for r in readings:
        print(f'\n== {r["model"]} (${r["cost"]:.4f}) ==')
        for e in r['errors']:
            print(f'FAILED {e}')
        if r['blind']:
            print(r['blind'].get('transcription', ''))
    diffs = differences(readings)
    print('\n== differences ==' if diffs else '\n== no differences in entry type, dates or names ==')
    print('\n'.join(diffs))
    if a.out:
        json.dump({'crop': a.crop, 'page': a.page, 'context': vars(a), 'readings': readings, 'differences': diffs},
                  open(a.out, 'w'), ensure_ascii=False, indent=1)
        print(f'\nsaved {a.out}')
    if any(r['errors'] for r in readings):
        sys.exit(1)


if __name__ == '__main__':
    main()
