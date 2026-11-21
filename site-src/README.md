The family website is a private site for relatives, built from the project's tree, stories and saved record images, and served behind an email sign-in so that living relatives' details reach only close family. It has a home page, a page for each person (story first, then a one-sentence summary, the life events with plain-language evidence labels, records and research notes), a fan chart and a family chart, a map and timeline that follow each surname line across the generations with historical events beside it, story chapters that move along a map, a military service map, a records gallery with a zoomable viewer, a page of numbers about the family, a "How are we related?" view, and a form on every person page through which relatives send a memory or a photo to the owner. It was built for one family in November 2026 and reworked after a review for readers who are not researchers; this folder and `family-app/` are the general version.

There are three parts:

- `tools/build_site.py` reads the project and writes two git-ignored folders: `site/` (the static site every approved reader may load) and `private/` (read only by the sign-in app).
- `site-src/index.html`, `app.js` and `style.css` are the front end: plain JavaScript with no build step, with Leaflet for the maps loaded from a CDN.
- `family-app/` is the sign-in app (FastAPI and SQLite) and the deploy script; `family-app/DEPLOYING.md` describes how to run it on a server.

## Building it

```bash
pip install markdown Pillow
python3 tools/geocode_places.py        # once, and again after new places enter the tree
python3 tools/build_site.py --report   # writes site/ and private/
```

To look at the result before any server exists, run the sign-in app locally (`family-app/DEPLOYING.md`, "Testing locally"). Opening `site/index.html` from disk does not work, because the page loads its data with `fetch`.

## The inputs

| File | Who writes it | What it holds |
|---|---|---|
| `tree.ged` | the agents | people, families, events, citations and `Evidence:` notes |
| `biographies.md` | the agents | sections whose `###` headings name a person and their years become that person's story |
| `stories/*.md` | the agents | story chapters (below) |
| `sources/` | the agents | record images; a citation's images are shown if `site-src/rights.json` allows it |
| `site-src/site.json` | the project | the settings below |
| `site-src/places.json` | `tools/geocode_places.py` | map coordinates for each place, cached |
| `site-src/places-overrides.json` | the project | hand corrections to the coordinates, which always win |
| `site-src/rights.json` | the project | which citations' images the site may show |
| `site-src/history-events.json` | the project | historical events for the timeline and person pages |
| `site-src/service.json` | the project | military service routes |

The project files start as templates from the framework and then belong to the project; `tools/framework_sync.py` never overwrites them. Each one's `_note` says what its fields mean.

### Settings in site.json

- `name`: the site's name, in the page title and the sign-in emails.
- `owner_name`: who receives contributions and access requests ("Send to Alex"); by default the `owner` in `project.json`.
- `root_name`: what the pages call the root person when a reader is not linked to anyone in the tree ("Alex's direct line"); by default `owner_name`. The root person is usually living, so the public data does not hold their name, and this is the only place the pages get it from.
- `story_footer` and `history_footer`: a line under every story chapter and under the historical events, for a warning such as "a draft, not yet checked against sources"; empty by default.
- `origins_note`: a sentence added to the explanation of the "Where the family came from" chart.
- `request_example`: the example under "How are you related?" on the access request form.
- `root`: the person whose ancestors the site follows, as a tree ID (`"@I730@"`), when that is not the project's root. Set it to a child of the project's root to show both parents' sides as the family's lines; `root_name` then names that child. The research tools keep the project's root.
- `joined_trees`: other GEDCOM files to show on the site beside `tree.ged`, such as a spouse's own tree, without importing them into the research database. Each entry gives `file` (a path in the project, usually under `site-src/trees/`), `same_people` and `same_families` (each person or family the two files share, as `{"@ID in the joined file@": "@ID in tree.ged@"}`), and optionally `evidence` (an Evidence note added to every fact of the joined file that has none, such as `"lead only. From Laura's own tree; not checked by this project."`, so the pages hedge those facts) and `offset`. The joined file's other people get IDs of their own number plus 1,000,000 (2,000,000 for a second file), so their pages keep the same address when either file grows. Source records become free-text citations. To update, replace the file with a new export and run the build. See `tools/join_trees.py`.
- `regions`: pairs of a pattern and a code. A person's regions are the codes whose patterns match the places of their events, and a historical event is shown to people who share one of its region codes (or to everyone, for `world`).
- `countries`: up to six, each with a `label`, a `match` pattern and a palette `color` from 1 to 6 (`style.css`). A person's colour in the charts comes from the first country, in list order, whose pattern matches their birth or baptism place, so put a narrow pattern before a broad one that would also match it (a province before the state it belonged to, for example). The legend shows them in colour order.
- `geocode_rewrites`: pattern and replacement pairs that `tools/geocode_places.py` applies to a place name before looking it up, to turn old or local names into ones OpenStreetMap knows. A list here replaces the built-in one, so copy it from the template and add to it; keep its first rule, which drops a leading part that starts with a house number, so that no street address (a living relative's among them) is ever sent to the service.
- `deploy`: `ssh_host`, `remote_dir`, `url` and `env_file` for `family-app/deploy.sh`.

Patterns are regular expressions read by both Python and JavaScript, so keep to the syntax the two share (alternatives, character classes, anchors).

### Stories

A story chapter is a Markdown file in `stories/`, named with a number for its order (`01-across-the-ocean.md`). It starts with front matter and is divided into steps, each opened by a marker on a line of its own:

```markdown
---
title: Across the Ocean
subtitle: Johann Adler, from Musterdorf to Ohio, 1850 to 1880
line: Adler
people: I5 I9
---

{{step place="Musterdorf, Hessen, Germany" year=1850 person=I5 zoom=9}}

The text of this step, in Markdown.

<!-- About the sources: the register entry, sources/1850-...-baptism-Musterdorf-Johann-Adler.md. TODO: find the passenger list. -->

{{note}}

A step with no place on the map.
```

`place` must be a place that `site-src/places.json` puts on the map, written exactly as in `tree.ged`; the build reports a step whose place is missing. HTML comments become a collapsed "About the sources" note under the step, with their TODO sentences left out. The build stops if a step names a living person.

## Living relatives

The build follows the same rule as `tools/living.py`: anyone with no death record whose birth year, recorded or estimated from close relatives, is after the project's living cutoff counts as living.

- Living people appear in the public data only as an ID, a sex and family links, so the charts can show "Living relative". Their names, dates and events are in `private/private.json`.
- People who have died but have a living child or grandchild are "recent". Their names, dates, places and events are public, but their stories, research notes, citations and images are private, because those often name living relatives.
- Any image of a 1940 or 1950 census is private.
- The sign-in app gives each reader the living people of their own family under the grandparents rule: their own ancestors and descendants, the descendants of their parents and grandparents (siblings, aunts and uncles, first cousins and their children), and the spouses of anyone in those groups. A reader is linked to a person in the tree when the admin approves them; a reader linked to no one sees no living relatives, and the admin sees everyone.

`python3 tools/living.py` lists who the build treats as living; the owner should read that list before the site goes live, and record any deaths the family knows of in `tree.ged`.

## Image rights

Showing a record image to signed-in relatives is still publishing it on the web, and many archives and websites forbid that in their terms. `site-src/rights.json` decides, citation by citation, whether the images are shown; when they are not, the site shows the citation and the transcription, with the link to the archive's own viewer. Read each repository's terms before adding it to `show`, and record what you read and when. In November 2026 the original project found, for example, that NARA images are in the public domain, that Matricula allows non-commercial use with credit and a notice to the archive, that FamilySearch's custodian contracts usually forbid publishing images on the web, and that Ancestry's terms limit images to personal research; terms change, so check them again yourself. Photographs the family took and books published long enough ago to be out of copyright can be shown.

## Evidence labels

Each life event carries a label taken from its `Evidence:` note in `tree.ged`, in words a relative understands: "Seen in the original record", "Stated in a later record", "From an index", "From a published history", "Worked out from a record", "Probably right", "A lead, not yet checked", "Family memory" and "Records disagree". The one-sentence summary at the top of a person page adds "probably", "possibly" or "reportedly" where the evidence calls for it, so the site never sounds surer than the tree.
