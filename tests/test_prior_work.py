"""Tests for tools/prior_work.py and the hook .claude/hooks/prior_work_guard.py.

Each test builds a small project with the kinds of earlier work that were
missed in practice: a find logged under another spelling, a register logged
by its viewer number, an image saved under a variant spelling, and an open
list naming an image already saved.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta

import support

TREE = """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Georg /Seyler/
1 SEX M
1 DEAT
2 DATE 1820
2 SOUR Deed, with image (sources/1825-deed-Musterkreis-heirs-of-Georg-Seyler-DGS001112223-img028.jpg)
0 @I2@ INDI
1 NAME Kaspar /Weidel/
1 SEX M
0 @I3@ INDI
1 NAME Anna /Fahl/
1 SEX F
0 TRLR
"""

LOG = """# Research log

- 2026-11-18 (Claude) Archion worker claude-sub-298: Musterdorf baptisms 1748-1756 (123456 Bild 159-183) read page by page; no Kaspar Weidel.
- 2026-11-19 (Claude) Pfahl family of Musterkreis: the 1792 bond names Anna Pfahl.
- 2026-11-20 (Claude) Files: the file was saved; file names checked; File line notes.
- 2026-11-20 (Claude) Anna Fahl's 1795 bond is in the file of bonds.
"""

DOSSIER = """Compiled by: claude-sub-49, 19 Nov 2026

Index rows read: "Seiler Johann p. 129" on the L heading.
Coverage: DGS007654321 img 671-683 | Musterkreis court minutes Nov 1794-May 1795 | Seiler will probate | none found | claude-sub-58, 2026-11-22
"""

OPEN_Q = """# Open questions

## Failed retrievals to retry

- 2026-11-20: DGS 001234567 img 240 not opened (timeout).

## Other

- DGS 001234567 img 240 is mentioned here too, outside the retry list.
"""

IMAGES = (
    "sources/1825-deed-Musterkreis-heirs-of-Georg-Seyler-DGS001112223-img028.jpg",
    "sources/1830-10-28-deed-Musterkreis-Brandt-heirs-DGS1234567-img240.jpg",
    "sources/1830-10-28-deed-Musterkreis-Brandt-heirs-DGS1234567-img241.jpg",
    "sources/1753-baptisms-Musterdorf-Archion-123456-Bild175.jpg",
    "sources/1696-12-baptism-Beispielheim-234567-Bild139-twins-Kornweiser.jpg",
)

CONFIG = {"root": "@I1@", "surname_variants": [["Fahl", "Pfahl", "Vahl"]]}


def make(tmp):
    support.make_project(tmp, tree=TREE, config=CONFIG, images=IMAGES, image_size=(20, 20))
    files = {
        "research-log.md": LOG,
        "sources/2026-11-19-Ancestry-tree-source-links-2.md": DOSSIER,
        "open-questions.md": OPEN_Q,
        "tasks.md": "# Tasks\n",
        "sources-index.md": "| Place | Records | Identifiers |\n|---|---|---|\n| Musterdorf | baptisms | Archion 123456 |\n",
    }
    for rel, text in files.items():
        with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
            f.write(text)
    return tmp


def run(tmp, *args):
    return support.run(tmp, "tools/prior_work.py", *args)


class PhoneticTest(unittest.TestCase):
    def test_koelner_codes(self):
        import prior_work
        self.assertEqual(prior_work.koelner("Seyler"), prior_work.koelner("Seiler"))
        self.assertEqual(prior_work.koelner("Kornwieser"), prior_work.koelner("Kornweiser"))
        self.assertNotEqual(prior_work.koelner("Seyler"), prior_work.koelner("Miller"))

    def test_fold_writes_umlauts_out(self):
        import prior_work
        self.assertEqual(prior_work.fold("Mühlhördt"), "muehlhoerdt")


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        make(self.tmp)

    def test_sound_alike_finds_other_spelling(self):
        r = run(self.tmp, "--person", "@I1@")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Seiler", r.stdout)
        self.assertIn("2026-11-19-Ancestry-tree-source-links-2.md", r.stdout)
        self.assertIn('"Seiler Johann p. 129"', r.stdout)

    def test_variant_group_from_project_json(self):
        r = run(self.tmp, "--person", "@I3@")
        self.assertIn("names searched: Fahl, Pfahl, Vahl", r.stdout)
        self.assertIn("the 1792 bond names Anna Pfahl", r.stdout)

    def test_surname_that_is_a_word_needs_a_given_name(self):
        with open(os.path.join(self.tmp, "research-log.md"), "a", encoding="utf-8") as f:
            f.write("- Fahl notes; fahl fahl fahl fahl fahl fahl.\n")
        r = run(self.tmp, "--surname", "Fahl", "--no-sound")
        self.assertIn("also ordinary words", r.stdout)
        self.assertIn("Anna Fahl's 1795 bond", r.stdout)
        self.assertNotIn("Fahl notes", r.stdout)

    def test_place_widens_to_register_and_range(self):
        r = run(self.tmp, "--person", "@I2@", "--place", "Musterdorf")
        self.assertIn("identifiers tied to Musterdorf: Archion 123456", r.stdout)
        self.assertIn("Archion 123456", r.stdout.split("Registers")[1])
        self.assertIn("range 159-183 (research-log.md:3", r.stdout)

    def test_saved_images_by_key_and_image(self):
        r = run(self.tmp, "--key", "DGS001234567:240")
        saved = r.stdout.split("Registers")[0]
        self.assertIn("DGS 001234567: images 240", saved)
        self.assertNotIn("img241", saved)

    def test_saved_image_under_sound_alike(self):
        r = run(self.tmp, "--surname", "Kornwieser")
        self.assertIn("Saved images under a sound-alike spelling", r.stdout)
        self.assertIn("Bild139-twins-Kornweiser.jpg", r.stdout)

    def test_coverage_lines_reported(self):
        r = run(self.tmp, "--key", "DGS007654321")
        self.assertIn("Coverage: DGS007654321 img 671-683", r.stdout)

    def test_tree_citations_listed(self):
        r = run(self.tmp, "--person", "@I1@")
        tree = r.stdout.split("Tree: files already cited")[1]
        self.assertIn("DGS001112223-img028.jpg", tree)

    def test_unknown_person_is_an_error(self):
        r = run(self.tmp, "--person", "@I99@")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("@I99@ is not in", r.stdout + r.stderr)

    def test_stale_open_item(self):
        r = run(self.tmp, "--stale")
        self.assertIn("open-questions.md:5 lists DGS 001234567 image 240 as open, but 1 of 1 are saved", r.stdout)
        self.assertNotIn("open-questions.md:9", r.stdout)

    def test_brief_block_header(self):
        r = run(self.tmp, "--person", "@I1@", "--brief-block")
        self.assertTrue(r.stdout.startswith(f"Prior work (tools/prior_work.py, {date.today().isoformat()}"), r.stdout[:200])
        self.assertIn("--person @I1@", r.stdout.splitlines()[0])
        self.assertTrue(r.stdout.rstrip().endswith("End of prior work."))


class CheckBriefTest(unittest.TestCase):
    def check(self, text, today=date(2026, 11, 22)):
        import prior_work
        return prior_work.check_brief(text, today)

    def test_no_block(self):
        p = self.check("Brief for worker 1. Georg Seyler (@I1@).")
        self.assertTrue(p and "no 'Prior work" in p[0])

    def test_old_block(self):
        p = self.check("Brief, @I1@.\nPrior work (tools/prior_work.py, 2026-11-19, 4 hits): --person @I1@\nEnd of prior work.")
        self.assertTrue(any("dated 2026-11-19" in x for x in p), p)

    def test_id_missing_from_query(self):
        p = self.check("Brief, @I1@ and @I2@.\nPrior work (tools/prior_work.py, 2026-11-22, 4 hits): --person @I1@\nEnd of prior work.")
        self.assertTrue(any("@I2@" in x for x in p), p)

    def test_no_person_and_no_key(self):
        p = self.check("Brief.\nPrior work (tools/prior_work.py, 2026-11-22, 4 hits): --surname Seyler\nEnd of prior work.")
        self.assertTrue(any("names no person" in x for x in p), p)

    def test_ids_inside_block_do_not_count(self):
        text = ("Brief, @I1@.\nPrior work (tools/prior_work.py, 2026-11-22, 4 hits): --person @I1@\n"
                "  @I7@ Someone: 2 files\nEnd of prior work.\n")
        self.assertEqual(self.check(text), [])

    def test_good_brief(self):
        self.assertEqual(self.check("Brief, @I1@.\nPrior work (tools/prior_work.py, 2026-11-21, 4 hits): --person @I1@"), [])

    def test_key_only_brief(self):
        self.assertEqual(self.check("Calibrate the film.\nPrior work (tools/prior_work.py, 2026-11-22, 0 hits): --key DGS007654321"), [])


HOOK = os.path.join(support.FRAMEWORK, ".claude", "hooks", "prior_work_guard.py")


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        make(self.tmp)
        support.install_framework(self.tmp)
        os.makedirs(os.path.join(self.tmp, "scratch", "main"))

    def hook(self, prompt, subagent="retriever"):
        data = {"tool_name": "Agent", "tool_input": {"subagent_type": subagent, "prompt": prompt, "description": "x"},
                "cwd": self.tmp}
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(data), capture_output=True, text=True,
                           env=dict(os.environ, CLAUDE_PROJECT_DIR=self.tmp), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def brief(self, text):
        with open(os.path.join(self.tmp, "scratch", "main", "2026-11-22-brief-1-x.md"), "w", encoding="utf-8") as f:
            f.write(text)
        return "Your brief is the file scratch/main/2026-11-22-brief-1-x.md. Read it in full."

    def test_refuses_brief_without_block(self):
        out = self.hook(self.brief("Brief for worker 1. Georg Seyler (@I1@)."))
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("no 'Prior work", out["permissionDecisionReason"])

    def test_allows_brief_with_block(self):
        today = date.today().isoformat()
        out = self.hook(self.brief(f"Brief, @I1@.\nPrior work (tools/prior_work.py, {today}, 3 hits): --person @I1@\nEnd of prior work.\n"))
        self.assertIsNone(out)

    def test_refuses_unreadable_brief(self):
        out = self.hook("Your brief is the file scratch/main/2026-11-22-brief-9-missing.md.")
        self.assertEqual(out["permissionDecision"], "deny")

    def test_checks_inline_prompt(self):
        out = self.hook("BRIDGE_AGENT=claude-sub-1. Fetch the deed of @I1@.", subagent="general-purpose")
        self.assertEqual(out["permissionDecision"], "deny")

    def test_ignores_other_agents(self):
        self.assertIsNone(self.hook("Read the crop and argue the link.", subagent="reader"))

    def test_old_block_refused(self):
        old = (date.today() - timedelta(days=3)).isoformat()
        out = self.hook(self.brief(f"Brief, @I1@.\nPrior work (tools/prior_work.py, {old}, 3 hits): --person @I1@\n"))
        self.assertEqual(out["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
