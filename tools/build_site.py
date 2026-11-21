#!/usr/bin/env python3
"""Build the family website from tree.ged, biographies.md, stories/ and sources/.

Usage: python3 tools/build_site.py            (writes site/ and private/)
       python3 tools/build_site.py --report   (also lists unmatched stories and places)

Needs the Python packages markdown and Pillow. The site's settings (its name,
whose name the pages use, the regions and countries, the story footer) are in
site-src/site.json; see site-src/README.md for every input file.

Output, both git-ignored:
  site/      static files every approved reader may load: the page, the code,
             data/public.json (deceased people), and img/ (record images that
             site-src/rights.json allows to be shown).
  private/   private.json and img/, read only by the sign-in app
             (family-app/), which hands each reader the living relatives and
             recent stories their grandparents rule allows.

Privacy rules (site-src/README.md, "Living relatives"):
  - living: no death record and a birth year, recorded or estimated, after
    the project's living cutoff (tools/living.py). Public data keeps only an
    ID, sex and family links for them, so charts can show "Living relative".
  - recent: deceased, with a living child or grandchild. Their names, dates,
    places and events are public, but their stories, notes, citations and
    images are private, since those often name living relatives.
  - any image of a 1940 or 1950 census is private.
Compiled by Claude, 9 November 2026; made project-neutral for genealogy-agents, 17 November 2026.
"""
import glob
import hashlib
import html
import json
import os
import re
import shutil
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import living as livingmod  # noqa: E402
import project_config as cfg  # noqa: E402

SITE, PRIV, SRC = "site", "private", "site-src"
REPORT = "--report" in sys.argv
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
SKIP_TAGS = {"NOTE", "SOUR", "OBJE", "FAMC", "FAMS", "NAME", "SEX"}

# Settings in site-src/site.json; keys it leaves out take these values.
# Patterns are regular expressions that must work in both Python and JavaScript.
SITE_DEFAULTS = {
    "name": "Our Family History",
    "owner_name": None,       # who receives contributions and requests; default: owner in project.json
    "root_name": None,        # what the pages call the root person ("Alex's ancestors"); default: owner_name
    "story_footer": "",       # a line under every story chapter, such as a draft warning
    "history_footer": "",     # a line under the historical events, such as "not yet checked against sources"
    "origins_note": "",       # added to the explanation of the "Where the family came from" chart
    "request_example": "for example, \"Aunt Carol's daughter\"",
    "root": None,             # the person whose ancestors the site follows, if not the project's root (a child, to show both parents' sides)
    "joined_trees": [],       # other GEDCOM files shown on the site alongside tree.ged; see tools/join_trees.py
    # Region codes for matching historical events (site-src/history-events.json) to people.
    "regions": [
        ["France", "fr"], ["Germany|Deutschland|Prussia|Heiliges", "de"], ["Poland|Polska", "pl"],
        ["Ireland", "ie"], ["Scotland", "sco"], ["England", "eng"], ["Wales", "wal"],
        ["Netherlands|Holland", "nl"], ["Italy|Italia", "it"], ["Sweden|Norway|Denmark", "scand"],
        ["Canada", "ca"], ["USA|America", "us"],
    ],
    # Birthplace colours, tested in list order; color is a palette slot from 1 to 6 (style.css).
    "countries": [
        {"label": "America", "match": "USA|America", "color": 1},
        {"label": "France", "match": "France", "color": 2},
        {"label": "Germany", "match": "Germany|Deutschland|Prussia|Heiliges", "color": 3},
        {"label": "Britain and Ireland", "match": "Ireland|Scotland|England|Wales", "color": 4},
        {"label": "Poland", "match": "Poland|Polska", "color": 5},
    ],
}


def load_site_config(path=None):
    """site-src/site.json merged over SITE_DEFAULTS, with the names filled in from project.json."""
    conf = dict(SITE_DEFAULTS)
    path = path or os.path.join(cfg.PROJECT_DIR, SRC, "site.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            conf.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    conf["owner_name"] = conf["owner_name"] or cfg.OWNER
    conf["root_name"] = conf["root_name"] or conf["owner_name"]
    return conf


SITE_CONF = load_site_config()
REGION_WORDS = [(pat, code) for pat, code in SITE_CONF["regions"]]


# ---------- helpers ----------

def sort_year(d):
    m = re.search(r"(?:(\d{1,2}) )?(?:([A-Z]{3}) )?(\d{4})", d or "")
    if not m:
        return None
    return round(int(m.group(3)) + MONTHS.get(m.group(2) or "", 0) / 12 + (int(m.group(1) or 1) - 1) / 365, 3)


def pretty_date(d):
    d = (d or "").replace("ABT ", "about ").replace("BEF ", "before ").replace("AFT ", "after ").replace("EST ", "about ")
    d = re.sub(r"BET (.+) AND (.+)", r"\1 to \2", d)
    d = re.sub(r"FROM (.+) TO (.+)", r"\1 to \2", d)
    return re.sub(r"\b([A-Z]{3})\b", lambda m: m.group(1).title(), d)


def short_place(p):
    return re.sub(r", (USA|France|Germany)$", "", p or "")


def regions(place):
    return sorted({r for pat, r in REGION_WORDS if re.search(pat, place or "")})


def surnames():
    out = {}
    with open(cfg.TREE, encoding="utf-8") as f:
        text = f.read()
    for m in re.finditer(r"(?m)^0 @(I\d+)@ INDI\n1 NAME [^\n/]*/([^/\n]*)/", text):
        out[m.group(1)] = m.group(2).strip()
    return out


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- images ----------

RIGHTS = {"show": [], "never": []}   # loaded from site-src/rights.json in main()


def files_in(citation):
    """Image files a citation points to, including images beside a cited .md."""
    out = []
    for f in re.findall(r"sources/[^ ;),]+", citation):
        f = f.rstrip(".")
        if re.search(r"\.(jpe?g|png)$", f, re.I) and os.path.exists(f):
            out.append(f)
        elif f.endswith(".md"):
            stem = f[:-3]
            out += sorted(g for g in glob.glob(glob.escape(stem) + "*")
                          if re.search(r"\.(jpe?g|png)$", g, re.I) and not os.path.basename(g)[len(os.path.basename(stem)):].lstrip("-_").startswith(("p0", "page")))
    seen = []
    for f in out:
        if f not in seen:
            seen.append(f)
    return seen[:6]


def allowed(citation):
    if any(re.search(p, citation, re.I) for p in RIGHTS["never"]):
        return False
    return any(re.search(p, citation, re.I) for p in RIGHTS["show"])


KINDS = [
    (r"census", "Censuses", "Census"),
    (r"baptism|christening", "Births and baptisms", "Baptism record"),
    (r"birth", "Births and baptisms", "Birth record"),
    (r"marriage", "Marriages", "Marriage record"),
    (r"headstone", "Deaths and burials", "Headstone"),
    (r"obituary|funeral", "Deaths and burials", "Obituary"),
    (r"burial", "Deaths and burials", "Burial record"),
    (r"death", "Deaths and burials", "Death record"),
    (r"passenger", "Arrivals and citizenship", "Passenger list"),
    (r"naturalization|oath", "Arrivals and citizenship", "Naturalization record"),
    (r"draft", "Military", "Draft card"),
    (r"muster", "Military", "Muster roll"),
    (r"morning-report", "Military", "Army morning report"),
    (r"wwi|wwii|pow|service|pension|military", "Military", "Military record"),
]
MONTH_NAMES = "January February March April May June July August September October November December".split()


def sidecar_of(path):
    """The note beside an image: the same name, or the name with trailing parts such as -crop removed."""
    stem = os.path.splitext(path)[0]
    for _ in range(5):
        if os.path.exists(stem + ".md"):
            return stem + ".md"
        if "-" not in os.path.basename(stem):
            break
        stem = stem.rsplit("-", 1)[0]
    return None


def caption_of(path, citation):
    """A plain title, a group and a year for a record image, and the translation in its sidecar note if the record
    was made in 1900 or earlier (later notes can name living people, so only their title is used)."""
    base = os.path.basename(path)
    m = re.match(r"(\d{4})(?:-(\d\d))?(?:-(\d\d))?-?(.*)", base)
    yr = int(m.group(1)) if m else None
    rest = (m.group(4) if m else base).lower()
    group, word = next(((g, w) for pat, g, w in KINDS if re.search(pat, rest)), ("Other records", "Record"))
    title, tr = "", ""
    side = sidecar_of(path)
    if side:
        text = open(side, encoding="utf-8").read()
        h = re.search(r"(?m)^# (.+)$", text)
        if h:
            title = re.sub(r"[`*_]", "", h.group(1)).strip()
        t = re.search(r"(?m)^(?:[-*] )?\**Translation\**:\**\s*(.+)$", text)
        if t and yr and yr <= 1900:
            tr = re.sub(r"[`*]", "", t.group(1)).strip()[:700]
    if not title:
        if re.match(r"\d{4} ", citation):
            title = ", ".join(x.strip() for x in citation.split(",")[:2])
        else:
            when = ""
            if m:
                mon = MONTH_NAMES[int(m.group(2)) - 1] + " " if m.group(2) and 1 <= int(m.group(2)) <= 12 else ""
                day = str(int(m.group(3))) + " " if m.group(3) and mon else ""
                when = ", " + day + mon + m.group(1)
            title = word + when
    return {"title": title, "group": group, "yr": yr, "tr": tr}


def make_image(path, private, images, citation, pid):
    key = hashlib.sha1(path.encode()).hexdigest()[:12]
    if key not in images:
        outdir = os.path.join(PRIV if private else SITE, "img")
        os.makedirs(outdir, exist_ok=True)
        big, th = os.path.join(outdir, key + ".webp"), os.path.join(outdir, key + "_t.webp")
        if not (os.path.exists(big) and os.path.getmtime(big) >= os.path.getmtime(path)):
            from PIL import Image
            try:
                im = Image.open(path)
                im = im.convert("RGB")
                im.thumbnail((2000, 2000))
                im.save(big, "WEBP", quality=80)
                im.thumbnail((360, 360))
                im.save(th, "WEBP", quality=70)
            except Exception as e:
                print("  image failed:", path, e)
                return None
        from PIL import Image
        with Image.open(big) as im:
            w, h = im.size
        images[key] = {"src": ("pimg/" if private else "img/") + key + ".webp", "th": ("pimg/" if private else "img/") + key + "_t.webp",
                       "w": w, "h": h, "file": os.path.basename(path), "cite": citation, "people": [], **caption_of(path, citation)}
    if pid not in images[key]["people"]:
        images[key]["people"].append(pid)
    return key


# ---------- stories ----------

def fold(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s)


def biography_sections(people, names):
    """Map sections of biographies.md to the people their headings name."""
    if not os.path.exists("biographies.md"):
        return {}, []
    import markdown
    with open("biographies.md", encoding="utf-8") as f:
        text = f.read()
    parts = re.split(r"(?m)^(#{2,4}) (.+)$", text)
    sections, unmatched = {}, []
    for i in range(1, len(parts) - 2, 3):
        level, head, body = parts[i], parts[i + 1], parts[i + 2]
        if len(level) < 3:
            continue
        found = []
        for seg in re.split(r"\band\b|,? & ", head):
            m = re.match(r"\s*([^(,]+)", seg)
            if not m:
                continue
            nm = fold(re.sub(r"\b(Sr|Jr|I{1,3})\b\.?", "", m.group(1))).split()
            if len(nm) < 2:
                continue
            years = [int(y) for y in re.findall(r"\b(1[4-9]\d\d|20\d\d)\b", seg)]
            best = None
            for pid, full in names.items():
                f = fold(full).split()
                if not f or f[-1] != nm[-1] or f[0] != nm[0]:
                    continue
                by = people[pid]["by"]
                if years and by and abs(years[0] - by) > 3:
                    continue
                best = pid if best is None else best
            if best:
                found.append(best)
        if not found:
            unmatched.append(head)
            continue
        body = re.sub(r"\[([^\]]+)\]\((?:sources/|\.\./)[^)]*\)", r"\1", body)  # local links become plain text
        rendered = markdown.markdown(body.strip(), extensions=["sane_lists"])
        for pid in found:
            sections.setdefault(pid, []).append({"head": head, "html": rendered})
    return sections, unmatched


# ---------- story chapters (stage 4) ----------

STEP_RE = re.compile(r"^\{\{(step|note)([^}]*)\}\}\s*$", re.M)


def read_stories(people, living, places_raw):
    """Parse stories/*.md: front matter, then sections each opened by {{step place="..." year=... person=I.. zoom=..}}
    or {{note}}. HTML comments become a collapsed 'About the sources' note, without their TODO sentences."""
    import markdown
    out, problems = [], []
    for path in sorted(glob.glob("stories/*.md")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        meta = dict(re.findall(r"^(\w+):\s*(.+)$", m.group(1), re.M)) if m else {}
        body = text[m.end():] if m else text
        parts = STEP_RE.split(body)
        steps = []
        for i in range(1, len(parts) - 2, 3):
            kind, md = parts[i], parts[i + 2]
            attrs = {k: a or b for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|(\S+))', parts[i + 1])}
            notes = []
            for c in re.findall(r"<!--(.*?)-->", md, re.S):
                c = re.sub(r"TODO:.*?(?=(?:\s+[A-Z][a-z]+:)|$)", "", c, flags=re.S).strip()
                if c:
                    notes.append(c)
            md = re.sub(r"<!--.*?-->", "", md, flags=re.S).strip()
            step = {"kind": kind, "html": markdown.markdown(md),
                    "src": markdown.markdown(" ".join(notes)) if notes else ""}
            if kind == "step":
                pl, pid = attrs.get("place", ""), attrs.get("person", "")
                r = places_raw.get(pl)
                if not r or r.get("precision") != "place":
                    problems.append(f"{path}: place not on the map: {pl}")
                else:
                    step.update(place=pl, lat=r["lat"], lon=r["lon"], name=r.get("label") or short_place(pl))
                if pid and pid not in people:
                    problems.append(f"{path}: no such person {pid}")
                elif pid in living:
                    raise SystemExit(f"{path}: {pid} is living and may not appear in a story step")
                step.update(person=pid, year=float(attrs.get("year", 0)) or None, zoom=int(attrs.get("zoom", 8)))
            steps.append(step)
        slug = re.sub(r"^\d+-", "", os.path.basename(path)[:-3])
        ppl = meta.get("people", "").split()
        out.append({"slug": slug, "title": meta.get("title", slug), "subtitle": meta.get("subtitle", ""),
                    "line": meta.get("line", ""), "people": ppl, "steps": steps})
    return out, problems


# ---------- one-sentence summaries ----------

def date_phrase(d):
    """A GEDCOM date as words that keep its certainty: 'on 12 May 1765', 'in July 1839', 'about 1580', 'before 1700'."""
    d = (d or "").strip()
    if not d:
        return ""
    def words(x):
        m = re.fullmatch(r"(?:(\d{1,2}) )?(?:([A-Z]{3}) )?(\d{4})", x.strip())
        if not m:
            return None
        mon = MONTH_NAMES[MONTHS[m.group(2)]] if m.group(2) in MONTHS else ""
        return " ".join(x for x in (str(int(m.group(1))) if m.group(1) and mon else "", mon, m.group(3)) if x)
    m = re.fullmatch(r"(ABT|EST|CAL|BEF|AFT) (.+)", d)
    if m and words(m.group(2)):
        return {"ABT": "about ", "EST": "about ", "CAL": "about ", "BEF": "before ", "AFT": "after "}[m.group(1)] + words(m.group(2))
    m = re.fullmatch(r"BET (.+) AND (.+)", d)
    if m and words(m.group(1)) and words(m.group(2)):
        return f"between {words(m.group(1))} and {words(m.group(2))}"
    w = words(d)
    if not w:
        return ""
    return ("on " if re.match(r"\d{1,2} [A-Z]", w) else "in ") + w


def place_phrase(p):
    parts = [x.strip() for x in short_place(p).split(",") if x.strip()]
    if len(parts) > 2:
        parts = [parts[0], parts[-1]]
    return ", ".join(parts)


def hedge(e):
    """A word of caution taken from the fact's Evidence note, so the summary is no surer than the tree."""
    ev = next((n for n in e.get("notes", []) if n.startswith("Evidence:")), "").lower()
    lvl = ev[len("evidence:"):].strip()
    if lvl.startswith(("lead", "inferred, possible", "source not recorded")):
        return "possibly "
    if lvl.startswith(("inferred, probable", "conflict")):
        return "probably "
    if lvl.startswith("secondary"):
        return "reportedly "
    return ""


def intro_of(k, p, people, fams, names, living):
    """One sentence built only from facts in the tree: occupation, birth, marriage, children, death."""
    evs = [e for e in p["events"] if e["tag"] not in SKIP_TAGS]
    first = lambda *labels: next((e for lab in labels for e in evs if e.get("label") == lab and (e.get("date") or e.get("place"))), None)
    bits = []
    b = first("Born") or first("Christened", "Baptized")
    if b:
        verb = "born" if b["label"] == "Born" else "baptized"
        bits.append(" ".join(x for x in (hedge(b) + verb, date_phrase(b.get("date")), "in " + place_phrase(b["place"]) if b.get("place") else "") if x))
    im = first("Immigrated", "Emigrated")
    if im and date_phrase(im.get("date")):
        bits.append(f"{hedge(im)}{'emigrated' if im['label'] == 'Emigrated' else 'came to America'} {date_phrase(im['date'])}"
                    if "USA" in (im.get("place") or "USA") else f"{hedge(im)}emigrated {date_phrase(im['date'])}")
    kids = 0
    for f in p["fams"]:
        fam = fams[f]
        kids += len(fam["chil"])
        sp = fam["wife"] if fam["husb"] == k else fam["husb"]
        mar = next((e for e in fam["events"] if e.get("label") == "Married"), None)
        if sp and sp not in living and names.get(sp) and not any(x.startswith("married") or " married" in x for x in bits):
            when = date_phrase(mar.get("date")) if mar else ""
            when = re.sub(r"^on \d{1,2} \w+ ", "in ", when)
            bits.append(f"{hedge(mar) if mar else ''}married {names[sp]}" + (" " + when if when else ""))
    if kids:
        bits.append(f"{'father' if p['sex'] == 'M' else 'mother' if p['sex'] == 'F' else 'parent'} of {'one child' if kids == 1 else f'{kids} children'} in the tree")
    d = first("Died") or first("Buried")
    if d and k not in living:
        verb = "died" if d["label"] == "Died" else "buried"
        age = f", aged about {p['dy'] - p['by']}" if p["by"] and p["dy"] and 0 < p["dy"] - p["by"] < 111 and d["label"] == "Died" else ""
        bits.append(" ".join(x for x in (hedge(d) + verb, date_phrase(d.get("date")), "in " + place_phrase(d["place"]) if d.get("place") else "") if x) + age)
    if not bits:
        return ""
    occ = next((e["val"] for e in evs if e.get("label") == "Occupation" and e.get("val") and len(e["val"]) <= 60), "")
    text = "; ".join(bits)
    text = text[0].upper() + text[1:] + "."
    return (occ[0].upper() + occ[1:].rstrip(".") + ". " if occ else "") + text


# ---------- main build ----------

def event_of(e, by, keep_notes):
    y = sort_year(e.get("date"))
    val = e["val"] if e["val"] not in ("", "Y") else ""
    out = {"t": e["label"], "d": pretty_date(e.get("date")), "y": y, "pl": short_place(e.get("place")),
           "pk": e.get("place") or "", "tx": val}
    if y and by and y - by >= 1:
        out["age"] = int(y - by)
    if keep_notes:
        notes = e.get("notes", [])
        ev = next((n for n in notes if n.startswith("Evidence:")), "")
        out["ev"] = ev
        out["nt"] = [n for n in notes if not n.startswith("Evidence:")]
        out["src"] = e.get("sources", [])
    return out


def use_joined_trees():
    """Build from tree.ged plus the joined_trees files, written to a temporary file that is removed at exit."""
    if not SITE_CONF["joined_trees"]:
        return
    import atexit
    import tempfile
    import join_trees
    with open(cfg.TREE, encoding="utf-8") as f:
        text = join_trees.join_all(f.read(), SITE_CONF["joined_trees"], cfg.PROJECT_DIR)
    fd, path = tempfile.mkstemp(prefix="site-tree-", suffix=".ged")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    atexit.register(os.remove, path)
    cfg.TREE = path


def main():
    global RIGHTS
    os.chdir(cfg.PROJECT_DIR)
    RIGHTS = read_json(os.path.join(SRC, "rights.json"), {"show": [], "never": []})
    use_joined_trees()
    root = (SITE_CONF["root"] or cfg.ROOT).strip("@")
    people, fams = livingmod.load()
    if root not in people:
        raise SystemExit(f"build_site: root person @{root}@ is missing from the tree")
    living, unknown, est = livingmod.possibly_living(people, fams)
    living = set(living) | set(unknown)
    sname = surnames()
    names = {k: p["names"][0] if p["names"] else "" for k, p in people.items()}

    graph = {}
    for k, p in people.items():
        par, kids, sp = [], [], []
        for f in p["famc"]:
            par += [x for x in (fams[f]["husb"], fams[f]["wife"]) if x]
        for f in p["fams"]:
            kids += fams[f]["chil"]
            sp += [x for x in (fams[f]["husb"], fams[f]["wife"]) if x and x != k]
        graph[k] = {"p": par, "c": kids, "s": sp}

    def living_desc(k, depth):
        out = set()
        for c in graph[k]["c"]:
            if c in living:
                out.add(c)
            if depth > 1:
                out |= living_desc(c, depth - 1)
        return out

    audience = {k: sorted(living_desc(k, 2)) for k in people if k not in living}
    recent = {k for k, a in audience.items() if a}

    places_raw = read_json(os.path.join(SRC, "places.json"), {})
    for k, v in read_json(os.path.join(SRC, "places-overrides.json"), {}).items():
        if not k.startswith("_"):
            places_raw[k] = dict(places_raw.get(k, {}), **v)
    stories, unmatched = biography_sections(people, names)
    chapters, story_problems = read_stories(people, living, places_raw)

    for d in (SITE, PRIV):
        os.makedirs(d, exist_ok=True)
    pub_people, priv_people, pub_imgs, priv_imgs = {}, {}, {}, {}
    used_places = set()

    for k, p in people.items():
        if k in living:
            pub_people[k] = {"lv": 1, "sx": p["sex"], "famc": p["famc"], "fams": p["fams"]}
        is_private = k in living or k in recent
        evs_pub, evs_priv = [], []
        imgs_pub, imgs_priv = [], []
        for e in p["events"]:
            if e["tag"] in SKIP_TAGS or e["tag"] == "EVEN" and e.get("label") == "Archival Extinction":
                continue
            evs_priv.append(event_of(e, p["by"], True))
            evs_pub.append(event_of(e, p["by"], not is_private))
            if e.get("place"):
                used_places.add(e["place"])
            for c in e.get("sources", []):
                if not allowed(c):
                    continue
                for f in files_in(c):
                    priv_img = is_private or re.search(r"\b19[45]0\b.*census|census.*\b19[45]0\b", c, re.I)
                    key = make_image(f, bool(priv_img), priv_imgs if priv_img else pub_imgs, c, k)
                    if key:
                        (imgs_priv if priv_img else imgs_pub).append(key)
        for x in (evs_pub, evs_priv):
            x.sort(key=lambda e: (e["y"] is None, e["y"] or 0))
        notes = [e["val"] for e in p["events"] if e["tag"] == "NOTE"]
        rec = {"n": names[k], "sn": sname.get(k, ""), "alt": p["names"][1:], "sx": p["sex"], "by": p["by"], "dy": p["dy"],
               "est": None if p["by"] else (est.get(k) or [None])[0], "famc": p["famc"], "fams": p["fams"],
               "link": p["link"], "regions": sorted({r for e in p["events"] for r in regions(e.get("place"))})}
        story = stories.get(k, [])
        rec["intro"] = intro_of(k, p, people, fams, names, living)
        if k in living:
            priv_people[k] = dict(rec, lv=1, ev=evs_priv, story=story, notes=notes, img=sorted(set(imgs_priv + imgs_pub)))
            continue
        pub_people[k] = dict(rec, ev=evs_pub, img=sorted(set(imgs_pub)),
                             story=[] if k in recent else story, notes=[] if k in recent else notes, rc=1 if k in recent else 0)
        if k in recent:
            priv_people[k] = {"ev": evs_priv, "story": story, "notes": notes, "img": sorted(set(imgs_priv))}

    pub_fams, priv_fams = {}, {}
    for f, fam in fams.items():
        members = [x for x in (fam["husb"], fam["wife"]) if x]
        has_living = any(x in living for x in members)
        evs = []
        for e in fam["events"]:
            if e["tag"] in SKIP_TAGS:
                continue
            evs.append(event_of(e, None, not has_living and not any(x in recent for x in members)))
            if e.get("place"):
                used_places.add(e["place"])
        pub_fams[f] = {"h": fam["husb"], "w": fam["wife"], "c": fam["chil"], "ev": [] if has_living else evs}
        if has_living:
            priv_fams[f] = {"ev": [event_of(e, None, True) for e in fam["events"] if e["tag"] not in SKIP_TAGS]}

    places = {}
    for pl in used_places:
        r = places_raw.get(pl)
        if r and r.get("precision") == "place":
            places[pl] = {"lat": r["lat"], "lon": r["lon"], "name": short_place(r.get("label") or pl),
                          "approx": bool(r.get("dropped")) or bool(r.get("approx"))}

    # Lines to follow on the journey view: per surname, the earliest direct ancestor.
    anc, stack = {}, [(root, 0)]
    while stack:
        k, g = stack.pop()
        for par in graph[k]["p"]:
            if par not in anc:
                anc[par] = g + 1
                stack.append((par, g + 1))
    by_sn = {}
    for k in anc:
        s = sname.get(k)
        if s and people[k]["by"]:
            by_sn.setdefault(s, []).append(k)
    lines = []
    for s, ks in by_sn.items():
        if len(ks) < 2:
            continue
        founder = min(ks, key=lambda k: people[k]["by"])
        chain, cur = [founder], founder
        while cur != root:
            nxt = next((c for c in graph[cur]["c"] if c == root or c in anc), None)
            if not nxt:
                break
            chain.append(nxt)
            cur = nxt
        chain = [c for c in chain if c not in living]
        lines.append({"sn": s, "founder": founder, "chain": chain, "gens": len(ks)})
    lines.sort(key=lambda l: (-l["gens"], people[l["founder"]]["by"]))

    history = read_json(os.path.join(SRC, "history-events.json"), {}).get("events", [])

    # Military service routes (site-src/service.json): only for people who are in the tree and not living.
    service = []
    for s in read_json(os.path.join(SRC, "service.json"), {}).get("service", []):
        if s["person"] not in people:
            story_problems.append(f"service.json: no such person {s['person']}")
        elif s["person"] in living:
            raise SystemExit(f"service.json: {s['person']} is living and may not appear")
        else:
            service.append(s)
    site = {k: SITE_CONF[k] for k in ("name", "owner_name", "root_name", "story_footer", "history_footer", "origins_note",
                                      "request_example", "regions", "countries")}
    public = {"built": date.today().isoformat(), "root": root, "site": site, "people": pub_people, "fams": pub_fams,
              "places": places, "images": pub_imgs, "history": history, "lines": lines, "stories": chapters, "service": service}
    private = {"built": public["built"], "people": priv_people, "fams": priv_fams, "images": priv_imgs,
               "graph": graph, "living": sorted(living), "audience": {k: audience[k] for k in recent},
               "names": {k: names[k] for k in living}}

    os.makedirs(os.path.join(SITE, "data"), exist_ok=True)
    for path, data in ((os.path.join(SITE, "data", "public.json"), public), (os.path.join(PRIV, "private.json"), private)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    # Version stamps on the script and stylesheet links, so browsers fetch new copies after a deploy.
    with open(os.path.join(SRC, "index.html"), encoding="utf-8") as f:
        page = f.read().replace("{{SITE_NAME}}", html.escape(SITE_CONF["name"]))
    for f in ("app.js", "style.css"):
        shutil.copy(os.path.join(SRC, f), os.path.join(SITE, f))
        with open(os.path.join(SRC, f), "rb") as fh:
            stamp = hashlib.sha1(fh.read()).hexdigest()[:10]
        page = page.replace(f'"{f}"', f'"{f}?v={stamp}"')
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    unplaced = sorted(pl for pl in used_places if pl not in places)
    print(f"site/: {len(pub_people)} people ({len(living)} living as stubs, {len(recent)} recent), "
          f"{len(pub_imgs)} public images, {len(places)} mapped places, {len(lines)} lines, {len(history)} historical events")
    print(f"private/: {len(priv_people)} records, {len(priv_imgs)} private images")
    print(f"{len(service)} service records")
    print(f"{len(chapters)} story chapters" + (": " + "; ".join(story_problems) if story_problems else ""))
    print(f"stories matched to {len(stories)} people; {len(unmatched)} headings unmatched; {len(unplaced)} places not mapped")
    if REPORT:
        print("\nUnmatched story headings:\n  " + "\n  ".join(unmatched))
        print("\nPlaces not on the map:\n  " + "\n  ".join(unplaced))


if __name__ == "__main__":
    main()
