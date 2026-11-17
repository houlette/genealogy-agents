The shared instructions for all agents in this repository are in `AGENTS.md` and `PROJECT.md`, imported below. Change rules there, not here; this file holds only notes that apply to Claude Code.

@AGENTS.md
@PROJECT.md

## Claude Code notes

- **Browser.** Do not use Claude in Chrome (the `mcp__claude-in-chrome__*` tools) in this project, even if they are loaded, because the bridge daemon cannot see or refuse them. Drive Chrome through the bridge in `tools/browser_bridge/` with `BRIDGE_AGENT=claude` (or a subagent's own name), from Python with `client.py` or with `curl`. For handwriting or a headstone, save a screenshot with `client.screenshot` and open the file with Read.
- **Retrieval subagents.** Delegate browser work, including searches and single downloads, to the `retriever` subagent (`.claude/agents/retriever.md`). Give it a self-contained prompt with the exact URL or search, the download procedure, the destination filename in `sources/`, the facts to report back, and its own `BRIDGE_AGENT` name (`claude-sub-1` and so on). If a worker fails, fix the brief and send it again (on Sonnet if Haiku failed) rather than taking over the browser. Browser subagents may run in parallel only on different sites. Keep analysis, `tree.ged` edits and narrative writing in the main session.
- **Readings.** The `reader` subagent (`.claude/agents/reader.md`) reads saved images blind and argues a link from a dossier; give it only the crop and page paths and neutral context, never another reader's text.
- **Memory.** Claude's private memory is not visible to the other agents. Anything another agent needs to know goes in `PROJECT.md`, `research-log.md` or `open-questions.md`, not only in memory.
