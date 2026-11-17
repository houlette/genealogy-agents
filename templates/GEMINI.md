The shared instructions for all agents in this repository are in `AGENTS.md` and `PROJECT.md`, imported below. Change rules there, not here; this file holds only notes that apply to Gemini.

@./AGENTS.md
@./PROJECT.md

## Gemini notes

- If the imports above did not load, read `AGENTS.md` and `PROJECT.md` in full before doing anything else. Their evidence rules and their rules for working alongside the other agents apply to every session.
- Start the browser bridge daemon only if `PROJECT.md` says agents may, with `python3 tools/browser_bridge/daemon.py` as a background process (in Antigravity, `run_command` with `IsDaemon: true`), then check it with `curl -s http://127.0.0.1:9333/ping`.
