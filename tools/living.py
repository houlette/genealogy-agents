#!/usr/bin/env python3
"""List the people in tree.ged who may be living, for the owner to review.

Usage: python3 tools/living.py

A person counts as possibly living when tree.ged has no death or burial for
them and their birth year, recorded or estimated, is after the cutoff year
("living_cutoff" in project.json; by default 100 years before this year). When there is no birth year, it is estimated from the
nearest relatives: a parent's birth year plus 25, a child's minus 25, or a
spouse's. People with no estimate at all are listed separately, since the
viewer and any publishing step must treat them as living until someone checks.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import project_config as cfg  # noqa: E402

CUTOFF = cfg.LIVING_CUTOFF


def load():
    spec = importlib.util.spec_from_file_location("mtv", os.path.join(ROOT, "make-tree-view.py"))
    mtv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mtv)
    return mtv.build(mtv.parse(open(cfg.TREE, encoding="utf-8").read()))


def estimates(people, fams):
    est = {k: (p["by"], "recorded") for k, p in people.items() if p["by"]}
    for _ in range(6):  # spread estimates a few generations
        for k, p in people.items():
            if k in est:
                continue
            for f in p["famc"]:
                for par in (fams[f]["husb"], fams[f]["wife"]):
                    if par in est:
                        est[k] = (est[par][0] + 25, f"parent {people[par]['names'][0]}")
            for f in p["fams"]:
                fam = fams[f]
                for c in fam["chil"]:
                    if c in est and k not in est:
                        est[k] = (est[c][0] - 25, f"child {people[c]['names'][0]}")
                sp = fam["wife"] if fam["husb"] == k else fam["husb"]
                if sp in est and k not in est:
                    est[k] = (est[sp][0], f"spouse {people[sp]['names'][0]}")
    return est


def possibly_living(people, fams):
    est = estimates(people, fams)
    living, unknown = [], []
    for k, p in people.items():
        if p["died"]:
            continue
        if k not in est:
            unknown.append(k)
        elif est[k][0] > CUTOFF:
            living.append(k)
    return living, unknown, est


def main():
    people, fams = load()
    living, unknown, est = possibly_living(people, fams)
    print(f"Possibly living (no death record, born or estimated after {CUTOFF}): {len(living)}")
    for k in sorted(living, key=lambda k: est[k][0]):
        y, how = est[k]
        print(f"  @{k}@ {people[k]['names'][0]}, born {'' if how == 'recorded' else 'about '}{y}{'' if how == 'recorded' else ' (from ' + how + ')'}")
    print(f"No birth year and no relative to estimate from (treated as living): {len(unknown)}")
    for k in sorted(unknown):
        print(f"  @{k}@ {people[k]['names'][0]}")


if __name__ == "__main__":
    main()
