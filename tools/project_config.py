#!/usr/bin/env python3
"""Project settings, read from project.json at the project root.

    python3 tools/project_config.py        # print the settings in force

Every tool imports this module rather than hard-coding the root person, the
owner's name or the browser profile, so one copy of the tools serves any
project. Keys missing from project.json take the defaults below, and a
missing project.json means every default applies.

    owner            the person who runs the project; used in messages such
                     as "ask <owner> to click Allow"
    root             GEDCOM ID of the person the tree is built around
    tree             the GEDCOM file, relative to the project root
    living_cutoff    a person with no death record born after this year is
                     treated as possibly living; null means 100 years ago
    keyfile          file of API keys (KEY=value lines), kept outside the
                     repository
    bridge.port      port of the browser bridge daemon
    bridge.profile   Chrome profile the agents must work in; null allows any
    bridge.downloads folder Chrome saves downloads to
    surname_variants lists of spellings of one surname, searched together by
                     tools/prior_work.py, e.g. [["Seyler", "Seiler", "Sailer"]]
    briefs_glob      where the earlier briefs are, for tools/prior_work.py
    gemini_route     "plan" sends Gemini calls in tools/llm.py through the
                     owner's Google AI plan (the Antigravity CLI, agy) first
                     and falls back to the API key only when the plan's quota
                     is used up; "api" (the default) uses the API key only
"""
import copy
import json
import os
import sys
from datetime import date

PROJECT_DIR = os.environ.get(
    "GENEALOGY_PROJECT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULTS = {
    "owner": "the project owner",
    "root": "@I1@",
    "tree": "tree.ged",
    "living_cutoff": None,
    "keyfile": "~/.config/genealogy-agents.env",
    "bridge": {
        "port": 9333,
        "profile": None,
        "downloads": "~/Downloads",
    },
    "surname_variants": [],
    "briefs_glob": ["scratch/*/*brief*.md"],
    "gemini_route": "api",
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif not k.startswith("_"):
            out[k] = v
    return out


def load():
    path = os.path.join(PROJECT_DIR, "project.json")
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULTS)
    with open(path, encoding="utf-8") as f:
        return _merge(DEFAULTS, json.load(f))


CONFIG = load()
OWNER = CONFIG["owner"]
ROOT = CONFIG["root"]
TREE = os.path.join(PROJECT_DIR, CONFIG["tree"])
LIVING_CUTOFF = CONFIG["living_cutoff"] or date.today().year - 100
KEYFILE = os.path.expanduser(CONFIG["keyfile"])
BRIDGE = CONFIG["bridge"]
GEMINI_ROUTE = CONFIG["gemini_route"]

if __name__ == "__main__":
    json.dump({**CONFIG, "_project_dir": PROJECT_DIR, "_living_cutoff_in_force": LIVING_CUTOFF},
              sys.stdout, indent=2)
    print()
