#!/usr/bin/env python3
"""Unified verification tool: check_tree, make-tree-view, lint_prose and check_tasks in one call.

Usage:
    python3 tools/verify.py            # runs all 4 checks with standard options
    python3 tools/verify.py --sources  # forwards --sources to check_tree.py
    python3 tools/verify.py --rev A..B # forwards --rev to lint_prose.py
"""
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def run_step(name, cmd):
    res = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    out = res.stdout.strip()
    err = res.stderr.strip()
    combined = "\n".join(filter(None, [out, err]))
    return res.returncode, combined

def main():
    args = sys.argv[1:]
    check_tree_args = ["python3", "tools/check_tree.py"]
    lint_args = ["python3", "tools/lint_prose.py"]

    # Forward specific arguments
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--sources":
            check_tree_args.append(a)
        elif a in ("--rev", "--message", "--only-message") and i + 1 < len(args):
            lint_args += [a, args[i + 1]]
            i += 1
        i += 1

    failed = False

    # Step 1: check_tree.py
    code, out = run_step("check_tree", check_tree_args)
    if out:
        print(out)
    if code != 0:
        print("[FAIL] check_tree.py returned non-zero exit status.")
        failed = True

    # Step 2: make-tree-view.py (rebuild viewer)
    code_view, out_view = run_step("make-tree-view", ["python3", "make-tree-view.py"])
    if out_view:
        print(out_view)
    if code_view != 0:
        print("[FAIL] make-tree-view.py returned non-zero exit status.")
        failed = True

    # Step 3: lint_prose.py
    code_lint, out_lint = run_step("lint_prose", lint_args)
    if out_lint:
        print(out_lint)
    if code_lint != 0:
        print("[FAIL] lint_prose.py flagged prohibited certainty words or formatting.")
        failed = True

    # Step 4: check_tasks.py (the work queue's tables, stale claims, duplicate rows)
    code_tasks, out_tasks = run_step("check_tasks", ["python3", "tools/check_tasks.py"])
    if out_tasks:
        print(out_tasks)
    if code_tasks != 0:
        print("[FAIL] check_tasks.py found broken tables in tasks.md.")
        failed = True

    if failed:
        sys.exit(1)
    else:
        print("verify: all checks passed cleanly.")

if __name__ == "__main__":
    main()
