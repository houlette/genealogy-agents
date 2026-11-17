This file describes this project for every agent, and holds the owner's own rules and decisions. `AGENTS.md` (from the genealogy-agents framework) gives the shared rules; where this file differs from it, this file wins. Agents read it at the start of every session. Keep it current, and date each decision with the owner's name, for example "(Jane, 3 March 2027)", so agents can tell an owner's decision from an agent's habit.

## The owner and the root person

- Owner: TODO (name as agents should address you; also set `owner` in `project.json`).
- Root person: TODO (name), `@I1@` in `tree.ged` (set `root` in `project.json` if it is another ID).
- Contact: agents never contact anyone for the owner. Drafts go in `sources/` for the owner to send.

## The family lines

TODO: list the surnames and places of the main lines, for example "the Schmidt line of Musterdorf, Hesse, to Ohio about 1850". Agents use this to tell which records matter.

## The end product

TODO: what the owner wants in the end (a narrative family history, a website, a printed book, a lineage-society application) and anything about it agents should know now. Say whether agents may draft narrative in `biographies.md` yet.

## Models and accounts

TODO: which agents and models this project uses (for example "Claude Code as the main session, Haiku subagents for retrieval, Gemini by API as the second reader"), any subscription window with its end date, and anything they must never be used for. Agents follow `rules-routing.md` unless this section says otherwise. Accounts and spending are in `rules-access.md`.

## Owner's decisions and changes to the shared rules

TODO: add each standing decision here with its date, for example:

- (Jane, 3 March 2027) Standing approval to download every available image of an original record into `sources/` without asking.
- (Jane, 3 March 2027) Do not draft biographies until the Schmidt line is back to 1700.

## Notes for particular tools

Claude Code, Gemini and other tools read their own file (`CLAUDE.md`, `GEMINI.md`), which imports `AGENTS.md` and this file. Put tool-specific notes there.
