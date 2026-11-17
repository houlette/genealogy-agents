#!/usr/bin/env python3
"""Look up people in tree.ged without reading the whole file.

Run from the project root:

    python3 tools/person.py @I54@          # one person: record, parents, spouses, children
    python3 tools/person.py @F12@          # one family
    python3 tools/person.py Schmidt        # every person whose name contains the text
    python3 tools/person.py @I54@ --brief  # relatives only, without the GEDCOM lines

The GEDCOM lines are printed with their line numbers in tree.ged, so an edit
can go straight to the right place. Re-run the lookup before editing if
another agent may have changed the file since.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_config as cfg  # noqa: E402

PATH = cfg.TREE


def load(path=PATH):
    records, order, current = {}, [], None
    for n, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.rstrip("\n")
        m = re.match(r"^0 (@[^@]+@) (\w+)", line)
        if m:
            current = m.group(1)
            records[current] = {"type": m.group(2), "lines": [(n, line)]}
            order.append(current)
        elif line.startswith("0 "):
            current = None
        elif current and line.strip():
            records[current]["lines"].append((n, line))
    return records


def values(rec, tag, level=1):
    out = []
    for _, line in rec["lines"]:
        m = re.match(rf"^{level} {tag}(?: (.*))?$", line)
        if m:
            out.append(m.group(1) or "")
    return out


def year(rec, tag):
    lines = [l for _, l in rec["lines"]]
    for i, line in enumerate(lines):
        if line.startswith(f"1 {tag}"):
            for sub in lines[i + 1:]:
                if sub.startswith("1 "):
                    break
                if sub.startswith("2 DATE "):
                    return sub[7:]
    return ""


def label(records, pid):
    rec = records.get(pid)
    if not rec:
        return f"{pid} (missing)"
    name = (values(rec, "NAME") or ["?"])[0].replace("/", "").strip()
    b, d = year(rec, "BIRT") or year(rec, "CHR") or year(rec, "BAPM"), year(rec, "DEAT") or year(rec, "BURI")
    return f"{pid} {name} ({b or '?'} - {d or '?'})"


def show_family(records, fid, brief):
    rec = records[fid]
    if not brief:
        for n, line in rec["lines"]:
            print(f"{n:>6}  {line}")
    for role in ("HUSB", "WIFE", "CHIL"):
        for pid in values(rec, role):
            print(f"  {role.lower():5} {label(records, pid)}")


def show_person(records, pid, brief):
    rec = records[pid]
    print(label(records, pid))
    if not brief:
        for n, line in rec["lines"]:
            print(f"{n:>6}  {line}")
    for fid in values(rec, "FAMC"):
        fam = records.get(fid, {"lines": []})
        parents = [label(records, p) for r in ("HUSB", "WIFE") for p in values(fam, r)]
        print(f"parents ({fid}): " + ("; ".join(parents) or "none recorded"))
    for fid in values(rec, "FAMS"):
        fam = records.get(fid, {"lines": []})
        spouses = [label(records, p) for r in ("HUSB", "WIFE") for p in values(fam, r) if p != pid]
        marr = year(fam, "MARR")
        print(f"spouse ({fid}{', m. ' + marr if marr else ''}): " + ("; ".join(spouses) or "none recorded"))
        for c in values(fam, "CHIL"):
            print(f"  child {label(records, c)}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    brief = "--brief" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(2)
    records = load()
    for q in args:
        if q in records:
            (show_person if records[q]["type"] == "INDI" else show_family)(records, q, brief)
        else:
            hits = [k for k, r in records.items() if r["type"] == "INDI"
                    and any(q.lower() in v.lower() for v in values(r, "NAME"))]
            for k in hits:
                print(label(records, k))
            if not hits:
                print(f"no person matches {q!r}")
        print()


if __name__ == "__main__":
    main()
