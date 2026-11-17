#!/usr/bin/env python3
"""Structural check of tree.ged, shared by every agent working in this repository.

Run from the project root:

    python3 tools/check_tree.py            # checks tree.ged
    python3 tools/check_tree.py other.ged  # checks another file

Errors (exit status 1): a family link to a record that does not exist, a link
that is recorded on one side only, or any person or family that cannot be
reached from the root person ("root" in project.json, default @I1@)
through FAMC/FAMS links.

Warnings (exit status 0): lifespans over 105 years, parents younger than 13 or
older than 65 at a child's birth, and a child born more than a year after a
parent's death. A warning is a prompt to look at the record, not proof of an
error.

Source checks (added 1 November 2026; see "Citing sources and stating
certainty" in AGENTS.md): a citation to a file in sources/ that does not exist
is an error. The run also prints one summary line counting dated facts with no
2 SOUR line, facts with no "Evidence:" note, facts marked "source not
recorded", cited images that may be too small to read (longest side under
700 pixels; checked only if Pillow is installed), and the grades of parent links
on the root person's direct line ("Parents: established|probable|hypothesis" notes). For
the full lists:

    python3 tools/check_tree.py --sources

Evidence checks (added 4 November 2026, after the review of that date): each
fact's Evidence: note is compared with its own citations, and a mismatch is
printed as an EVIDENCE warning naming the tree.ged line. The checks are: more
than one Evidence: note on one fact; "direct, original" with no saved image or
PDF cited; a "direct" grade whose only citations are labelled "index only" or
"online tree, lead only"; an exact birth date whose notes give only a baptism;
and an exact death date whose notes and citations give only a funeral or
burial. These are prompts to look, like the other warnings; fix the fact or
reword its note. The summary line also counts citations under facts that carry
none of the six kind-of-source labels of AGENTS.md citation rule 3.

The last line prints the next free @I@ and @F@ numbers for new records.
"""
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_config as cfg  # noqa: E402

ROOT = cfg.ROOT
EVENTS = {"BIRT", "CHR", "BAPM", "DEAT", "BURI", "CREM", "MARR", "DIV", "RESI", "CENS", "IMMI",
          "EMIG", "NATU", "PROB", "WILL", "EDUC", "GRAD", "MILI", "OCCU", "EVEN", "RELI"}
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp")
KIND_LABELS = ("with image", "with images", "image not saved", "index only", "compiled secondary source",
               "online tree, lead only", "family knowledge")
EXACT_DATE = re.compile(r"^\d{1,2} [A-Z]{3} \d{3,4}$")


def evidence_check(rid, tag, sub, lineno, base):
    """Return EVIDENCE warnings for one fact: its Evidence: notes against its citations."""
    out = []
    notes = [v for _, t, v in sub if t == "NOTE"]
    sours = [v for _, t, v in sub if t == "SOUR"]
    date = next((v for _, t, v in sub if t == "DATE"), "")
    grades = [n for n in notes if n.startswith("Evidence:")]
    where = f"line {lineno} {rid} {tag}"
    if len(grades) > 1:
        out.append(f"{where}: {len(grades)} Evidence: notes on one fact; keep one")
    grade = grades[0] if grades else ""
    if grade.startswith("Evidence: direct, original"):
        files = [f for s in sours for f in re.findall(r"sources/[^\s;,)`]+", s)]
        if not any(f.rstrip(".").lower().endswith(IMAGE_EXT + (".pdf",))
                   and os.path.exists(os.path.join(base, f.rstrip("."))) for f in files):
            out.append(f"{where}: 'direct, original' but no saved image is cited; regrade or save the image")
    if grade.startswith("Evidence: direct") and sours and all(
            ("index only" in s or "lead only" in s) and "with image" not in s for s in sours):
        out.append(f"{where}: 'direct' grade on index or online-tree citations only; use 'index' or 'lead only'")
    text = " ".join(notes).lower()
    stext = " ".join(sours).lower()
    if tag == "BIRT" and EXACT_DATE.match(date) and re.search(r"bapti|christen", text) \
            and not re.search(r"\bborn\b|\bnat[aeu]s?\b|\bgeboren\b|\bné", text) and "birth" not in stext:
        out.append(f"{where}: exact birth date, but the notes give only a baptism; enter CHR and use BEF or ABT for the birth")
    if tag == "DEAT" and EXACT_DATE.match(date) and re.search(r"funeral|buried|burial|beerdigt|begraben|inhum", text) \
            and not re.search(r"\bdied\b|\bdeath\b|\bdeceased\b|gestorben|décéd|\bobiit\b", text) \
            and not re.search(r"death|headstone|gravestone|obituary|sterbe", stext):
        out.append(f"{where}: exact death date, but the notes give only a funeral or burial; enter BURI and use BEF for the death")
    return out


def source_check(records, path):
    """Return (errors, summary line, detail lines) for the citations in tree.ged."""
    base = os.path.dirname(os.path.abspath(path))
    try:
        from PIL import Image
    except ImportError:
        Image = None
    errors, no_sour, no_evid, not_recorded, small = [], [], [], [], {}
    evidence, unlabelled = [], 0
    sizes = {}
    for rid, rec in records.items():
        if rec["type"] not in ("INDI", "FAM"):
            continue
        lines = rec["lines"]
        for i, (level, tag, value) in enumerate(lines):
            for f in re.findall(r"sources/[^\s;,)`]+", value):
                full = os.path.join(base, f.rstrip("."))
                if not os.path.exists(full):
                    errors.append(f"{rid} cites {f}, which does not exist")
                elif Image and full.lower().endswith(IMAGE_EXT):
                    if full not in sizes:
                        try:
                            sizes[full] = Image.open(full).size
                        except Exception:
                            sizes[full] = None
                    if sizes[full] and max(sizes[full]) < 700:
                        small.setdefault(f, (sizes[full], rid))
            if level != 1 or tag not in EVENTS:
                continue
            sub = []
            for level2, tag2, value2 in lines[i + 1:]:
                if level2 <= 1:
                    break
                sub.append((level2, tag2, value2))
            if not any(t == "DATE" for _, t, _ in sub):
                continue
            label = f"{rid} {tag} {next(v for _, t, v in sub if t == 'DATE')}"
            notes = " ".join(v for _, t, v in sub if t in ("NOTE", "CONT", "CONC"))
            if "Evidence: source not recorded" in notes:
                not_recorded.append(label)
            elif not any(t == "SOUR" for _, t, _ in sub) and f"Source: {cfg.OWNER}" not in notes:
                no_sour.append(label)
            if "Evidence:" not in notes:
                no_evid.append(label)
            evidence += evidence_check(rid, tag, sub, rec["linenos"][i], base)
            unlabelled += sum(1 for _, t, v in sub if t == "SOUR" and not any(k in v for k in KIND_LABELS))
    summary = (f"sources: {len(no_sour)} dated facts without a citation, "
               f"{len(not_recorded)} marked 'source not recorded', "
               f"{len(no_evid)} without an Evidence: note, "
               f"{len(small)} cited images under 700 px, "
               f"{unlabelled} citations without a kind-of-source label, "
               f"{len(evidence)} evidence warnings"
               + ("" if Image else " (Pillow not installed, sizes not checked)")
               + "; details: --sources")
    detail = (["", "Dated facts without a citation:"] + [f"  {x}" for x in no_sour]
              + ["", "Facts marked 'source not recorded':"] + [f"  {x}" for x in not_recorded]
              + ["", "Cited images under 700 px on the longest side (check that they are readable):"]
              + [f"  {f} {w}x{h} (first cited by {rid})" for f, ((w, h), rid) in sorted(small.items())]
              + ["", f"Facts without an Evidence: note: {len(no_evid)} (not listed; add notes as facts are reviewed)", ""])
    return errors, summary, detail, evidence


def parse(path):
    records = {}
    current = None
    with open(path, encoding="utf-8") as f:
        raw_lines = f.readlines()
    for lineno, raw in enumerate(raw_lines, 1):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue  # blank separator lines between records are allowed
        m = re.match(r"^(\d+) (?:(@[^@]+@) )?(\S+)(?: (.*))?$", line)
        if not m:
            print(f"WARNING unparseable line: {line!r}")
            continue
        level, xref, tag, value = m.groups()
        level = int(level)
        if level == 0:
            current = xref or tag
            records[current] = {"type": tag, "lines": [], "linenos": []}
        elif current is not None:
            records[current]["lines"].append((level, tag, value or ""))
            records[current]["linenos"].append(lineno)
    return records


def first_year(lines, event):
    for i, (level, tag, _) in enumerate(lines):
        if level == 1 and tag == event:
            for level2, tag2, value2 in lines[i + 1:]:
                if level2 <= 1:
                    break
                if tag2 == "DATE":
                    m = re.search(r"\b(\d{4})\b", value2)
                    return int(m.group(1)) if m else None
    return None


def link_grades(people, families):
    """Grade of each parent link on the root person's direct line, from the child's "Parents:" note."""
    def parents(pid):
        fams = [v for l, t, v in people[pid]["lines"] if l == 1 and t == "FAMC" and v in families]
        if not fams:
            return []
        return [v for l, t, v in families[fams[0]]["lines"] if l == 1 and t in ("HUSB", "WIFE") and v in people]

    grades, ungraded, hypotheses = collections.Counter(), [], []
    if ROOT not in people:
        return "direct-line parent links: root person missing", []
    seen, queue = {ROOT}, [ROOT]
    while queue:
        pid = queue.pop()
        notes = " ".join(v for l, t, v in people[pid]["lines"] if t in ("NOTE", "CONT", "CONC"))
        par = parents(pid)
        m = re.search(r"Parents: (established|probable|hypothesis)", notes)
        if m and m.group(1) == "hypothesis":
            hypotheses.append(pid)
        if par:
            if m:
                grades[m.group(1)] += 1
            elif pid != ROOT:
                ungraded.append(pid)
        for p in par:
            if p not in seen:
                seen.add(p)
                queue.append(p)
    summary = (f"direct-line parent links: {grades['established']} established, {grades['probable']} probable, "
               f"{grades['hypothesis']} hypothesis (link kept), {len(ungraded)} not yet graded; "
               f"{len(hypotheses) - grades['hypothesis']} hypotheses without a link")
    detail = (["Direct-line people whose parent link has no 'Parents:' grade yet:"] + [f"  {x}" for x in sorted(ungraded)]
              + ["", "Recorded hypotheses (with or without a kept link):"] + [f"  {x}" for x in sorted(hypotheses)] + [""])
    return summary, detail


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else cfg.TREE
    records = parse(path)
    source_errors, source_summary, source_detail, evidence = source_check(records, path)
    people = {k: v for k, v in records.items() if v["type"] == "INDI"}
    families = {k: v for k, v in records.items() if v["type"] == "FAM"}
    errors = []
    warnings = []
    graph = collections.defaultdict(set)

    for pid, rec in people.items():
        for _, tag, value in rec["lines"]:
            if tag in ("FAMC", "FAMS"):
                if value not in families:
                    errors.append(f"{pid} {tag} points to missing family {value}")
                    continue
                graph[pid].add(value)
                graph[value].add(pid)
                roles = ("CHIL",) if tag == "FAMC" else ("HUSB", "WIFE")
                if not any(l == 1 and t in roles and v == pid for l, t, v in families[value]["lines"]):
                    errors.append(f"{pid} has {tag} {value}, but {value} does not list {pid}")

    for fid, rec in families.items():
        for level, tag, value in rec["lines"]:
            if level == 1 and tag in ("HUSB", "WIFE", "CHIL"):
                if value not in people:
                    errors.append(f"{fid} {tag} points to missing person {value}")
                    continue
                graph[fid].add(value)
                graph[value].add(fid)
                back = "FAMC" if tag == "CHIL" else "FAMS"
                if not any(t == back and v == fid for _, t, v in people[value]["lines"]):
                    errors.append(f"{fid} lists {value} as {tag}, but {value} has no {back} {fid}")

    if ROOT not in people:
        errors.append(f"root person {ROOT} is missing")
    seen = {ROOT}
    queue = [ROOT]
    while queue:
        node = queue.pop()
        for nxt in graph[node]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    for rid in list(people) + list(families):
        if rid not in seen:
            errors.append(f"{rid} is not connected to {ROOT}")

    def name(pid):
        for _, tag, value in people[pid]["lines"]:
            if tag == "NAME":
                return value
        return "?"

    for pid, rec in people.items():
        born, died = first_year(rec["lines"], "BIRT"), first_year(rec["lines"], "DEAT")
        if born and died and (died < born or died - born > 105):
            warnings.append(f"{pid} {name(pid)}: born {born}, died {died}")
    for fid, rec in families.items():
        parents = [v for l, t, v in rec["lines"] if l == 1 and t in ("HUSB", "WIFE") and v in people]
        children = [v for l, t, v in rec["lines"] if l == 1 and t == "CHIL" and v in people]
        for p in parents:
            pb = first_year(people[p]["lines"], "BIRT")
            pd = first_year(people[p]["lines"], "DEAT")
            for c in children:
                cb = first_year(people[c]["lines"], "BIRT")
                if pb and cb and not 13 <= cb - pb <= 65:
                    warnings.append(f"{fid} parent {name(p)} born {pb}, child {name(c)} born {cb}")
                if pd and cb and cb > pd + 1:
                    warnings.append(f"{fid} child {name(c)} born {cb}, after parent {name(p)} died {pd}")

    errors += source_errors
    link_summary, link_detail = link_grades(people, families)
    source_summary += "; " + link_summary
    source_detail += link_detail
    if "--sources" in sys.argv:
        print("\n".join(source_detail))
    for w in warnings:
        print("WARNING", w)
    for w in evidence:
        print("EVIDENCE", w)
    for e in errors:
        print("ERROR", e)
    print(source_summary)

    def next_id(prefix, keys):
        nums = [int(k[2:-1]) for k in keys if re.fullmatch(rf"@{prefix}\d+@", k)]
        return f"@{prefix}{max(nums) + 1}@" if nums else f"@{prefix}1@"

    print(
        f"{len(people)} people, {len(families)} families; "
        f"{len(seen & set(people))}/{len(people)} people reachable from {ROOT}; "
        f"{len(errors)} errors, {len(warnings)} warnings; "
        f"next free IDs {next_id('I', people)} {next_id('F', families)}"
    )
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
