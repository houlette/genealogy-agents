Which model does which work. The principle is to keep the expensive main session for planning, review and structural edits, and to send token-heavy work (browser retrieval, screening, first readings) to cheaper workers. The models a project actually has, and any temporary arrangement such as a subscription month, are in `PROJECT.md`; where it names a model for a role, it overrides the defaults below. The roster with prices is `tools/models.json`, read by `tools/llm.py`.

## Roles

- **Main session: planning, review, structure and narrative.** It writes the briefs, checks what comes back, does the weekly review (below), makes the structural edits to `tree.ged`, and writes in `biographies.md`. It avoids browser work and open-ended searching unless the owner asks, and claims a site only to check one page a worker reported, or to repair the bridge.
- **Plain downloads: the cheapest capable model.** A document at a known URL (an `ark:`, a record number, a permanent link) goes to a retrieval worker: a `retriever` subagent (`.claude/agents/retriever.md`), or `python3 tools/dispatch.py <brief> --role retrieve` with a backend such as `claude-api:haiku`. The worker saves and names the file and checks that the target is on it. It does not read handwriting, and nothing it reads from an image goes into `tree.ged` or a dossier as a reading.
- **Retrieval that needs searching or reading:** a mid-sized model. Finding a page in a register, browsing a newspaper issue, or checking that a citation points at the right image. Retrieval workers save and describe records but do not add parent-child links to `tree.ged`.
- **Index screening, census and directory lookups, first drafts of printed or typed text:** a fast, cheap model.
- **Old handwriting: two blind readers from different vendors.** Each entry is read by two strong models from different vendors with `python3 tools/transcribe.py <crop> --models <a>,<b>`, blind first, and by a third reader only where the two disagree. A field the readers still disagree on is recorded as unsettled, with each reading, and not chosen silently. A `reader` subagent (`.claude/agents/reader.md`) given only the crop, the page and neutral context, never another reader's text, may take the place of one API reading; record which route each reading took.
- **Link arguments and dossiers:** a strong model in a fresh context (the `reader` subagent) that gets only the dossier and the crops.
- **Check across agents.** A negative result or new link counts as settled only after a model from a different vendor confirms it: the second reader checks the first reader's transcription against the image, a different model re-runs a search before its negative result stands, and the main session decides disputes and any claim that adds a generation.

## Rules for workers

- Workers follow every rule in `AGENTS.md`. A dispatched worker signs its report with its backend and agent name, claims its sites through the bridge under that name, and reports a failed retrieval as a failed retrieval.
- Give each worker its own folder for scripts and temporary files (`scratch/<agent name>/`), because workers that share a scratch folder can run each other's scripts under the wrong bridge name.
- Each dispatched run is recorded in `usage-ledger.jsonl` (`python3 tools/usage.py` summarizes it). API keys stay in the keyfile named in `project.json`, outside the repository, and a brief sent to an outside model must not contain the owner's email address or account details.
- A brief is self-contained: the exact URL or search, the download procedure, the destination filenames in `sources/`, the facts to report, and the bridge agent name. If a worker fails, fix the brief and send it again (on a stronger model if a cheap one failed) rather than taking over the browser.
- Before writing a brief, `grep -n` every research log for the surname and place, so the worker does not repeat a search already done.

## Testing the routing

`tools/bakeoff.py` scores models on reading this project's own records (crops in `tools/bakeoff/entries.json`, with reference readings and decoys), and `tools/bakeoff_sheet.py` makes a sheet for checking the crops by eye. Run it before fixing the routing for a project, and again when new models appear; record the results and the decision in `PROJECT.md`.

## The task queue, the review flag and the weekly review

- At the start of a session, take the highest open task in `tasks.md` that suits you and mark it "in progress" with your name and the date. When you finish, set it to "for review" or "done" with the commit hash. Add tasks you discover as "proposed".
- Anything that adds a generation, changes a parent-child link, or claims a negative result across a whole record set is "for review" until a different agent has checked it: normally the main session for new generations, and never the agent that ran the search, so do not mark your own negative result "done". Enter such a link in `tree.ged` with a `NOTE` beginning `For review:` that names the record it rests on. The reviewer removes the flag or downgrades the claim.
- The weekly review reads only what changed since the last one. Each review ends by tagging the reviewed commit (`git tag review-YYYY-MM-DD`). The next review starts from `git log --oneline $(git describe --tags --abbrev=0 --match 'review-*')..HEAD` and `git diff <that tag>..HEAD -- tree.ged`, plus the `--sources` lists of `tools/check_tree.py`, and opens dossiers and images only where the diff points to them.
