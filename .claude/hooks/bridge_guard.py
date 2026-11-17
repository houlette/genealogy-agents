#!/usr/bin/env python3
"""PreToolUse hook (Bash): warn the main Claude session when it drives the
browser bridge itself under the agent name 'claude'. Workers use other names
(claude-sub-1, claude-haiku-fs8), so they are not warned. It never blocks; it adds a reminder to Claude's context."""
import json, re, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
tool_input = data.get("tool_input") if isinstance(data, dict) else None
cmd = tool_input.get("command") if isinstance(tool_input, dict) else ""
if not isinstance(cmd, str):
    cmd = ""
patterns = [
    r"BRIDGE_AGENT=['\"]?claude(?![\w-])",
    r"set_agent\(\s*['\"]claude['\"]\s*\)",
    r"python3?\s+\S*scratch/claude/",
]
if any(re.search(p, cmd) for p in patterns):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": (
            "Bridge guard: this command drives Chrome as the main session ('claude'). "
            "CLAUDE.md: delegate all browser work, including "
            "searches and single downloads, to the retriever subagent; the main session "
            "claims a site only to check one page a worker reported, or to repair the "
            "bridge. If this is neither, stop and write a brief instead."),
    }}))
sys.exit(0)
