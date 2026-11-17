The shared work queue. Pick the highest open task suited to you, mark it in progress with your name and the date, and when you finish, set it to "for review" or "done" with the commit hash. The routing rules are in `rules-routing.md`. Each task names its section in `open-questions.md`, where the background and evidence so far are kept, so read that first. The owner or the main session adds new tasks; any agent may add a task it discovers, marked "proposed". When the queue runs low, move a few rows from `python3 tools/frontier.py` into it.

Status values: open, proposed, in progress (agent, date), for review (agent, date, commit), done (commit), blocked (reason).

## Queue

| Task | Where to look | Suits | Status |
|---|---|---|---|
| Enter the root person, their parents and grandparents from family knowledge, each fact with "family knowledge" and the owner's name and date | Ask the owner | main session | open |
