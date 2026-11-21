"""Tests for tools/join_trees.py, which adds another GEDCOM file to the tree for the website."""
import re
import unittest

import support  # noqa: F401  (puts tools/ on the path)
import join_trees as jt

MAIN = """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Alex /Doe/
1 FAMS @F1@
0 @I2@ INDI
1 NAME Sam /Roe/
1 FAMS @F1@
0 @I3@ INDI
1 NAME Kit /Doe/
1 FAMC @F1@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 TRLR
"""

EXTRA = """0 HEAD
1 SOUR Ancestry
0 @I1@ INDI
1 NAME Sam /Roe/
1 BIRT
2 DATE 1975
2 SOUR @S1@
1 FAMC @F2@
1 FAMS @F9@
0 @I5@ INDI
1 NAME Pat /Roe/
1 BIRT
2 DATE 1940
2 SOUR @S1@
3 PAGE Census 1950,
4 CONC  sheet 4
3 NOTE record 12
1 FAMS @F2@
0 @I8@ INDI
1 NAME Alex /Doe/
1 FAMS @F9@
0 @F2@ FAM
1 HUSB @I5@
1 CHIL @I1@
0 @F9@ FAM
1 HUSB @I8@
1 WIFE @I1@
0 @S1@ SOUR
1 TITL 1950 census
0 TRLR
"""


def joined(**kw):
    return jt.join(MAIN, EXTRA, {"@I1@": "@I2@", "@I8@": "@I1@"}, {"@F9@": "@F1@"}, **kw)


def record(text, rid):
    m = re.search(rf"(?ms)^0 @{rid}@ .*?(?=^0 )", text)
    return m.group(0) if m else None


class JoinTrees(unittest.TestCase):
    def test_new_people_get_offset_ids_and_links_stay_whole(self):
        out = joined()
        self.assertIn("0 @I1000005@ INDI", out)
        self.assertIn("1 HUSB @I1000005@", record(out, "F1000002"))
        self.assertIn("1 CHIL @I2@", record(out, "F1000002"))
        refs = set(re.findall(r"@([IF]\d+)@", out))
        defined = set(re.findall(r"(?m)^0 @([IF]\d+)@", out))
        self.assertEqual(refs - defined, set())

    def test_same_people_keep_the_main_record_and_gain_parents(self):
        sam = record(joined(), "I2")
        self.assertIn("1 FAMC @F1000002@", sam)
        self.assertNotIn("BIRT", sam)
        self.assertEqual(sam.count("1 FAMS @F1@"), 1)
        self.assertIsNone(record(joined(), "I1000001"))
        self.assertIsNone(record(joined(), "F1000009"))

    def test_citations_become_one_free_text_line(self):
        pat = record(joined(), "I1000005")
        self.assertIn("2 SOUR 1950 census; Census 1950, sheet 4", pat)
        self.assertNotIn("@S1@", pat)
        self.assertNotIn("SOUR @S", joined())

    def test_evidence_note_added_only_when_asked(self):
        self.assertNotIn("Evidence:", joined())
        pat = record(joined(evidence="lead only. Their tree."), "I1000005")
        self.assertIn("2 NOTE Evidence: lead only. Their tree.", pat)

    def test_main_tree_ids_past_the_offset_are_refused(self):
        with self.assertRaises(ValueError):
            jt.join(MAIN, EXTRA, {}, {}, offset=2)

    def test_unknown_same_person_is_refused(self):
        with self.assertRaises(ValueError):
            jt.join(MAIN, EXTRA, {"@I1@": "@I99@"})


if __name__ == "__main__":
    unittest.main()
