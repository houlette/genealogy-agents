#!/usr/bin/env python3
"""Read a typed or printed page with Tesseract (local, no tokens) and list the lines that name a surname.

    python3 tools/ocr_print.py sources/1903-10-21-newspaper-p3.jpg --find Schmidt,Schmid
    python3 tools/ocr_print.py page.png --lang deu --save

Use this for typescripts, newspapers, directories and printed indexes. The
1 November 2026 test found Tesseract close to perfect on a typescript and of
no use on handwriting, so do not use it on handwriting. The text is a finding
aid: a line it does not find is not a negative result, and anything cited is
read from the image. --save writes the text beside the image as <image>.ocr.txt.

Only English is installed at present; German (deu), French (fra) and Fraktur
(deu_latf, older frk) need `brew install tesseract-lang`.
"""

import argparse
import os
import subprocess
import sys
import unicodedata


def fold(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s.lower()) if not unicodedata.combining(c))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image')
    ap.add_argument('--lang', default='eng')
    ap.add_argument('--find', default='', help='comma-separated surnames or words')
    ap.add_argument('--psm', default='3', help='Tesseract page mode: 3 automatic, 4 one column, 6 one block')
    ap.add_argument('--save', action='store_true')
    a = ap.parse_args()
    have = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True).stdout.split()[5:]
    missing = [l for l in a.lang.split('+') if l not in have]
    if missing:
        sys.exit(f'Tesseract language not installed: {missing} (have {have}). Run: brew install tesseract-lang')
    p = subprocess.run(['tesseract', a.image, 'stdout', '-l', a.lang, '--psm', a.psm], capture_output=True, text=True)
    if p.returncode:
        sys.exit(f'tesseract failed: {p.stderr[-300:]}')
    text = p.stdout
    if a.save:
        out = os.path.splitext(a.image)[0] + '.ocr.txt'
        open(out, 'w').write(f'Tesseract draft ({a.lang}) of {os.path.basename(a.image)}; machine text, not checked against the image.\n\n{text}')
        print('saved', out)
    words = [fold(w.strip()) for w in a.find.split(',') if w.strip()]
    if not words:
        print(text)
        return
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if any(w in fold(l) for w in words)]
    for i in hits:
        print(f'line {i + 1}: ' + ' / '.join(l.strip() for l in lines[max(0, i - 1):i + 2] if l.strip()))
    print(f'{len(hits)} line(s) matched {words} in {len(lines)} lines of machine text. No match is not a negative result.')


if __name__ == '__main__':
    main()
