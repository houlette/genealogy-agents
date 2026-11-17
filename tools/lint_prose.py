#!/usr/bin/env python3
"""Check new prose for certainty words and formatting the rules forbid.

Run from the project root before committing:

    python3 tools/lint_prose.py                 # lines added since HEAD, plus new untracked .md files
    python3 tools/lint_prose.py --rev A..B      # lines added in a commit range (for the weekly review)
    python3 tools/lint_prose.py --message FILE  # also check a commit message
    python3 tools/lint_prose.py --only-message FILE  # check only a commit message (used by tools/hooks/commit-msg)

It reads only added lines, so older text does not flood the output, and it
skips framework files the project has not changed (listed with their hashes in
.framework-lock.json), whose rules quote the forbidden words on purpose. It flags:

- certainty words that AGENTS.md (certainty rule 8 and evidence rule 9) allows
  only for "direct, original" evidence or not at all: proves, proven, proof
  (but not "proved", the legal term for a will),
  definitively, verified, confirmed, RESOLVED, resolved, exhaustive, conclusively;
- em dashes, which the project's style rules forbid;
- in tasks.md, a row marked "done" that reports a negative or exhaustive search,
  which stays "for review" until a different agent has repeated it (AGENTS.md,
  "The task queue, the review flag and the weekly review").

Quoted record text is exempt from the style rules, but this script cannot tell
quotation from prose, so treat each hit as a prompt: reword it, or leave it if
it is inside a quotation or the evidence is "direct, original". Exit status is
1 when anything is flagged, 0 otherwise.
"""
import hashlib
import json
import re
import subprocess
import sys

WORDS = re.compile(r"\b(prov(?:es|en|ing)|proof|definitive(?:ly)?|conclusive(?:ly)?|verified|"
                   r"confirm(?:s|ed)|resolved?|resolves|RESOLVED|exhaustive(?:ly)?)\b", re.I)
NEGATIVE = re.compile(r"\b(negative|exhaustive|not found|no (?:record|entry|trace)|absent)\b", re.I)
FILES = re.compile(r"\.(md|ged|txt)$")


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's empty tree, for a repository with no commits


def framework_text():
    """Paths of framework-owned files the project has not changed (from .framework-lock.json).

    Their prose is the framework's, and the rule files quote the forbidden words
    on purpose, so they are not linted. A file the project has edited is linted."""
    try:
        with open(".framework-lock.json", encoding="utf-8") as f:
            files = json.load(f).get("files", {})
    except (OSError, ValueError):
        return set()
    out = set()
    for path, digest in files.items():
        try:
            with open(path, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == digest:
                    out.add(path)
        except OSError:
            pass
    return out


def added_lines(rev):
    """Yield (file, line number, text) for each line added in the diff."""
    base = rev
    if not base:
        has_head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"],
                                  capture_output=True).returncode == 0
        base = "HEAD" if has_head else EMPTY_TREE
    cmd = ["git", "diff", "-U0", "--no-color"] + base.split()
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    path, num = None, 0
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            num = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and path:
            yield path, num, line[1:]
            num += 1
    if not rev:
        new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                             capture_output=True, text=True, check=True).stdout.split()
        for path in new:
            if path.endswith(".md"):
                for num, text in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                    yield path, num, text.rstrip("\n")


def check(path, num, text):
    hits = []
    for m in WORDS.finditer(text):
        hits.append(f"certainty word '{m.group(0)}'")
    if "—" in text:
        hits.append("em dash")
    if path == "tasks.md" and text.startswith("|"):
        status = text.rstrip(" |").rsplit(" | ", 1)[-1]
        if status.lower().startswith("done") and NEGATIVE.search(status):
            hits.append("negative result marked done; set it to 'for review' for a second agent")
    return [f"{path}:{num}: {h}: {text.strip()[:120]}" for h in hits]


def main():
    args = sys.argv[1:]
    rev = args[args.index("--rev") + 1] if "--rev" in args else None
    found = []
    only_msg = "--only-message" in args
    if only_msg:
        args += ["--message", args[args.index("--only-message") + 1]]
    skip = framework_text()
    for path, num, text in ([] if only_msg else added_lines(rev)):
        if FILES.search(path) and not path.startswith("tree-view") and path not in skip:
            found += check(path, num, text)
    if "--message" in args:
        msg = args[args.index("--message") + 1]
        for num, text in enumerate(open(msg, encoding="utf-8"), 1):
            found += check("commit message", num, text.rstrip("\n"))
    for f in found:
        print(f)
    print(f"lint_prose: {len(found)} flagged" + ("; reword, or leave if quoted or 'direct, original'" if found else ""))
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
