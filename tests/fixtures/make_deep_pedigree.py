#!/usr/bin/env python3
"""Write tests/fixtures/deep-pedigree.ged, an invented 27-generation pedigree.

None of these people existed. The tree is shaped to test the website's fan
chart and timeline at depth: every ancestor is known for the first four
generations, about half the branches go on to generation ten, two father
lines run on alone (one to generation 27, one to generation 21), two
parent links are graded probable and hypothesis, a few
people have no recorded birth year (so the site has to estimate one), and
one couple in generation 5 are first cousins, so their shared grandparents
are reached twice (pedigree collapse). The root and parents are living.

Usage: python3 tests/fixtures/make_deep_pedigree.py   (rewrites the .ged beside it)
Compiled by Claude, 21 November 2026.
"""
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deep-pedigree.ged")
MEN = "Anton Bernd Carl Dieter Emil Felix Georg Hugo Ivo Jakob Konrad Lukas Moritz Nils Otto Paul Quirin Rolf Simon Theo Urban Veit Walter".split()
WOMEN = "Anna Berta Cora Dora Elsa Frieda Greta Hanna Ida Jutta Klara Lene Marta Nora Olga Paula Rosa Selma Tilda Ursel Vera Wilma".split()
SURNAMES = "Fischer Keller Lang Moreau Petit Dubois Carter Hale Webb Novak Berg Vogel Roux Blanc Marsh Pike Thorne Falk Stein Lorne Quill Ashby".split()
PLACES = ["Musterdorf, Hessen, Germany", "Beispielstadt, Bayern, Germany", "Exempleville, Normandie, France",
          "Sampleton, Kent, England", "Springfield, Ohio, USA"]

# Parent-link grades on two people, so the charts draw their parents with dashed (probable) and dotted (theory) edges.
LINK_NOTES = {"MF": "probable. One identity point, set out in an invented dossier.",
              "MFM": "hypothesis (link kept). Too few identity points; an invented example."}
rng = random.Random(27)
people, fams = [], []          # lists of GEDCOM line lists
fam_of = {}                    # path -> family id whose children include the person at path
COLLAPSE_CHILD = "FFMFMM"      # B: is made a child of A's parents, A = "FFMFFF"
COLLAPSE_SOURCE = "FFMFFF"


def new_id(prefix, n=[0, 0]):
    i = 0 if prefix == "I" else 1
    n[i] += 1
    return f"@{prefix}{n[i]}@"


def place_for(g):
    if g <= 2:
        return PLACES[4]
    if g <= 5:
        return rng.choice(PLACES[1:])
    return rng.choice(PLACES[:4])


def shape(path, g):
    """Which parents this person has: 'both', 'father' or 'none'."""
    if g < 4 or path in ("FFMF", "FFMFF", "FFMFM", COLLAPSE_SOURCE):
        return "both"
    if set(path) == {"F"}:                     # the root's own father line: to generation 27
        return "both" if g in (8, 14) else "father" if g < 27 else "none"
    if path.startswith("MMF") and set(path[3:]) <= {"F"}:   # a second father line, to generation 21
        return "father" if g < 21 else "none"
    if g >= 10:
        return "none"
    r = rng.random()
    return "both" if r < 0.5 else "father" if r < 0.75 else "none"


def person(path, g, sex, surname):
    if path == COLLAPSE_CHILD:                 # a sister of A, so she carries A's surname
        surname = surname_at[COLLAPSE_SOURCE]
    surname_at[path] = surname
    pid = new_id("I")
    given = rng.choice(MEN if sex == "M" else WOMEN)
    born = 1990 - 29 * g + rng.randint(-4, 4)
    rec = [f"0 {pid} INDI", f"1 NAME {given} /{surname}/", f"1 SEX {sex}"]
    no_birth_year = g >= 6 and rng.random() < 0.12
    if g <= 1:
        rec += ["1 BIRT", f"2 DATE {born}", f"2 PLAC {place_for(g)}"]      # living: no death
    else:
        if not no_birth_year:
            rec += ["1 BIRT", f"2 DATE {born}", f"2 PLAC {place_for(g)}"]
        if rng.random() < 0.8 or g == 2:
            rec += ["1 DEAT", f"2 DATE {born + rng.randint(35, 85)}"]
        else:
            rec += ["1 DEAT Y"]
    if path == COLLAPSE_CHILD:
        rec.append(f"1 FAMC {fam_of[COLLAPSE_SOURCE]}")
        fams_by_id[fam_of[COLLAPSE_SOURCE]].append(f"1 CHIL {pid}")
        people.append(rec)
        return pid, rec
    kind = shape(path, g)
    if kind != "none":
        fid = new_id("F")
        fam = [f"0 {fid} FAM"]
        fams.append(fam)
        fams_by_id[fid] = fam
        fam_of[path] = fid
        father, _ = person(path + "F", g + 1, "M", surname)
        fam.append(f"1 HUSB {father}")
        if kind == "both":
            mother, _ = person(path + "M", g + 1, "F", rng.choice(SURNAMES))
            fam.append(f"1 WIFE {mother}")
        fam.append(f"1 CHIL {pid}")
        rec.append(f"1 FAMC {fid}")
        if path in LINK_NOTES:
            rec.append(f"1 NOTE Parents: {LINK_NOTES[path]}")
    people.append(rec)
    return pid, rec


fams_by_id = {}
surname_at = {}


def add_fams_links():
    """FAMS lines on each spouse, from the family records."""
    by_id = {r[0].split()[1]: r for r in people}
    for fam in fams:
        fid = fam[0].split()[1]
        for line in fam[1:]:
            tag, who = line.split()[1:3]
            if tag in ("HUSB", "WIFE"):
                by_id[who].append(f"1 FAMS {fid}")


def main():
    person("", 0, "M", "Adler")
    add_fams_links()
    people.sort(key=lambda r: int(r[0].split()[1][2:-1]))
    fams.sort(key=lambda r: int(r[0].split()[1][2:-1]))
    head = ["0 HEAD", "1 SOUR genealogy-agents", "1 GEDC", "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8",
            "1 NOTE An invented 27-generation pedigree for the website tests (tests/fixtures/make_deep_pedigree.py). None of these people existed."]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(head) + "\n\n")
        for r in people + fams:
            f.write("\n".join(r) + "\n\n")
        f.write("0 TRLR\n")
    print(f"{OUT}: {len(people)} people, {len(fams)} families")


if __name__ == "__main__":
    main()
