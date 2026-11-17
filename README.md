genealogy-agents is a framework for researching a family tree with several AI coding agents at once (Claude Code, Gemini CLI or Antigravity, and cheaper models called by API), under rules strict enough that the tree stays trustworthy. It grew out of six weeks of running agents on one real family's tree in October and November 2026, and almost every rule in it answers a mistake the agents made there: generations added from citations nobody had seen, baptism dates copied into birth dates, a rate-limited search recorded as "no record found", a confident reading of a name the agent had been told to expect. It holds no family data; you start your own project from its templates.

The agents work in a git repository that holds the tree as a GEDCOM file, the saved record images and transcriptions, and a set of Markdown files (a research log, a task queue, open questions) through which agents that do not remember each other's sessions hand work on. A main session plans, reviews and edits the tree; workers retrieve documents through your own signed-in Chrome and read old handwriting; and every reading, new link and negative result is checked by a model from a different vendor before it counts.

## What is in it

- `AGENTS.md`: the rules every agent follows. The core is the evidence rules: cite only what you have seen, label every citation with the kind of source it is, give every fact an `Evidence:` grade that matches its citations, grade every parent-child link (`established`, `probable`, `hypothesis`) by its identity points, never treat a failed retrieval as a result, and never erase a corrected claim. `rules-rationale.md` gives the reasons behind each rule.
- `rules-routing.md`: which kind of model does which work, the two-reader rule for handwriting, the task queue, the review flag and the weekly review.
- `rules-browser.md` and `tools/browser_bridge/`: a small HTTP daemon in front of [chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) that lets several agents drive one signed-in Chrome at the same time, one agent per site, each in its own tab, in a Chrome profile kept for the agents. `site-notes.md` holds what the agents learned about FamilySearch, Ancestry, Archion, Matricula, Geneanet and other sites.
- Tree tools: `tools/check_tree.py` (broken or one-sided links, people not connected to the root person, impossible ages, cited files that do not exist, and evidence grades that do not match their citations), `tools/person.py` (one person and their family, with line numbers, without reading the whole file), `tools/frontier.py` (direct ancestors whose parents are not yet found, with warning flags), `tools/living.py` (who may be living), `make-tree-view.py` (a self-contained interactive pedigree viewer), and `tools/verify.py` (all checks in one step).
- Prose and commit checks: `tools/lint_prose.py` flags certainty words ("proves", "verified", "exhaustive") that the evidence rules reserve for original records, and em dashes; `tools/hooks/` runs the checks before each commit and blocks only on structural errors.
- Model tools: `tools/llm.py` (one-shot calls to Anthropic, Gemini and OpenRouter models, with a usage ledger and costs from `tools/models.json`), `tools/transcribe.py` (blind-then-informed readings of a record crop by two or more models, with the differences shown), `tools/dispatch.py` (sends a task brief to a worker agent: Claude Code, Antigravity, OpenCode or a small built-in agent loop, with spending caps), `tools/usage.py`, and `tools/bakeoff.py` (scores models on reading your own records before you decide which to trust).
- `.claude/agents/`: two Claude Code subagents, `retriever` (fetches one document through the bridge, on a cheap model) and `reader` (reads saved images and argues a link, with no browser).
- `templates/`: the project files a new project starts with.

## Starting a project

You need Python 3.10 or later, git, and Pillow (`pip install Pillow`) for image checks and crops. The browser bridge also needs Node.js and a recent Chrome; the model tools need API keys only if you use them.

```bash
git clone https://github.com/houlette/genealogy-agents.git
python3 genealogy-agents/tools/framework_sync.py init ~/my-family-tree
cd ~/my-family-tree
git init && git config core.hooksPath tools/hooks
```

Then:

1. Fill in `PROJECT.md` (who you are, the root person, the family lines, what you want in the end, which agents and accounts you have) and `project.json` (the same settings for the tools; `python3 tools/project_config.py` prints what is in force).
2. Replace the placeholder person in `tree.ged` with yourself or whoever the tree is built around, or import an existing GEDCOM file (keep the root person's ID in `project.json`). Run `python3 tools/verify.py`.
3. Fill in `rules-access.md` with your accounts and spending limits.
4. If agents will use the browser, follow "Setting up" in `tools/browser_bridge/README.md`: a separate Chrome profile for the agents, its name in `project.json`, and a one-time marker.
5. Start your agent in the project folder. Claude Code reads `CLAUDE.md` and Gemini reads `GEMINI.md`; both import `AGENTS.md` and `PROJECT.md`. A good first task is the one already in `tasks.md`: enter what the family knows, then confirm it from records, one generation at a time.

## Keeping a project up to date

Files the framework owns (`AGENTS.md`, the rule files, `tools/`, `tests/`, the subagents) can be updated from a newer clone:

```bash
python3 tools/framework_sync.py status --framework ../genealogy-agents
python3 tools/framework_sync.py sync --framework ../genealogy-agents
```

Sync replaces a file only if your project has not changed it since the last sync, and records what it installed in `.framework-lock.json`. A file you have changed is reported and left alone; `diff PATH` shows the difference, so you can send the improvement back to the framework or overwrite your copy with `--force PATH`. Your own rules and decisions belong in `PROJECT.md`, which overrides `AGENTS.md` and is never touched by sync.

## Tests

```bash
python3 tools/run_tests.py
```

runs about 640 unit tests of every tool, the hooks, the sync and the bridge. They use a small fictional tree (`tests/fixtures/sample-tree.ged`), throwaway git repositories and mocked HTTP, so they need no network, browser, API key or money. `tools/browser_bridge/live_two_agents.py` is a separate live test of the bridge against a real Chrome.

## Things to know before you rely on it

- It was built and tested on one project, on macOS, with Claude Code, Antigravity and API workers. Linux should work for everything except the bridge's keep-awake feature; Windows is untested.
- The model names and prices in `tools/models.json` were current in November 2026. Check them, and run `python3 tools/llm.py --check` once your keys are set.
- The bridge drives your real, signed-in Chrome. Keep the agents in their own Chrome profile, read `tools/browser_bridge/README.md` first, and respect each site's terms of use and rate limits; the rules tell agents to stop on sign-outs, rate limits and repeated failures, but you are responsible for what runs under your accounts.
- `tree-view.html` and the tree itself hold living relatives' details. Keep the project repository private, and treat anything you publish separately.
- The rules ask agents to write in plain language with marked certainty. They make agents slower and more cautious than they would otherwise be, which is the point.

## License

MIT; see `LICENSE`.
