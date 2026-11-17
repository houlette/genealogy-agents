#!/usr/bin/env python3
"""Build tree-view.html, an interactive pedigree chart, from tree.ged.

Usage: python3 make-tree-view.py   (writes tree-view.html in the project folder)

The page is self-contained and stays on this computer: it holds living
relatives' details, so it is not meant to be published.
"""
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import project_config as cfg  # noqa: E402

TAGS = {
    "BIRT": "Born", "BAPM": "Baptized", "CHR": "Christened", "DEAT": "Died",
    "BURI": "Buried", "MARR": "Married", "DIV": "Divorced", "RESI": "Lived",
    "OCCU": "Occupation", "EDUC": "Education", "IMMI": "Immigrated",
    "RELI": "Religion", "DSCR": "Description", "EVEN": "Event", "PROP": "Property",
    "ORDN": "Ordained", "NOTE": "Note", "SOUR": "Source", "OBJE": "Media",
    "NATU": "Naturalized", "CENS": "Census", "EMIG": "Emigrated",
}


def parse(text):
    recs = {}
    for chunk in re.split(r"(?m)^(?=0 )", text):
        m = re.match(r"0 @(\w+)@ (INDI|FAM)", chunk)
        if not m:
            continue
        rid, typ = m.groups()
        facts, cur = [], None
        for line in chunk.splitlines()[1:]:
            lm = re.match(r"(\d) (\w+)(?: (.*))?$", line)
            if not lm:
                continue
            lvl, tag, val = int(lm.group(1)), lm.group(2), (lm.group(3) or "")
            if lvl == 1:
                cur = {"tag": tag, "val": val, "sub": []}
                facts.append(cur)
            elif cur is not None:
                cur["sub"].append([tag, val])
        recs[rid] = {"type": typ, "facts": facts}
    return recs


def year(d):
    m = re.search(r"(\d{4})", d or "")
    return int(m.group(1)) if m else None


def build(recs):
    people, fams = {}, {}
    for rid, r in recs.items():
        if r["type"] == "FAM":
            f = {"husb": None, "wife": None, "chil": [], "events": []}
            for x in r["facts"]:
                if x["tag"] == "HUSB": f["husb"] = x["val"].strip("@")
                elif x["tag"] == "WIFE": f["wife"] = x["val"].strip("@")
                elif x["tag"] == "CHIL": f["chil"].append(x["val"].strip("@"))
                else: f["events"].append(fact(x))
            fams[rid] = f
    for rid, r in recs.items():
        if r["type"] != "INDI":
            continue
        p = {"names": [], "sex": "", "famc": [], "fams": [], "events": [], "nsfx": ""}
        for x in r["facts"]:
            t = x["tag"]
            if t == "NAME":
                p["names"].append(x["val"].replace("/", "").strip())
                for st, sv in x["sub"]:
                    if st == "NSFX": p["nsfx"] = sv
            elif t == "SEX": p["sex"] = x["val"]
            elif t == "FAMC": p["famc"].append(x["val"].strip("@"))
            elif t == "FAMS": p["fams"].append(x["val"].strip("@"))
            else: p["events"].append(fact(x))
        b = next((e for e in p["events"] if e["tag"] in ("BIRT", "BAPM", "CHR") and e.get("date")), None)
        d = next((e for e in p["events"] if e["tag"] in ("DEAT", "BURI") and e.get("date")), None)
        p["by"] = year(b["date"]) if b else None
        p["dy"] = year(d["date"]) if d else None
        p["died"] = any(e["tag"] in ("DEAT", "BURI") for e in p["events"])
        bp = next((e for e in p["events"] if e["tag"] in ("BIRT", "BAPM", "CHR") and e.get("place")), None)
        p["bplace"] = bp["place"] if bp else ""
        ext = next((e for e in p["events"] if e.get("label") == "Archival Extinction"), None)
        p["extinct"] = bool(ext)
        p["extinct_reason"] = " ".join(ext.get("notes", [])) if ext else ""
        p["extinct_sources"] = ext.get("sources", []) if ext else []
        link = next((e for e in p["events"] if e["tag"] == "NOTE" and re.match(r"Parents: (established|probable|hypothesis)", e.get("val", ""))), None)
        p["link"] = re.match(r"Parents: (\w+)", link["val"]).group(1) if link else ""
        p["link_note"] = link["val"] if link else ""
        people[rid] = p
    return people, fams


def fact(x):
    e = {"tag": x["tag"], "label": TAGS.get(x["tag"], x["tag"].title()), "val": x["val"]}
    for st, sv in x["sub"]:
        if st == "DATE": e["date"] = sv
        elif st == "PLAC": e["place"] = sv
        elif st == "ADDR": e["place"] = sv + (", " + e["place"] if e.get("place") else "")
        elif st == "TYPE": e["label"] = sv
        elif st == "CAUS": e.setdefault("notes", []).append("Cause: " + sv)
        elif st == "NOTE": e.setdefault("notes", []).append(sv)
        elif st == "SOUR": e.setdefault("sources", []).append(sv)
        elif st == "NSFX": pass
    return e


def main():
    recs = parse(open(cfg.TREE, encoding="utf-8").read())
    people, fams = build(recs)
    data = {"people": people, "fams": fams, "root": cfg.ROOT.strip("@"), "built": date.today().isoformat()}
    here = os.path.dirname(os.path.abspath(__file__))
    html = open(os.path.join(here, "tree-view.template.html"), encoding="utf-8").read()
    html = html.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    open(os.path.join(cfg.PROJECT_DIR, "tree-view.html"), "w", encoding="utf-8").write(html)
    print(f"tree-view.html: {len(people)} people, {len(fams)} families")


if __name__ == "__main__":
    main()
