#!/usr/bin/env python3
"""Check the shared work queue, tasks.md, for the faults that make it drift.

    python3 tools/check_tasks.py                  # check the project's tasks.md
    python3 tools/check_tasks.py --stale-days 5   # claims older than 5 days are stale
    python3 tools/check_tasks.py FILE --today 2026-11-17

Two kinds of finding:

  ERROR  the file is broken as Markdown, so rows do not display as written:
         a row with a different number of cells from its table's header
         (extra cells are silently dropped, often the Status cell), or a row
         cut off from its table by a blank line or with no header above it.
         The exit status is 1 when there is an ERROR.
  NOTE   the queue has drifted: an "in progress" claim whose latest date is
         older than --stale-days (default 3), or a person ID (@I12@) named in
         the task cell of more than one open row, which usually means a second
         row was added instead of extending the first. Notes do not change
         the exit status; clear them in the next weekly review.

A row counts as open when its Status cell starts with open, proposed,
in progress or blocked. The tool cannot tell whether finished work was
marked finished; the rule in AGENTS.md (update the row in the commit that
logs the result) covers that.
"""
import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from project_config import PROJECT_DIR  # noqa: E402

OPEN = ("open", "proposed", "in progress", "blocked")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RE = re.compile(r"\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{4})\b"
                     r"|\b(\d{4})-(\d{2})-(\d{2})\b")
ID_RE = re.compile(r"@I\d+@")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def dates_in(text):
    out = []
    for m in DATE_RE.finditer(text):
        try:
            if m.group(1):
                out.append(date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))))
            else:
                out.append(date(int(m.group(4)), int(m.group(5)), int(m.group(6))))
        except ValueError:
            pass
    return out


def check(text, today, stale_days):
    errors, notes = [], []
    lines = text.split("\n")
    header_cols = None          # columns of the table we are in, or None
    open_ids = defaultdict(list)
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s.startswith("|"):
            header_cols = None       # a blank line, heading or paragraph ends a Markdown table
            continue
        nxt = lines[n].strip() if n < len(lines) else ""
        if re.fullmatch(r"\|?\s*:?-{3,}.*", s):
            continue                                     # the separator row
        if re.fullmatch(r"\|(\s*:?-{3,}:?\s*\|)+", nxt):
            header_cols = len(cells(s))                  # a header row
            continue
        c = cells(s)
        if header_cols is None:
            errors.append(f"ERROR line {n}: table row with no header above it (a blank line cut it off "
                          f"from its table?): {s[:70]}")
            continue
        if len(c) != header_cols:
            errors.append(f"ERROR line {n}: {len(c)} cells, but the table's header has {header_cols}; "
                          f"Markdown drops or blanks the difference: {s[:70]}")
            continue
        status = c[-1]
        low = status.lower()
        if low.startswith("in progress"):
            ds = dates_in(status)
            if ds and (today - max(ds)).days > stale_days:
                notes.append(f"NOTE line {n}: claim 'in progress' last dated {max(ds):%d %b %Y}, "
                             f"{(today - max(ds)).days} days ago; finish it, update it or release it: {c[0][:60]}")
        if low.startswith(OPEN):
            for pid in set(ID_RE.findall(c[0])):
                open_ids[pid].append(n)
    for pid, rows in sorted(open_ids.items()):
        if len(rows) > 1:
            notes.append(f"NOTE {pid} is named in {len(rows)} open rows (lines {', '.join(map(str, rows))}); "
                         f"merge them or say in each how they differ")
    return errors, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", nargs="?", default=os.path.join(PROJECT_DIR, "tasks.md"))
    ap.add_argument("--stale-days", type=int, default=3)
    ap.add_argument("--today", help="YYYY-MM-DD, for tests")
    a = ap.parse_args()
    if not os.path.exists(a.file):
        print(f"check_tasks: no {a.file}; nothing to check")
        return 0
    today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()
    with open(a.file, encoding="utf-8") as f:
        errors, notes = check(f.read(), today, a.stale_days)
    for l in errors + notes:
        print(l)
    print(f"check_tasks: {len(errors)} errors, {len(notes)} notes")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
