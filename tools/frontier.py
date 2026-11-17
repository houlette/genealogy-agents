#!/usr/bin/env python3
"""List the root person's direct ancestors whose parents are not yet in tree.ged.

Run from the project root:

    python3 tools/frontier.py              # every frontier ancestor, nearest generation first
    python3 tools/frontier.py --max-gen 6  # only generations up to 6 (root person = 0)
    python3 tools/frontier.py --clean      # leave out people on lines with warning flags

Columns: generation, ID, name and dates, the great-grandparent line (the
generation-3 ancestor the person descends through), how many parents are
missing, and flags:

    unverified   the person carries "Unverified: secondary source only"
    for-review   the person carries a "For review:" note
    no-source    a fact is marked "Evidence: source not recorded"
    weak-line    a descendant between this person and the root is unverified or for review
    hypothesis   candidate parents are recorded as a theory ("Parents: hypothesis"),
                 not yet entered; a good target for more identity points

Extend a flagged line only after its verification task is done (AGENTS.md,
evidence rules 4 and 5). The list is the reserve for tasks.md: move a few
rows into the queue when it runs low rather than copying the whole list.
"""
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import person as P  # noqa: E402
import project_config as cfg  # noqa: E402

ROOT = cfg.ROOT


def main():
    max_gen = None
    if "--max-gen" in sys.argv:
        max_gen = int(sys.argv[sys.argv.index("--max-gen") + 1])
    clean = "--clean" in sys.argv  # hides warning flags; keeps hypotheses
    R = P.load()
    if ROOT not in R:
        sys.exit(f"frontier: root person {ROOT} is missing from the tree")

    def parents(pid):
        fams = P.values(R[pid], "FAMC")
        if not fams or fams[0] not in R:
            return []
        return [p for role in ("HUSB", "WIFE") for p in P.values(R[fams[0]], role)]

    def flags(pid):
        text = " ".join(line for _, line in R[pid]["lines"])
        out = []
        if "Unverified: secondary source only" in text:
            out.append("unverified")
        if "For review:" in text:
            out.append("for-review")
        if "Evidence: source not recorded" in text:
            out.append("no-source")
        if "Parents: hypothesis" in text:
            out.append("hypothesis")
        return out

    gen, line, weak = {ROOT: 0}, {ROOT: ""}, {ROOT: False}
    queue, rows = [ROOT], []
    while queue:
        pid = queue.pop(0)
        own = flags(pid)
        par = parents(pid)
        if len(par) < 2 and pid != ROOT:
            f = own + (["weak-line"] if weak[pid] else [])
            rows.append((gen[pid], pid, line[pid], 2 - len(par), f))
        for p in par:
            if p in gen:
                continue
            gen[p] = gen[pid] + 1
            line[p] = P.label(R, p).split(" (")[0].split(" ", 1)[1] if gen[p] == 3 else line[pid]
            weak[p] = weak[pid] or any(f in ("unverified", "for-review") for f in own)
            queue.append(p)

    shown = 0
    for g, pid, ln, missing, f in sorted(rows):
        if max_gen is not None and g > max_gen:
            continue
        if clean and set(f) - {"hypothesis"}:
            continue
        shown += 1
        print(f"{g:>2}  {P.label(R, pid)}  | line: {ln} | missing {missing} | {', '.join(f) or '-'}")
    print(f"\n{shown} shown of {len(rows)} frontier ancestors ({len(gen) - 1} direct ancestors in all)")


if __name__ == "__main__":
    main()
