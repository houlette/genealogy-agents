#!/usr/bin/env python3
"""Find the earlier work on a person, surname, place or record before tasking a worker.

    python3 tools/prior_work.py --person @I565@ --place Musterkreis --year 1780-1800
    python3 tools/prior_work.py --surname Kornwieser --place Beispielheim
    python3 tools/prior_work.py --key DGS007654321:671-683     # one film, some images
    python3 tools/prior_work.py --key 123457:46                # Archion viewer, Bild 46
    python3 tools/prior_work.py --person @I565@ --brief-block  # the block a brief must carry
    python3 tools/prior_work.py --stale                        # open lists naming saved images

Run it before writing any retrieval brief, and paste the --brief-block output
into the brief. The hook .claude/hooks/prior_work_guard.py and tools/dispatch.py
refuse a retrieval brief without that block (see check_brief below).

The search is the same every time, so it does not depend on the terms the
person tasking the worker happens to choose. It widens the query first:

- names: every NAME and SURN in tree.ged for each --person, each group of
  surname_variants in project.json that holds a name, and the words in the
  corpus with the same Koelner Phonetik code (a phonetic code made for German
  names), shown apart as "sound-alikes";
- places: the record identifiers (DGS and film numbers, Archion viewer
  numbers, Ancestry collections) in the file names of sources/ that name the
  place, and those written just after the place in the logs and dossiers.

It then searches the research logs, open-questions.md, tasks.md,
sources-index.md, every .md under sources/, the earlier briefs (briefs_glob in
project.json), the image paths on disk and in the archive manifest, and the
citations on each --person in tree.ged. The output, newest first in each part:

  saved     images already in sources/ (or in the manifest), by identifier and image
  registers each identifier: how often it is mentioned, the image ranges written
            next to it, and the Coverage: lines of the dossiers
  text      the lines that mention the names or IDs, grouped by file, best first
  tree      the sources/ files the people given already cite

Nothing is cut off silently: every capped list says how many it left out, and
--full prints everything. Dossiers should record each register range read as

  Coverage: DGS007654321 img 671-683 | Musterkreis court minutes Nov 1794-May 1795 | Seiler will probate | none found | claude-sub-58, 2026-11-22

so that this tool can report it exactly.
"""
import argparse
import glob
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_config as cfg  # noqa: E402

PROJECT = cfg.PROJECT_DIR
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp", ".pdf")
BLOCK_RE = re.compile(r"Prior work \(tools/prior_work\.py, (\d{4}-\d{2}-\d{2})[^)]*\): *(.*)")
BRIEF_ID_RE = re.compile(r"@I\d+@")


# ---------------------------------------------------------------- phonetics

@lru_cache(maxsize=200000)
def _fold_word(text):
    return _fold(text)


def fold(text):
    if len(text) < 40:
        return _fold_word(text)
    return _fold(text)


def _fold(text):
    """Lower-case ASCII, umlauts written out as in file names (oe for ö), accents dropped."""
    return fold_case(text).lower()


def fold_case(text):
    """ASCII with the case kept: umlauts written out as in file names (oe for ö), accents dropped."""
    if text.isascii():
        return text
    for a, b in (("ß", "ss"), ("ẞ", "SS"), ("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        text = text.replace(a, b)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


@lru_cache(maxsize=None)
def koelner(word):
    """Koelner Phonetik code of a word ('Seyler' and 'Seiler' both give '857')."""
    w = re.sub("[^A-Z]", "", fold(word).upper())
    codes = []
    for i, c in enumerate(w):
        prev = w[i - 1] if i else ""
        nxt = w[i + 1] if i + 1 < len(w) else ""
        if c in "AEIJOUY":
            code = "0"
        elif c == "H":
            continue
        elif c == "B":
            code = "1"
        elif c == "P":
            code = "3" if nxt == "H" else "1"
        elif c in "DT":
            code = "8" if nxt in ("C", "S", "Z") and nxt else "2"
        elif c in "FVW":
            code = "3"
        elif c in "GKQ":
            code = "4"
        elif c == "C":
            if i == 0:
                code = "4" if nxt and nxt in "AHKLOQRUX" else "8"
            else:
                code = "8" if prev in ("S", "Z") or not nxt or nxt not in "AHKOQUX" else "4"
        elif c == "X":
            code = "8" if prev in ("C", "K", "Q") and prev else "48"
        elif c == "L":
            code = "5"
        elif c in "MN":
            code = "6"
        elif c == "R":
            code = "7"
        elif c in "SZ":
            code = "8"
        else:
            continue
        codes.append(code)
    out = []
    for ch in "".join(codes):
        if not out or out[-1] != ch:
            out.append(ch)
    s = "".join(out)
    return s[:1] + s[1:].replace("0", "") if s else ""


# ---------------------------------------------------------------- identifiers

def norm_num(n):
    return n.lstrip("0") or "0"


def parse_key(text):
    """'DGS007654321:671-683' -> ('dgs', '7654321', (671, 683)). Bare 6 digits is an Archion viewer."""
    text = text.strip()
    rng = None
    m = re.match(r"(.*?):(\d+)(?:-(\d+))?$", text)
    if m and not text.lower().startswith("ark:"):
        text, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        rng = (a, b)
    t = text.replace(" ", "")
    for kind, pat in (("dgs", r"(?i)dgs0*(\d{6,9})$"), ("film", r"(?i)film0*(\d{5,8})$"),
                      ("archion", r"(?i)(?:archion-?)?(\d{6})$"), ("ancestry", r"(?i)(?:ancestry|collection|dbid)-?(\d{3,5})$")):
        m = re.match(pat, t)
        if m:
            return (kind, norm_num(m.group(1)), rng)
    if t.lower().startswith("ark:"):
        return ("ark", t, rng)
    m = re.match(r"0*(\d{7,9})$", t)
    if m:
        return ("dgs", norm_num(m.group(1)), rng)
    raise SystemExit(f"prior_work: cannot read the identifier {text!r}; write it like DGS007654321, film1234567, 123457 (Archion), Ancestry9061 or ark:/...")


def key_label(k):
    kind, num = k[0], k[1]
    return {"dgs": f"DGS {num.zfill(9)}", "film": f"film {num}", "archion": f"Archion {num}",
            "ancestry": f"Ancestry collection {num}", "ark": num}[kind]


def key_regex(k):
    kind, num = k[0], k[1]
    if kind in ("dgs", "film"):
        return re.compile(rf"(?<!\d)0*{num}(?!\d)")
    if kind == "archion":
        return re.compile(rf"(?<!\d){num}(?!\d)")
    if kind == "ancestry":
        return re.compile(rf"(?i)(?:ancestry[- ]?(?:collection )?|collection |dbid=|coll\. ?){num}(?!\d)")
    return re.compile(re.escape(num))


def keys_in_filename(name):
    """Identifiers and image numbers in a sources/ file name."""
    out = []
    m = re.search(r"(?i)DGS0*(\d{6,9})", name)
    if m:
        img = re.search(r"(?i)img0*(\d+)", name[m.end():]) or re.search(r"(?i)img0*(\d+)", name)
        out.append((("dgs", norm_num(m.group(1))), int(img.group(1)) if img else None))
    m = re.search(r"(?i)film0*(\d{5,8})", name)
    if m:
        img = re.search(r"(?i)img0*(\d+)", name)
        out.append((("film", norm_num(m.group(1))), int(img.group(1)) if img else None))
    m = re.search(r"(?i)(?:Archion-)(\d{6})|(?<!\d)(\d{6})-(?:Bild|spread)|Bild\d+-(\d{6})(?!\d)", name)
    if m:
        img = re.search(r"(?i)Bild0*(\d+)", name)
        out.append((("archion", m.group(1) or m.group(2) or m.group(3)), int(img.group(1)) if img else None))
    m = re.search(r"(?i)Ancestry-(\d{3,5})(?!\d)", name)
    if m:
        img = re.search(r"(?i)img(\d+_0*(\d+))", name)
        out.append((("ancestry", norm_num(m.group(1))), int(img.group(2)) if img else None))
    return out


TEXT_ID_PATTERNS = (
    ("dgs", re.compile(r"(?i)DGS[ #:]*0*(\d{6,9})")),
    ("film", re.compile(r"(?i)\bfilm[ #:]*0*(\d{5,8})")),
    ("archion", re.compile(r"(?i)Archion[- ,]*(?:viewer )?(\d{6})(?!\d)")),
    ("archion", re.compile(r"(?i)(?:viewer|Bestand) ?(\d{6})(?!\d)")),
    ("archion", re.compile(r"(?<![\d/])(\d{6})(?!\d)(?=,? Bild)")),
    ("archion?", re.compile(r"\((\d{6})\)")),
    ("ancestry", re.compile(r"(?i)(?:Ancestry(?: collection)?|collection|dbid=)[ ]?(\d{3,5})(?!\d)")),
)

RANGE_RE = re.compile(r"(?i)\b(?:images?|imgs?|img|Bild(?:er)?|pages?|pp?\.)\s*0*(\d{1,4})(?!\d)(?:\s*(?:-|–|to|and)\s*0*(\d{1,4})(?!\d))?")


def ids_in_text(text):
    out = []
    loose_ok = "archion" in text.lower() or "bild" in text.lower()
    for kind, pat in TEXT_ID_PATTERNS:
        if kind == "archion?":
            if not loose_ok:
                continue
            kind = "archion"
        for m in pat.finditer(text):
            out.append(((kind, norm_num(m.group(1)) if kind != "archion" else m.group(1)), m.start()))
    return out


# ---------------------------------------------------------------- corpus

def rel(path):
    return os.path.relpath(path, PROJECT)


def corpus_files():
    conf = cfg.CONFIG
    pats = ["research-log*.md", "open-questions.md", "tasks.md", "sources-index.md",
            "sources/*.md", "sources/**/*.md"]
    pats += conf.get("briefs_glob") or ["scratch/*/*brief*.md"]
    seen, out = set(), []
    for p in pats:
        for f in sorted(glob.glob(os.path.join(PROJECT, p), recursive=True)):
            if f not in seen and os.path.isfile(f):
                seen.add(f)
                out.append(f)
    return out


def image_paths():
    """Image paths in sources/ on disk and in the archive manifest."""
    paths = set()
    src = os.path.join(PROJECT, "sources")
    for dirpath, _, files in os.walk(src):
        for f in files:
            if f.lower().endswith(IMAGE_EXT):
                paths.add(rel(os.path.join(dirpath, f)))
    manifest = os.path.join(PROJECT, (cfg.CONFIG.get("archive") or {}).get("manifest", "sources-manifest.tsv"))
    if os.path.exists(manifest):
        for line in open(manifest, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            p = line.split("\t")[0]
            if p.lower().endswith(IMAGE_EXT):
                paths.add(p)
    return sorted(paths)


def file_date(path):
    m = re.search(r"(20\d\d-\d\d-\d\d)", os.path.basename(path))
    if m:
        return m.group(1)
    try:
        return datetime.fromtimestamp(os.path.getmtime(os.path.join(PROJECT, path))).strftime("%Y-%m-%d")
    except OSError:
        return ""


def line_date(line, fallback):
    m = re.match(r"\s*[-*]?\s*(20\d\d-\d\d-\d\d)", line)
    return m.group(1) if m else fallback


def load_corpus():
    out = []
    for f in corpus_files():
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            cased = [fold_case(l) for l in lines]
            out.append((rel(f), lines, cased, [c.lower() for c in cased]))
        except OSError:
            continue
    return out


# ---------------------------------------------------------------- query expansion

def tree_people(ids):
    if not ids:
        return {}
    import person
    recs = person.load()
    out = {}
    for pid in ids:
        rec = recs.get(pid)
        if not rec:
            raise SystemExit(f"prior_work: {pid} is not in {cfg.CONFIG['tree']}")
        out[pid] = rec
    return out


def surnames_of(rec):
    names = []
    for _, line in rec["lines"]:
        m = re.match(r"^1 NAME .*?/([^/]+)/", line)
        if m:
            names.append(m.group(1).strip())
        m = re.match(r"^2 SURN (.+)", line)
        if m:
            names.append(m.group(1).strip())
    out = []
    for n in names:
        for part in re.split(r"[ ,]+(?:or|alias|genannt)?[ ,]*", n):
            if len(part) >= 3 and part not in out:
                out.append(part)
    return out


def cited_files(rec):
    out = []
    for _, line in rec["lines"]:
        for m in re.finditer(r"sources/[^\s,;()\]]+", line):
            p = m.group(0).rstrip(".:")
            if p not in out:
                out.append(p)
    return out


def variant_groups():
    return [list(g) for g in (cfg.CONFIG.get("surname_variants") or []) if g]


def expand_names(names):
    exact = []
    groups = variant_groups()
    for n in names:
        for x in [n] + [v for g in groups if any(fold(v) == fold(n) or fold(n).startswith(fold(v)) and len(v) >= 5 for v in g) for v in g]:
            if fold(x) not in {fold(e) for e in exact}:
                exact.append(x)
    return exact


WORD_RE = re.compile(r"[A-ZÄÖÜ][A-Za-zäöüßÄÖÜéèàç]{2,}")


def sound_alikes(names, corpus, image_list, limit=12):
    codes = defaultdict(set)
    for n in names:
        c = koelner(n)
        if len(c.lstrip("0")) >= 3:
            codes[c].add(_initial(n))
    if not codes:
        return []
    counts = Counter()
    known = {fold(n) for n in names}
    words = Counter()
    for _, lines, _c, _f in corpus:
        for line in lines:
            words.update(WORD_RE.findall(line))
    for w, n in words.items():
        c = koelner(w)
        if c in codes and _initial(w) in codes[c] and fold(w) not in known:
            counts[w] += n
    for p in image_list:
        for w in WORD_RE.findall(os.path.basename(p)):
            c = koelner(w)
            if c in codes and _initial(w) in codes[c] and fold(w) not in known:
                counts[w] += 1
    merged = Counter()
    for w, n in counts.items():
        merged[w] += n
    return [w for w, _ in merged.most_common(limit)]


def _initial(word):
    """First letter, for screening sound-alikes ('Kaitel' for Weidel, not 'Cattle')."""
    return fold(word)[:1]


def word_like(terms, corpus):
    """The terms used more often as ordinary words than as names in the corpus
    ('File' in 'Files:' and 'file'), which name_regex then matches only after a
    given name or between hyphens, as in 'Michael File' or '-File-'."""
    out = set()
    for t in terms:
        f = fold_case(t)
        low = re.compile(rf"(?<![A-Za-z]){re.escape(f.lower())}s?(?![A-Za-z])")
        cap = re.compile(rf"(?<![A-Za-z]){re.escape(f[:1].upper() + f[1:])}s?(?![A-Za-z])")
        nl = nc = 0
        for _, _l, cased, _f in corpus:
            for c in cased:
                if f[1:].lower() in c.lower():
                    nl += len(low.findall(c))
                    nc += len(cap.findall(c))
        if nl > nc:
            out.add(t)
    return out


def name_regex(terms, wordlike=()):
    """Matches a surname written as a name (capitalized or in capitals), so that a
    surname that is also a word ('File', 'Park', 'Miller') does not match the word."""
    if not terms:
        return None
    alts = set()
    for t in terms:
        f = fold_case(t)
        cap = f[:1].upper() + f[1:]
        if t in wordlike:
            alts.add(r"(?:(?<![A-Za-z])[A-Z][a-z]+\.? |(?<![A-Za-z])[A-Z]\. |-)" + re.escape(cap) + r"(?=[-_ ,;.)'\]]|$)")
        else:
            alts.add(r"(?<![A-Za-z])" + re.escape(cap) + r"[a-z]{0,3}(?![A-Za-z])")
        alts.add(r"(?<![A-Za-z])" + re.escape(f.upper()) + r"[A-Z]{0,3}(?![A-Za-z])")
    return re.compile("|".join(sorted(alts, key=len, reverse=True)))


def place_keys(places, corpus, image_list):
    """Identifiers tied to each place: in sources/ file names naming the place,
    and written within 120 characters after the place in the text."""
    found = Counter()
    for place in places:
        pf = fold(place)
        for p in image_list:
            if pf in fold(os.path.basename(p)):
                for k, _ in keys_in_filename(os.path.basename(p)):
                    found[k] += 3
        for _, lines, _c, folded in corpus:
            for line, fl in zip(lines, folded):
                if pf not in fl:
                    continue
                start = fl.find(pf)
                while start != -1:
                    window = line[start:start + len(place) + 120]
                    for k, _ in ids_in_text(window):
                        found[k] += 1
                    start = fl.find(pf, start + 1)
    return [k for k, _ in found.most_common()]


# ---------------------------------------------------------------- search

def snippet(line, pos, width=260):
    line = line.strip()
    if len(line) <= width:
        return line
    start = max(0, min(pos - width // 3, len(line) - width))
    s = line[start:start + width]
    return ("..." if start else "") + s + ("..." if start + width < len(line) else "")


def years_in(line):
    return [int(y) for y in re.findall(r"(?<!\d)(1[5-9]\d\d)(?!\d)", line)]


def search(q, corpus, image_list):
    res = {"saved": [], "registers": defaultdict(lambda: {"mentions": 0, "ranges": [], "coverage": []}),
           "text": defaultdict(list), "sounds": {}, "saved_sound": []}
    nre = name_regex(q["names"], q.get("wordlike", ()))
    sre = name_regex(q["sounds"])
    idre = re.compile("|".join(re.escape(i) for i in q["ids"])) if q["ids"] else None
    places = [fold(p) for p in q["places"]]
    all_keys = q["keys"] + [k for k in q["place_keys"] if k not in q["keys"]]
    kres = [(k, key_regex(k), k[1] if k[0] != "ark" else k[1][:12]) for k in all_keys]
    y0, y1 = q["years"] or (None, None)

    # saved images
    for p in image_list:
        base = os.path.basename(p)
        if re.search(r"(?i)-crop\b|-crop-", base):
            continue
        fb = fold_case(base)
        hit_name = bool(nre and nre.search(fb))
        if not hit_name and sre and sre.search(fb):
            res["saved_sound"].append(p)
        fkeys = keys_in_filename(base)
        hit_key = None
        for k, img in fkeys:
            if k in q["keys"]:
                rng = q["key_ranges"].get(k)
                if rng is None or img is None or rng[0] <= img <= rng[1]:
                    hit_key = (k, img)
        if hit_name or hit_key:
            k, img = hit_key or (fkeys[0] if fkeys else (None, None))
            res["saved"].append((p, k, img, bool(hit_key), file_date(p)))

    # text
    for path, lines, cased, folded in corpus:
        fdate = file_date(path)
        for n, (line, fc, fl) in enumerate(zip(lines, cased, folded), 1):
            score, pos, why = 0, None, []
            m = nre.search(fc) if nre else None
            if m:
                score += 2
                pos = m.start()
                why.append("name")
            elif sre:
                m = sre.search(fc)
                if m:
                    score += 1
                    pos = m.start()
                    why.append("sound-alike")
                    word = re.match(r"[A-Za-z]+", fc[m.start():]).group(0).lower()
                    res["sounds"].setdefault(word, []).append((line_date(line, fdate), path, n, snippet(line, m.start(), 200)))
            if idre:
                m = idre.search(line)
                if m:
                    score += 3
                    pos = m.start() if pos is None else pos
                    why.append("id")
            key_hit = False
            for k, kre, needle in kres:
                if needle not in line:
                    continue
                if k not in q["keys"] and score == 0:
                    continue
                km = kre.search(line)
                if not km:
                    continue
                key_hit = True
                reg = res["registers"][k]
                reg["mentions"] += 1
                if line.lstrip().startswith("Coverage:"):
                    reg["coverage"].append((path, n, line.strip(), fdate))
                tail = line[km.end():km.end() + 200]
                for r in RANGE_RE.finditer(tail):
                    a = int(r.group(1))
                    b = int(r.group(2)) if r.group(2) else a
                    reg["ranges"].append((a, b, path, n, line_date(line, fdate)))
                    break
                if k in q["keys"]:
                    score += 3
                    pos = km.start() if pos is None else pos
                    why.append("key")
            if score == 0:
                continue
            if key_hit and "key" not in why:
                score += 1
                why.append("place register")
            if places and any(p in fl for p in places):
                score += 1
                why.append("place")
            if y0 is not None and any(y0 <= y <= y1 for y in years_in(line)):
                score += 1
                why.append("year")
            res["text"][path].append((score, n, snippet(line, pos or 0), line_date(line, fdate), why))
    return res


# ---------------------------------------------------------------- report

def fmt_ranges(ranges, cap):
    seen, out = set(), []
    for a, b, path, n, d in sorted(ranges, key=lambda r: r[4], reverse=True):
        if (a, b) in seen:
            continue
        seen.add((a, b))
        out.append((f"{a}-{b}" if a != b else f"{a}") + f" ({path}:{n}, {d})")
    if cap and len(out) > cap:
        return out[:cap] + [f"... and {len(out) - cap} more"]
    return out


def report(q, res, people, caps):
    out = []
    say = out.append
    say("Query: " + q["label"])
    if q["names"]:
        say("  names searched: " + ", ".join(q["names"]))
    if q.get("wordlike"):
        say("  also ordinary words, so matched only after a given name or in a file name: " + ", ".join(sorted(q["wordlike"])))
    if q["sounds"]:
        say("  sound-alikes found in the corpus (Koelner Phonetik; check each): " + ", ".join(q["sounds"]))
    if q["place_keys"]:
        pk = [key_label(k) for k in q["place_keys"]]
        cap = caps["keys"]
        say(f"  identifiers tied to {', '.join(q['places'])}: " + ", ".join(pk[:cap]) +
            (f" ... and {len(pk) - cap} more (--full)" if cap and len(pk) > cap else ""))

    saved = sorted(res["saved"], key=lambda s: (not s[3], s[4]), reverse=False)
    say("")
    say(f"Saved images ({len(saved)}, crops not listed): do not fetch these again unless the brief says re-read")
    groups = defaultdict(list)
    for p, k, img, by_key, d in saved:
        groups[k].append((img, p))
    lines = []
    for k in sorted(groups, key=lambda k: (k is None, key_label(k) if k else "")):
        items = sorted(groups[k], key=lambda x: (x[0] is None, x[0] or 0))
        if k:
            imgs = sorted({i for i, _ in items if i is not None})
            lines.append(f"  {key_label(k)}: images {', '.join(map(str, imgs)) or '?'} ({len(items)} files)")
            for _, p in items[: caps["files_per_key"]]:
                lines.append(f"    {p}")
            if caps["files_per_key"] and len(items) > caps["files_per_key"]:
                lines.append(f"    ... and {len(items) - caps['files_per_key']} more files")
        else:
            for _, p in items:
                lines.append(f"  {p}")
    cap = caps["saved"]
    out.extend(lines[:cap] if cap else lines)
    if cap and len(lines) > cap:
        say(f"  ... and {len(lines) - cap} more lines (--full)")

    if res["sounds"]:
        say("")
        say("Sound-alike spellings: where each appears (newest first). Add real variants to surname_variants in project.json")
        for word, occ in sorted(res["sounds"].items(), key=lambda kv: -len(kv[1])):
            occ = sorted(occ, reverse=True)
            say(f"  {word} ({len(occ)} lines)")
            for d, path, n, snip in occ[: caps["lines_per_file"] or None]:
                say(f"    {path}:{n}: {snip}")

    ss = res["saved_sound"]
    if ss:
        say("")
        say(f"Saved images under a sound-alike spelling ({len(ss)}): check whether each is the same family")
        cap = caps["saved"] // 2
        for p in ss[:cap] if cap else ss:
            say(f"  {p}")
        if cap and len(ss) > cap:
            say(f"  ... and {len(ss) - cap} more (--full)")

    regs = {k: v for k, v in res["registers"].items() if v["mentions"]}
    say("")
    say(f"Registers ({len(regs)}): each --key, and each register named on a line about these people, "
        "with the image or page ranges written next to it and its Coverage: lines")
    ordered = sorted(regs, key=lambda k: (k not in q["keys"], -regs[k]["mentions"]))
    cap = caps["registers"]
    for k in ordered[:cap] if cap else ordered:
        v = regs[k]
        say(f"  {key_label(k)}: {v['mentions']} lines")
        for c in v["coverage"]:
            say(f"    {c[2]}  ({c[0]}:{c[1]})")
        for r in fmt_ranges(v["ranges"], caps["ranges"]):
            say("    range " + r)
    if cap and len(ordered) > cap:
        say(f"  ... and {len(ordered) - cap} more identifiers (--full)")

    say("")
    files = res["text"]
    ranked = sorted(files, key=lambda f: (max(h[0] for h in files[f]), max(h[3] for h in files[f]), len(files[f])), reverse=True)
    total = sum(len(v) for v in files.values())
    say(f"Text ({total} lines in {len(ranked)} files), best first")
    fcap, lcap = caps["text_files"], caps["lines_per_file"]
    for f in ranked[:fcap] if fcap else ranked:
        hits = sorted(files[f], key=lambda h: (h[0], h[3], -h[1]), reverse=True)
        say(f"  {f} ({len(hits)} lines)")
        for score, n, snip, d, why in hits[:lcap] if lcap else hits:
            say(f"    {n}: {snip}")
        if lcap and len(hits) > lcap:
            say(f"    ... and {len(hits) - lcap} more lines in this file")
    if fcap and len(ranked) > fcap:
        rest = ranked[fcap:]
        say(f"  ... and {len(rest)} more files: " + ", ".join(f"{f} ({len(files[f])})" for f in rest[: caps["rest_names"]]) +
            (f" ... (--full)" if caps["rest_names"] and len(rest) > caps["rest_names"] else ""))

    if people:
        say("")
        say("Tree: files already cited by the people given")
        for pid, rec in people.items():
            cf = cited_files(rec)
            say(f"  {pid} {(rec['lines'][1][1][7:] if len(rec['lines']) > 1 else '').strip()}: {len(cf)} files")
            cap = caps["cited"]
            for p in cf[:cap] if cap else cf:
                say(f"    {p}")
            if cap and len(cf) > cap:
                say(f"    ... and {len(cf) - cap} more (--full)")
    return out


CAPS = {"default": dict(keys=15, saved=60, files_per_key=4, registers=12, ranges=6, text_files=25, lines_per_file=3,
                        rest_names=40, cited=25),
        "brief": dict(keys=10, saved=40, files_per_key=3, registers=8, ranges=5, text_files=15, lines_per_file=2,
                      rest_names=25, cited=15),
        "full": dict(keys=0, saved=0, files_per_key=0, registers=0, ranges=0, text_files=0, lines_per_file=0,
                     rest_names=0, cited=0)}


# ---------------------------------------------------------------- stale lists

def stale(corpus, image_list):
    """Open lists (failed retrievals, open task rows) that name an image already saved."""
    saved = defaultdict(list)
    for p in image_list:
        for k, img in keys_in_filename(os.path.basename(p)):
            if img is not None:
                saved[(k, img)].append(p)
    out = []
    for path, lines, _c, _f in corpus:
        if path not in ("open-questions.md", "tasks.md"):
            continue
        section = ""
        for n, line in enumerate(lines, 1):
            if line.startswith("#"):
                section = line
            if path == "open-questions.md" and "retry" not in section.lower():
                continue
            if path == "tasks.md" and (not line.startswith("|") or re.search(r"(?i)\|\s*done\b", line)):
                continue
            found = sorted(ids_in_text(line), key=lambda x: x[1])
            for i, (k, pos) in enumerate(found):
                end = found[i + 1][1] if i + 1 < len(found) else pos + 160
                tail = line[pos:min(end, pos + 160)]
                for r in RANGE_RE.finditer(tail):
                    lo = int(r.group(1))
                    hi = int(r.group(2)) if r.group(2) else lo
                    if hi < lo or hi - lo > 300:
                        break
                    have = [img for img in range(lo, hi + 1) if (k, img) in saved]
                    if have:
                        span = f"image {lo}" if lo == hi else f"images {lo}-{hi}"
                        first = saved[(k, have[0])][0]
                        note = (f"{path}:{n} lists {key_label(k)} {span} as open, but {len(have)} of {hi - lo + 1} "
                                f"are saved (first: {first}). Update the line, or say why it stays open.")
                        if note not in out:
                            out.append(note)
                    break
    return out


# ---------------------------------------------------------------- brief check

def check_brief(text, today=None):
    """Problems that should stop a retrieval brief from being sent; [] when it may go."""
    today = today or date.today()
    m = BLOCK_RE.search(text)
    if not m:
        return ["the brief has no 'Prior work (tools/prior_work.py, <date>): <query>' block. Run "
                "python3 tools/prior_work.py --person <ID> ... --brief-block and paste its output into the brief."]
    problems = []
    try:
        d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        if today - d > timedelta(days=1):
            problems.append(f"the Prior work block is dated {d}; run tools/prior_work.py again, since later work may have changed the answer.")
    except ValueError:
        problems.append(f"the Prior work block's date {m.group(1)!r} is not a date.")
    query = m.group(2)
    block_start = m.start()
    named = set(BRIEF_ID_RE.findall(text[:block_start])) | set(BRIEF_ID_RE.findall(text[_block_end(text, m):]))
    missing = sorted(i for i in named if i not in query)
    if missing:
        problems.append("the brief names " + ", ".join(missing) + " but the Prior work query did not include them; "
                        "add --person for each and run it again.")
    if not named and "--key" not in query:
        problems.append("the brief names no person by ID (@I...@) and the Prior work query has no --key; name the "
                        "people on line 1 of the brief, or give the record identifier with --key.")
    return problems


def _block_end(text, m):
    end = text.find("\nEnd of prior work.", m.end())
    return end if end != -1 else len(text)


# ---------------------------------------------------------------- main

def build_query(a, corpus, image_list):
    people = tree_people(a.person)
    names = list(a.surname)
    for rec in people.values():
        for s in surnames_of(rec):
            if s not in names:
                names.append(s)
    names = expand_names(names)
    sounds = sound_alikes(names, corpus, image_list) if names and not a.no_sound else []
    keys = [parse_key(k) for k in a.key]
    pkeys = [k for k in place_keys(a.place, corpus, image_list) if k not in [(x[0], x[1]) for x in keys]] if a.place else []
    years = None
    if a.year:
        y = a.year.split("-")
        years = (int(y[0]), int(y[-1]))
    parts = []
    for flag, vals in (("--person", a.person), ("--surname", a.surname), ("--place", a.place), ("--key", a.key)):
        parts += [f"{flag} {v}" for v in vals]
    if a.year:
        parts.append(f"--year {a.year}")
    q = dict(ids=list(people), names=names, sounds=sounds, places=a.place, wordlike=word_like(names, corpus),
             keys=[(k[0], k[1]) for k in keys], key_ranges={(k[0], k[1]): k[2] for k in keys},
             place_keys=pkeys, years=years, label=" ".join(parts))
    return q, people


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--person", action="append", default=[], help="GEDCOM ID, e.g. @I565@ (repeatable)")
    ap.add_argument("--surname", action="append", default=[], help="surname or spelling (repeatable)")
    ap.add_argument("--place", action="append", default=[], help="place name (repeatable)")
    ap.add_argument("--key", action="append", default=[], help="record identifier, optionally :image or :from-to")
    ap.add_argument("--year", help="year or range, e.g. 1784-1795; ranks lines in range first")
    ap.add_argument("--full", action="store_true", help="print every hit")
    ap.add_argument("--brief-block", action="store_true", help="print the block to paste into a brief")
    ap.add_argument("--no-sound", action="store_true", help="skip the sound-alike search")
    ap.add_argument("--stale", action="store_true", help="list open items naming an image already saved")
    ap.add_argument("--check-brief", metavar="FILE", help="check a brief file as the hook does; exit 1 if it may not go")
    a = ap.parse_args(argv)

    if a.check_brief:
        problems = check_brief(open(a.check_brief, encoding="utf-8").read())
        for p in problems:
            print("prior_work: " + p)
        return 1 if problems else 0

    corpus = load_corpus()
    image_list = image_paths()
    if a.stale:
        notes = stale(corpus, image_list)
        for n in notes:
            print("NOTE stale: " + n)
        if not notes:
            print("prior_work --stale: no open item names an image already saved.")
        return 0
    if not (a.person or a.surname or a.place or a.key):
        ap.error("give at least one --person, --surname, --place or --key")

    q, people = build_query(a, corpus, image_list)
    res = search(q, corpus, image_list)
    caps = CAPS["full" if a.full else "brief" if a.brief_block else "default"]
    lines = report(q, res, people, caps)
    if a.brief_block:
        hits = len(res["saved"]) + sum(len(v) for v in res["text"].values())
        print(f"Prior work (tools/prior_work.py, {date.today().isoformat()}, {hits} hits): {q['label']}")
        print("Read this before starting. Do not fetch a saved image again or re-read a range listed here unless this brief says re-read.")
        print("\n".join(lines))
        print("End of prior work.")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
