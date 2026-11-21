#!/usr/bin/env python3
"""Join other people's GEDCOM files to the project's tree, for the website only.

A relative who keeps a tree of their own (a spouse's side, say) can have it shown
on the family website without importing it into tree.ged, which stays the
research database. build_site.py reads the "joined_trees" setting in
site-src/site.json and builds the site from tree.ged with each joined file added:

    "joined_trees": [{
        "file": "site-src/trees/partner.ged",
        "same_people": {"@I1@": "@I729@", "@I7@": "@I1@"},
        "same_families": {"@F14@": "@F275@"},
        "evidence": "lead only. From the partner's own tree; not checked by this project."
    }]

- same_people maps a person in the joined file to the same person in tree.ged.
  The tree.ged record is kept; from the joined record only the links to families
  that are not themselves joined are added, so the joined file's parents attach.
- same_families does the same for families (the couple both files hold).
- Every other record gets a new ID: its own number plus an offset (1,000,000 for
  the first joined file, 2,000,000 for the second; "offset" overrides it), so
  the IDs never collide with tree.ged's and stay the same from build to build.
- Citations to source records (2 SOUR @S12@ with 3 PAGE) become one free-text
  SOUR line, "title; page", as tree.ged writes them.
- evidence, if given, is added as an "Evidence:" note to every fact of the
  joined file that has none, so the site hedges them as the grade says.

Nothing is written to tree.ged; the joined text exists only for the build.
"""
import re

FACTS_WITH_EVIDENCE = {
    "BIRT", "CHR", "BAPM", "DEAT", "BURI", "CREM", "MARR", "DIV", "RESI", "OCCU", "EDUC",
    "EMIG", "IMMI", "NATU", "CENS", "PROB", "WILL", "EVEN", "GRAD", "RETI", "RELI", "MILI",
}


def records(text):
    """The level-0 records of a GEDCOM text, as (id or None, tag, lines)."""
    out = []
    for chunk in re.split(r"(?m)^(?=0 )", text):
        lines = [ln.rstrip("\r") for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        m = re.match(r"0 @([^@]+)@ (\w+)", lines[0])
        out.append((m.group(1), m.group(2), lines) if m else (None, lines[0][2:].strip(), lines))
    return out


def highest_number(text):
    nums = [int(n) for n in re.findall(r"(?m)^0 @[A-Z]+(\d+)@ ", text)]
    return max(nums, default=0)


def source_titles(recs):
    """Source record ID to its title (with author), for flattening citations."""
    titles = {}
    for rid, tag, lines in recs:
        if tag != "SOUR":
            continue
        title = author = ""
        for ln in lines[1:]:
            m = re.match(r"1 (TITL|AUTH) (.*)", ln)
            if m and m.group(1) == "TITL":
                title = m.group(2)
            elif m:
                author = m.group(2)
        titles[rid] = ", ".join(x for x in (title, author) if x) or rid
    return titles


def flatten_citations(lines, titles):
    """Turn each 'n SOUR @Sx@' with its PAGE (and CONC/CONT) into one 'n SOUR title; page' line."""
    out, i = [], 0
    while i < len(lines):
        m = re.match(r"(\d) SOUR @([^@]+)@\s*$", lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        lvl = int(m.group(1))
        text = titles.get(m.group(2), m.group(2))
        page, cur = "", None
        i += 1
        while i < len(lines):
            lm = re.match(r"(\d) (\w+)(?: (.*))?$", lines[i])
            if not lm or int(lm.group(1)) <= lvl:
                break
            sub_lvl, tag, val = int(lm.group(1)), lm.group(2), lm.group(3) or ""
            if sub_lvl == lvl + 1:
                cur = tag
                if tag == "PAGE":
                    page = val
            elif cur == "PAGE" and tag == "CONC":
                page += val
            elif cur == "PAGE" and tag == "CONT":
                page += " " + val
            i += 1
        out.append(f"{lvl} SOUR {text}" + (f"; {page}" if page else ""))
    return out


def add_evidence(lines, note):
    """Add '2 NOTE Evidence: <note>' to each level-1 fact that has no Evidence note."""
    out, i = [], 0
    while i < len(lines):
        out.append(lines[i])
        m = re.match(r"1 (\w+)", lines[i])
        i += 1
        if not m or m.group(1) not in FACTS_WITH_EVIDENCE:
            continue
        has = False
        while i < len(lines) and not lines[i].startswith(("0 ", "1 ")):
            has = has or lines[i].startswith("2 NOTE Evidence:")
            out.append(lines[i])
            i += 1
        if not has:
            out.append(f"2 NOTE Evidence: {note}")
    return out


def join(main_text, extra_text, same_people=None, same_families=None, evidence=None, offset=1000000):
    """main_text with the records of extra_text added; see the module docstring."""
    same = {k.strip("@"): v.strip("@") for k, v in (same_people or {}).items()}
    same.update({k.strip("@"): v.strip("@") for k, v in (same_families or {}).items()})
    recs = records(extra_text)
    titles = source_titles(recs)
    if highest_number(main_text) >= offset:
        raise ValueError(f"joined tree: the main tree has IDs of {offset} or more; set a larger offset")
    idmap, n = dict(same), offset + highest_number(extra_text)
    for rid, tag, _ in recs:
        if rid and rid not in idmap and tag in ("INDI", "FAM"):
            m = re.fullmatch(r"[A-Z]+(\d+)", rid)
            if m:
                num = offset + int(m.group(1))
            else:
                n += 1
                num = n
            idmap[rid] = ("I" if tag == "INDI" else "F") + str(num)

    def remap(line):
        return re.sub(r"@([^@]+)@", lambda m: "@" + idmap.get(m.group(1), m.group(1)) + "@", line)

    main_recs = records(main_text)
    main_ids = {rid for rid, _, _ in main_recs if rid}
    missing = [f"@{v}@" for v in same.values() if v not in main_ids]
    if missing:
        raise ValueError("joined tree: not in the main tree: " + ", ".join(missing))
    added_links = {}   # main record ID -> lines to append
    new_records = []
    for rid, tag, lines in recs:
        if tag not in ("INDI", "FAM"):
            continue  # HEAD, TRLR, SOUR, REPO, OBJE, NOTE records are left out
        body = flatten_citations(lines, titles)
        if rid in same:
            links = [remap(ln) for ln in body[1:]
                     if re.match(r"1 (FAMC|FAMS|HUSB|WIFE|CHIL) @([^@]+)@", ln)
                     and re.match(r"1 \w+ @([^@]+)@", ln).group(1) not in same]
            added_links.setdefault(same[rid], []).extend(links)
            continue
        if evidence:
            body = add_evidence(body, evidence)
        new_records.append("\n".join(remap(ln) for ln in body))
    out = []
    for rid, tag, lines in main_recs:
        if tag == "TRLR":
            continue
        out.append("\n".join(lines + [ln for ln in added_links.get(rid, []) if ln not in lines]))
    return "\n".join(out + new_records + ["0 TRLR"]) + "\n"


def join_all(main_text, joined, project_dir):
    """Apply every entry of the joined_trees setting in turn."""
    import os
    text = main_text
    for k, entry in enumerate(joined or []):
        with open(os.path.join(project_dir, entry["file"]), encoding="utf-8-sig") as f:
            extra = f.read()
        text = join(text, extra, entry.get("same_people"), entry.get("same_families"), entry.get("evidence"),
                    entry.get("offset", 1000000 * (k + 1)))
    return text
