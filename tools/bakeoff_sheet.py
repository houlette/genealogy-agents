#!/usr/bin/env python3
"""Build tools/bakeoff/crop-sheet.html: every bake-off crop with its key fields.

    python3 tools/bakeoff_sheet.py            # rebuild the page from saved boxes and results
    python3 tools/bakeoff_sheet.py --locate   # first ask a model where each key field is (costs about $0.02 an entry)
    python3 tools/bakeoff_sheet.py --locate --redo wuenschensuhl-1730

The page shows, for each entry, the crop the models are given, the key fields
with the number of models that found each, and a box around each key field.

The boxes are a model's guess (default: argon), made with the key in hand, so
they are an aid for a person checking the key against the image and nothing
more: a box does not show that the key is right, and "not located" does not
show that it is wrong. Boxes are kept in tools/bakeoff/boxes.json and drawn as
an overlay in the page; the image files are not changed.
"""

import argparse
import concurrent.futures
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bakeoff  # noqa: E402
import llm  # noqa: E402

DIR = bakeoff.DIR
BOXES = os.path.join(DIR, 'boxes.json')
SHEET = os.path.join(DIR, 'crop-sheet.html')
MODELS = [m for m in bakeoff.DEFAULT_MODELS.split(',')]
COLORS = ['#d62728', '#1f77b4', '#2ca02c', '#9467bd', '#ff7f0e', '#17becf', '#e377c2', '#8c564b', '#bcbd22', '#7f7f7f']

SYSTEM = ('You locate words in images of old handwritten records. You answer with JSON only. '
          'You never invent a location: if a word is not written in the image, you say so.')
PROMPT = '''The image is a crop from a {register}, {place}, {year}, written in {language}.

Below are items that a reference reading says are written in this image. An item may list spelling
alternatives separated by "|". For each item, find where it is written and give a tight bounding box
around those words only (not the whole line). If the words of an item are split across two lines, box
the larger part. If you cannot find an item in the image, set "found" to false and "box_2d" to null;
do not guess.

Items:
{items}

Answer with this JSON and nothing else:
{{"fields": [{{"item": "<the item exactly as given>", "found": true, "as_written": "<the letters you see there>",
"box_2d": [ymin, xmin, ymax, xmax]}}]}}
Coordinates are integers from 0 to 1000, relative to the image height (y) and width (x), with 0,0 at the top left.'''


def locate(entry, model):
    r = llm.call(model, SYSTEM,
                 PROMPT.format(register=entry['register'], place=entry['place'], year=entry['year'],
                               language=entry['language'], items='\n'.join(f'- {k}' for k in entry['key'])),
                 images=[os.path.join(llm.ROOT, entry['crop'])], effort='medium', task=f'cropsheet:{entry["id"]}')
    fields = llm.parse_json(r['text']).get('fields', [])
    out = {}
    for k in entry['key']:
        f = next((x for x in fields if str(x.get('item', '')).strip() == k), None)
        box = f.get('box_2d') if f else None
        ok = bool(f and f.get('found') and isinstance(box, list) and len(box) == 4
                  and all(isinstance(v, (int, float)) for v in box) and box[2] > box[0] and box[3] > box[1])
        out[k] = {'found': ok, 'as_written': (f or {}).get('as_written') or '', 'box': box if ok else None}
    return {'crop': entry['crop'], 'model': model, 'fields': out, 'cost': r['cost']}


def run_locate(entries, model, redo):
    boxes = json.load(open(BOXES)) if os.path.exists(BOXES) else {}
    # A saved set of boxes is stale when the entry's crop or key has changed.
    todo = [e for e in entries if e['id'] in redo or e['id'] not in boxes
            or boxes[e['id']].get('crop') != e['crop'] or set(boxes[e['id']]['fields']) != set(e['key'])]
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        futures = {pool.submit(locate, e, model): e for e in todo}
        for fut in concurrent.futures.as_completed(futures):
            e = futures[fut]
            try:
                boxes[e['id']] = fut.result()
                n = sum(f['found'] for f in boxes[e['id']]['fields'].values())
                print(f'{e["id"]:24} {n} of {len(e["key"])} located  ${boxes[e["id"]]["cost"]:.4f}', flush=True)
            except Exception as err:  # a failed call leaves the entry without boxes; run --locate again
                print(f'{e["id"]:24} FAILED {str(err)[:120]}', flush=True)
            tmp = BOXES + '.tmp'
            json.dump(boxes, open(tmp, 'w'), ensure_ascii=False, indent=1)
            os.replace(tmp, BOXES)


CSS = '''body{font:15px/1.45 -apple-system,sans-serif;margin:24px;background:#fafaf7;color:#222}
section{margin:0 0 36px;padding:14px;background:#fff;border:1px solid #ddd;border-radius:6px}
.wrap{position:relative;display:block;margin:8px 0;line-height:0}
.wrap img{width:100%;border:1px solid #bbb;display:block}
.box{position:absolute;border:3px solid;border-radius:3px;box-sizing:border-box}
.box b{position:absolute;left:-3px;top:-19px;font:600 12px/16px -apple-system,sans-serif;color:#fff;padding:0 5px;border-radius:3px 3px 0 0;white-space:nowrap}
body.hide .box{display:none}
h2{font-size:17px;margin:0 0 4px}
.k span{display:inline-block;margin:2px 6px 2px 0;padding:1px 7px;border-radius:10px;background:#eee;border-left:10px solid #999}
.k span.none{background:#f6c9c4}.k i{color:#666}
.m{color:#666}nav a{margin-right:10px}
.bar{position:sticky;top:0;background:#fafaf7;padding:6px 0;z-index:5}'''


def build(entries):
    boxes = json.load(open(BOXES)) if os.path.exists(BOXES) else {}
    out = ['<!doctype html><meta charset="utf-8"><title>Bake-off crop sheet</title>', f'<style>{CSS}</style>',
           '<h1 style="font-size:21px">Handwriting test: crops and keys</h1>',
           '<p>One block per test entry: the crop each model is shown, the key fields the scores rest on, and how many '
           'of the models with a saved reading found each field (red background: none did). Each numbered box marks where '
           'a model (argon), given the key, says that field is written. The boxes are that model\'s guess, an aid for '
           'checking and nothing more; "not located" means it could not find the words in the crop.</p>',
           '<div class="bar"><label><input type="checkbox" checked onchange="document.body.classList.toggle(\'hide\',!this.checked)"> show boxes</label></div>',
           '<nav>' + ' '.join(f'<a href="#{e["id"]}">{e["id"]}</a>' for e in entries) + '</nav>']
    for i, e in enumerate(entries, 1):
        hits, n = {k: 0 for k in e['key']}, 0
        for m in MODELS:
            p = bakeoff.result_path(e, m)
            if os.path.exists(p):
                s = bakeoff.score(e, json.load(open(p)))
                if s:
                    n += 1
                    for k in s['informed']:
                        hits[k] += 1
        b = boxes.get(e['id'], {})
        fields = b.get('fields', {}) if b.get('crop') == e['crop'] else {}
        spans, divs = [], []
        for j, k in enumerate(e['key']):
            c = COLORS[j % len(COLORS)]
            f = fields.get(k)
            where = '' if f is None else (f' <i>box reads "{html.escape(f["as_written"])}"</i>' if f['found'] else ' <i>not located</i>')
            cls = 'none' if n and not hits[k] else ''
            spans.append(f'<span class="{cls}" style="border-left-color:{c}">{j + 1}. {html.escape(k)} · {hits[k]}/{n}{where}</span>')
            if f and f['found']:
                y0, x0, y1, x1 = [v / 10 for v in f['box']]
                divs.append(f'<div class="box" style="border-color:{c};top:{y0:.1f}%;left:{x0:.1f}%;height:{y1 - y0:.1f}%;width:{x1 - x0:.1f}%">'
                            f'<b style="background:{c}">{j + 1}</b></div>')
        out.append(f'<section id="{e["id"]}"><h2>{i}. {e["id"]}</h2><div class="m">{html.escape(e["register"])}, '
                   f'{html.escape(e["place"])}, {e["year"]} · {e["script"]} · {html.escape(e.get("focus") or "")}</div>'
                   f'<div class="wrap"><img loading="lazy" src="../../{html.escape(e["crop"])}" alt="crop">{"".join(divs)}</div>'
                   f'<div class="m">{html.escape(e["crop"])}</div>'
                   f'<div class="k"><b>Key:</b> {"".join(spans)}</div><div><b>Expected:</b> {html.escape("; ".join(e["expect"]))}</div>'
                   + (f'<div><b>Decoy:</b> {html.escape(e["decoy"])}</div>' if e.get('decoy') else '')
                   + f'<div class="m">{html.escape(e.get("key_status", ""))} {html.escape(e.get("crop_note", ""))}</div></section>')
    open(SHEET, 'w').write('\n'.join(out))
    print(f'wrote {SHEET}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--locate', action='store_true', help='ask a model for the boxes of entries that have none')
    ap.add_argument('--model', default='argon')
    ap.add_argument('--redo', default='', help='comma-separated entry ids to locate again')
    a = ap.parse_args()
    entries = json.load(open(os.path.join(DIR, 'entries.json')))
    if a.locate:
        run_locate(entries, a.model, set(a.redo.split(',')) - {''})
    build(entries)


if __name__ == '__main__':
    main()
