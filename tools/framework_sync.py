#!/usr/bin/env python3
"""Copy the genealogy-agents framework into a project, and keep it up to date.

    python3 tools/framework_sync.py init ~/my-family-tree     # start a new project
    python3 tools/framework_sync.py status --project ~/my-family-tree
    python3 tools/framework_sync.py sync --project ~/my-family-tree
    python3 tools/framework_sync.py diff AGENTS.md --project ~/my-family-tree

Run it from the framework's own copy (the default --framework is the folder
this script sits in), or from a project's copy with --framework pointing at a
clone of the framework, or set GENEALOGY_FRAMEWORK. The default --project is
the current folder.

framework.json in the framework lists two kinds of file:

- owned files (AGENTS.md, the rule files, tools/, tests/, the Claude Code
  agents) belong to the framework. Sync copies a new version into the project
  only if the project's copy is unchanged since the last sync, so a project's
  own edits are never overwritten. A file the project has changed is reported
  as "changed here" (port the change back to the framework, or overwrite it
  with --force PATH), or as "conflict" when the framework changed it too.
- seeds (PROJECT.md, project.json, tree.ged, the logs and queues, under
  templates/) are copied once, when the project does not have them, and then
  belong to the project. A seed the project deletes is not copied again.

The project records what it last received in .framework-lock.json (commit it),
with a SHA-256 hash of each owned file. Files the framework no longer has are
reported as "removed upstream" and deleted only with --prune, and only when
the project has not changed them. --dry-run shows what sync would do.
Exit status: 0, or 1 when sync left conflicts or files changed here.
"""
import argparse
import difflib
import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date

LOCK = ".framework-lock.json"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(fw):
    path = os.path.join(fw, "framework.json")
    if not os.path.exists(path):
        sys.exit(f"framework_sync: no framework.json in {fw}; pass --framework <clone of genealogy-agents>")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def owned_files(fw, manifest):
    """Relative paths of the framework's owned files, sorted."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(fw):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "templates")]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), fw).replace(os.sep, "/")
            if any(fnmatch.fnmatchcase(rel, pat) and rel.count("/") == pat.count("/") for pat in manifest["owned"]):
                out.add(rel)
    return sorted(out)


def read_lock(project):
    path = os.path.join(project, LOCK)
    if not os.path.exists(path):
        return {"files": {}, "seeds": []}
    with open(path, encoding="utf-8") as f:
        lock = json.load(f)
    lock.setdefault("files", {})
    lock.setdefault("seeds", [])
    return lock


def write_lock(project, lock, fw, manifest):
    try:
        commit = subprocess.run(["git", "-C", fw, "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or None
    except OSError:
        commit = None
    lock.update({"framework": manifest.get("name", "genealogy-agents"), "commit": commit,
                 "synced": date.today().isoformat()})
    lock["files"] = dict(sorted(lock["files"].items()))
    lock["seeds"] = sorted(set(lock["seeds"]))
    with open(os.path.join(project, LOCK), "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=2)
        f.write("\n")


def plan(fw, project, manifest, lock):
    """Return a list of (status, path) for owned files and seeds."""
    rows = []
    files = owned_files(fw, manifest)
    for rel in files:
        new = sha(os.path.join(fw, rel))
        dest = os.path.join(project, rel)
        base = lock["files"].get(rel)
        if not os.path.exists(dest):
            rows.append(("deleted here" if base else "add", rel))
            continue
        cur = sha(dest)
        if cur == new:
            rows.append(("same", rel))
        elif base is None:
            rows.append(("conflict", rel))  # present but never synced, and different
        elif cur == base:
            rows.append(("update", rel))
        elif new == base:
            rows.append(("changed here", rel))
        else:
            rows.append(("conflict", rel))
    for rel, base in lock["files"].items():
        if rel in files:
            continue
        dest = os.path.join(project, rel)
        if not os.path.exists(dest):
            continue
        rows.append(("removed upstream" if sha(dest) == base else "removed upstream, changed here", rel))
    for src, rel in manifest.get("seeds", {}).items():
        if rel in lock["seeds"]:
            continue
        rows.append(("seed present" if os.path.exists(os.path.join(project, rel)) else "seed", rel))
    return rows


def copy(src, dest):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.copy2(src, dest)


def apply(fw, project, manifest, lock, rows, force, prune, dry):
    seeds = {rel: src for src, rel in manifest.get("seeds", {}).items()}
    done, left = [], []
    for status, rel in rows:
        src, dest = os.path.join(fw, rel), os.path.join(project, rel)
        forced = rel in force or "all" in force
        if status in ("add", "update") or (status in ("conflict", "changed here", "deleted here") and forced):
            if not dry:
                copy(src, dest)
                lock["files"][rel] = sha(dest)
            done.append((status if status in ("add", "update") else "overwrite", rel))
        elif status == "same":
            lock["files"][rel] = sha(src)
        elif status in ("conflict", "changed here", "deleted here", "removed upstream, changed here"):
            left.append((status, rel))
        elif status == "removed upstream":
            if prune:
                if not dry:
                    os.remove(dest)
                    lock["files"].pop(rel, None)
                done.append(("removed", rel))
            else:
                left.append((status, rel))
        elif status == "seed":
            if not dry:
                copy(os.path.join(fw, seeds[rel]), dest)
                lock["seeds"].append(rel)
            done.append(("seeded", rel))
        elif status == "seed present":
            lock["seeds"].append(rel)
    return done, left


def report(rows, title):
    if not rows:
        return
    print(title)
    for status, rel in rows:
        print(f"  {status:<32} {rel}")


def main(argv=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    default_fw = os.environ.get("GENEALOGY_FRAMEWORK") or here
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["init", "status", "sync", "diff"])
    ap.add_argument("path", nargs="?", help="init: the new project folder; diff: the file to compare")
    ap.add_argument("--framework", default=default_fw)
    ap.add_argument("--project", default=None)
    ap.add_argument("--force", action="append", default=[], metavar="PATH",
                    help="overwrite this file even if the project changed it ('all' for every file)")
    ap.add_argument("--prune", action="store_true", help="delete unchanged files the framework removed")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    fw = os.path.abspath(os.path.expanduser(a.framework))
    manifest = load_manifest(fw)
    if a.command == "init":
        if not a.path:
            ap.error("init needs the new project folder")
        project = os.path.abspath(os.path.expanduser(a.path))
        os.makedirs(project, exist_ok=True)
    else:
        project = os.path.abspath(os.path.expanduser(a.project or os.getcwd()))
    if os.path.realpath(project) == os.path.realpath(fw):
        sys.exit("framework_sync: the project folder is the framework itself; pass --project")
    lock = read_lock(project)

    if a.command == "diff":
        if not a.path:
            ap.error("diff needs a file path")
        mine = os.path.join(project, a.path)
        theirs = os.path.join(fw, a.path)
        def lines(p):
            if not os.path.exists(p):
                return []
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.readlines()
        sys.stdout.writelines(difflib.unified_diff(lines(theirs), lines(mine),
                                                   f"framework/{a.path}", f"project/{a.path}"))
        return 0

    rows = plan(fw, project, manifest, lock)
    if a.command == "status":
        report([r for r in rows if r[0] != "same"], f"{project} against {fw}:")
        counts = {}
        for s, _ in rows:
            counts[s] = counts.get(s, 0) + 1
        print("; ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "nothing to compare")
        return 0

    done, left = apply(fw, project, manifest, lock, rows, set(a.force), a.prune, a.dry_run)
    report(done, ("Would apply:" if a.dry_run else "Applied:"))
    report(left, "Left alone (see the docstring; use diff, --force PATH or --prune):")
    if not a.dry_run:
        write_lock(project, lock, fw, manifest)
    print(f"framework_sync: {len(done)} applied, {len(left)} left alone"
          + (" (dry run, nothing written)" if a.dry_run else f"; lock written to {LOCK}"))
    if a.command == "init" and not a.dry_run:
        print("Next: fill in PROJECT.md, project.json and rules-access.md, replace the placeholder in tree.ged, "
              "then run git init and git config core.hooksPath tools/hooks.")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
