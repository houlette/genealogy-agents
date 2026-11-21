#!/usr/bin/env python3
"""Find map coordinates for every place in tree.ged, for the family website.

Usage: python3 tools/geocode_places.py          (only places not yet cached)
       python3 tools/geocode_places.py --redo   (all places again)

Results go to site-src/places.json, keyed by the exact PLAC text. Each entry
records the query that matched, the OpenStreetMap name it matched, and a
precision: "place" (a town, village or finer, plotted on the map), "region"
(a county, state or country, not plotted), or "none" (no match). Street
addresses, cemeteries and wards are dropped one part at a time from the front
until something matches, so most entries are at town level; the "dropped"
field says what was left out. Hand corrections go in site-src/places-overrides.json
and always win. Old or local place names that OpenStreetMap does not know are
rewritten first ("geocode_rewrites" in site-src/site.json; see DEFAULT_REWRITE).
Uses the free OpenStreetMap Nominatim service, one request a second as its
usage policy asks. Compiled by Claude, 9 November 2026.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project_config as cfg  # noqa: E402

OUT = os.path.join(cfg.PROJECT_DIR, "site-src", "places.json")
SITE_JSON = os.path.join(cfg.PROJECT_DIR, "site-src", "site.json")
UA = {"User-Agent": "genealogy-agents-site-builder/1.0 (private family history site)"}
# Rewrites applied in order before a place is sent. The first three keep the
# query to a settlement; the rest map old state names to ones OpenStreetMap
# knows. A project replaces the whole list with "geocode_rewrites" in
# site-src/site.json (copy these and add its own old or local names), and
# should keep the first rule, so that street addresses (living relatives'
# among them) never leave the machine.
DEFAULT_REWRITE = [
    (r"^\d[^,]*, ", ""),  # a leading part that starts with a house number
    (r"\s*\([^)]*\)", ""),  # remarks in brackets: "(now ...)", "(?)"
    (r"(^|, )[^,]* parish, ", r"\1"),  # "Musterdorf parish, Kreis ..."
    (r", Thuringia, Germany$", ", Thüringen, Germany"),
    (r", Mecklenburg-Schwerin, Germany$", ", Mecklenburg-Vorpommern, Germany"),
    (r", Hessen-Darmstadt$", ", Hessen, Germany"),
    (r", Hessen$", ", Hessen, Germany"),
    (r", Pfalz, Bayern, Germany$", ", Rheinland-Pfalz, Germany"),
    (r", Pfalz$", ", Rheinland-Pfalz, Germany"),
    (r", Baden(, Germany)?$", ", Baden-Württemberg, Germany"),
    (r", Normandie, France$", ", France"),
    (r", Picardie, France$", ", France"),
    (r", Heiliges Römisches Reich$", ", Germany"),
    (r", Prussia$", ""),
]


def load_rewrites(path=SITE_JSON):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rules = json.load(f).get("geocode_rewrites")
        if rules:
            return [(pat, rep) for pat, rep in rules]
    return list(DEFAULT_REWRITE)


REWRITE = load_rewrites()


def rewrite(place, rules=None):
    q = place
    for pat, rep in (REWRITE if rules is None else rules):
        q = re.sub(pat, rep, q)
    return q


def query(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 1, "accept-language": "en"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                res = json.load(r)
            time.sleep(1.1)
            return res[0] if res else None
        except Exception as e:  # network or server error: wait and retry
            print("  retry:", e, file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    return "error"


def geocode(place):
    q = rewrite(place)
    parts = [p.strip() for p in q.split(",") if p.strip()]
    for i in range(len(parts)):
        sub = ", ".join(parts[i:])
        r = query(sub)
        if r == "error":
            return {"precision": "error", "query": sub}
        if r:
            rank = int(r.get("place_rank", 0))
            return {
                "lat": round(float(r["lat"]), 4), "lon": round(float(r["lon"]), 4),
                "precision": "place" if rank >= 13 else "region",
                "query": sub, "dropped": ", ".join(parts[:i]), "osm": r.get("display_name", "")[:120],
            }
    return {"precision": "none", "query": q}


def save(cache):
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)


def main():
    redo = "--redo" in sys.argv
    texts = []
    with open(cfg.TREE, encoding="utf-8") as f:
        texts.append(f.read())
    if os.path.exists(SITE_JSON):  # the joined_trees files of the website (tools/join_trees.py)
        with open(SITE_JSON, encoding="utf-8") as f:
            joined = json.load(f).get("joined_trees") or []
        for entry in joined:
            with open(os.path.join(cfg.PROJECT_DIR, entry["file"]), encoding="utf-8-sig") as f:
                texts.append(f.read())
    places = sorted({m.group(1).strip() for t in texts for m in re.finditer(r"(?m)^2 PLAC (.+)$", t)})
    cache = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            cache = json.load(f)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    todo = [p for p in places if redo or p not in cache or cache[p].get("precision") == "error"]
    print(f"{len(places)} places, {len(todo)} to look up")
    for n, p in enumerate(todo, 1):
        cache[p] = geocode(p)
        print(f"[{n}/{len(todo)}] {cache[p]['precision']:6} {p}")
        if n % 20 == 0:
            save(cache)
    save(cache)
    counts = {}
    for p in places:
        counts[cache[p]["precision"]] = counts.get(cache[p]["precision"], 0) + 1
    print("done:", counts)


if __name__ == "__main__":
    main()
