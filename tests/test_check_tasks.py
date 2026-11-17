"""Tests for check_tasks.py."""
import os
import tempfile
import unittest

import support

GOOD = """Intro.

## Queue

| Task | Where to look | Suits | Status |
|---|---|---|---|
| Baptism of Anna (`@I5@`) | Archion | worker | in progress (Gemini, 15 Nov 2026) |
| Marriage of Karl (`@I6@`) | FamilySearch | worker | open |
| Burial of Anna (`@I5@`) | Archion | worker | done (abc123) |
"""


class CheckTasks(unittest.TestCase):
    def run_on(self, text, *args):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "tasks.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return support.run(tmp, "tools/check_tasks.py", p, "--today", "2026-11-17", *args)

    def test_clean_file_passes(self):
        r = self.run_on(GOOD)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("0 errors, 0 notes", r.stdout)

    def test_row_after_blank_line_is_an_error(self):
        r = self.run_on(GOOD + "\n| Stray row | x | y | open |\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no header above it", r.stdout)

    def test_wrong_cell_count_is_an_error(self):
        r = self.run_on(GOOD.replace("| Marriage of Karl (`@I6@`) | FamilySearch |", "| Marriage of Karl (`@I6@`) |"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("3 cells, but the table's header has 4", r.stdout)

    def test_stale_claim_is_a_note(self):
        r = self.run_on(GOOD, "--stale-days", "1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("claim 'in progress' last dated 15 Nov 2026, 2 days ago", r.stdout)
        self.assertIn("0 notes", self.run_on(GOOD).stdout)

    def test_person_in_two_open_rows_is_a_note(self):
        r = self.run_on(GOOD.replace("done (abc123)", "proposed"))
        self.assertIn("@I5@ is named in 2 open rows", r.stdout)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
