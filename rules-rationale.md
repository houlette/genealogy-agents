Why the rules in `AGENTS.md` exist. They were written over several weeks of running Claude, Gemini and GPT agents on one real family tree, in October and November 2026, and almost every rule answers a failure that happened. Nothing here changes a rule; if this file and `AGENTS.md` disagree, `AGENTS.md` wins. A project keeps its own incidents in a file of its own (for example `rules-history.md`).

## One generation at a time, and check before you build

An audit found that early sessions had entered whole generations on citations nobody had seen and that may not exist. The errors came from single sessions that added four or five generations from one narrative source, each generation resting on the one below. Hence one generation at a time, each link with its own record, and the rule that an agent opens the cited image itself before building on another agent's claim. Agents write fluent, confident prose, so a conclusion that is written down looks settled whether or not it is.

## Every fact carries its own citation

A provenance audit of the same tree found hundreds of dated facts with no citation, and hundreds more whose source was named but never saved, many with no record of where the date came from. It also found certificates cited only in a person-level note, and a compiled typescript cited for a birth but not for the death beside it. A citation that sits anywhere other than under the fact gets separated from it in later edits. The same audit found a cited image that was an unreadable thumbnail, which is why `tools/check_tree.py` lists small cited images.

## Evidence grades that match the citations

Agents graded facts `direct, original` when only an index had been seen, and wrote "verified" about secondary sources. The `Evidence:` levels, the rule that one note matches the citations under its own fact, the `EVIDENCE` checks in `check_tree.py` and the certainty words in `lint_prose.py` all exist to keep the grade honest. The baptism and burial rules exist because agents routinely copied a baptism date into the birth date and a burial date into the death date.

## Identity points

Most wrong-parent errors were identity errors: a record of a person with the right name in the right village, but the wrong person. Collecting identity points in a table, with conflicts listed beside agreements, makes the identity argument visible and lets later agents add to it.

## Keep weak links

Early on, links whose evidence turned out weak were removed, and the connection and the reasoning behind it were lost. Grading a weak link `hypothesis (link kept)` keeps the connection visible while marking its weakness, and a retrieval task records what would settle it.

## Failed retrievals are not results

Agents treated rate limits, sign-outs, timeouts and empty pages as "no record found", and closed lines of research that had never been searched. Recording each failure in `open-questions.md` keeps the line open until a retry succeeds.

## Archival extinction

Agents marked lines as having no surviving earlier records because a register series started at a certain year. A register's start date shows where one series begins, not that no other evidence survives (court, tax, notarial and manorial records often reach further back), so a mark needs exhaustive research and the owner's approval.

## Two readers from different vendors

In a handwriting test on the project's own records (November 2026), the models differed widely in how many key fields they read correctly, and every model was pulled toward a name it was told to expect. Reading blind first, and comparing two readers from different vendors, catches errors that one model or two models from one vendor make together. The test also found that blind-fields-first was the only prompting method that improved accuracy.

## Cheap workers, expensive reviewer

Measured over three weeks, most of the cost of the agents was re-reading long contexts, not writing output, and browser work (above all screenshots) was the largest consumer. So the main session plans and reviews, and short-lived workers with small contexts do the browsing.

## One agent per site

Batched searches on one subscription site brought rate limits, error pages and disconnects, and two agents on one site share one account's limits. The bridge enforces one agent per site, and extensions that bypass it are not used.

## Images checked before naming, crops checked after cropping

A mislabelled image was saved under the wrong person's name, and crops were saved that showed a blank margin or the wrong passengers. Opening the image before naming it, and quoting the entry's first words from the crop, catches both.

## A work queue that stays true

In a project run by several agents over weeks, the queue drifted: one person collected seven rows added on different days, results reached the research log but not the task row, claims by agents that had moved on stood for ten days, and rows appended at the end of the file or after a blank line fell out of their tables, so their status cells did not display. Agents then picked up work that was already done. Searching before adding, updating the row in the same commit as the log, letting claims lapse, and `tools/check_tasks.py` in `verify.py` and the commit hook address each of these.
