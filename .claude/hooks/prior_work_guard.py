#!/usr/bin/env python3
"""PreToolUse hook (Agent): refuse to launch a retrieval worker whose brief has
not been checked against the earlier work with tools/prior_work.py.

A launch counts as retrieval when the subagent type is 'retriever' or the
prompt sets BRIDGE_AGENT. The brief is the file the prompt names (a path
ending in .md that contains 'brief'), or the prompt itself when it names no
file. The rules are tools/prior_work.py's check_brief: the brief carries a
'Prior work (tools/prior_work.py, <date>): <query>' block from today or
yesterday, and the query covers every @I...@ the brief names.

Workers were sent to fetch records already saved at least eight times between
11 and 22 November 2026, each time because the check was done by hand with
the wrong search terms; see rules-rationale.md. If the hook itself fails (no
tools/prior_work.py, an unreadable brief), it lets the launch go and says so.
"""
import importlib.util
import json
import os
import re
import sys


def emit(decision, reason):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision == "deny":
        out["hookSpecificOutput"]["permissionDecision"] = "deny"
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    else:
        out["hookSpecificOutput"]["additionalContext"] = reason
    print(json.dumps(out))
    sys.exit(0)


try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(data, dict) or data.get("tool_name") not in ("Agent", "Task"):
    sys.exit(0)
ti = data.get("tool_input") or {}
prompt = ti.get("prompt") or ""
if not isinstance(prompt, str):
    sys.exit(0)
if ti.get("subagent_type") != "retriever" and not re.search(r"BRIDGE_AGENT=", prompt):
    sys.exit(0)

project = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
tool = os.path.join(project, "tools", "prior_work.py")
if not os.path.exists(tool):
    emit("allow", "Prior-work guard: tools/prior_work.py is missing, so this brief was not checked.")
try:
    spec = importlib.util.spec_from_file_location("prior_work", tool)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(tool))
    spec.loader.exec_module(mod)
except Exception as e:  # a broken tool should not stop all work
    emit("allow", f"Prior-work guard: could not load tools/prior_work.py ({e}); the brief was not checked.")

text, where = prompt, "the prompt"
m = re.search(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]*brief[\w.-]*\.md)", prompt)
if m:
    path = m.group(1) if os.path.isabs(m.group(1)) else os.path.join(project, m.group(1))
    try:
        with open(path, encoding="utf-8") as f:
            text, where = f.read(), m.group(1)
    except OSError:
        emit("deny", f"Prior-work guard: the prompt names the brief {m.group(1)}, but it cannot be read. "
                     "Write and commit the brief before launching the worker.")

problems = mod.check_brief(text)
if problems:
    emit("deny", f"Prior-work guard ({where}): " + " ".join(problems))
sys.exit(0)
