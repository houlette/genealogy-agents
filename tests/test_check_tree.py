"""Tests for tools/check_tree.py: links, connectivity, ages, sources and evidence checks."""
import os
import re
import tempfile
import unittest

import support

import check_tree  # noqa: E402  (support puts tools/ on sys.path)

HAS_PIL = True
try:
    import PIL  # noqa: F401
except ImportError:
    HAS_PIL = False


def summary(out):
    return next(l for l in out.splitlines() if l.startswith("sources:"))


@unittest.skipUnless(HAS_PIL, "Pillow is needed to make the fixture image")
class FixtureRun(unittest.TestCase):
    """The fixture is built to give 0 errors and a known set of warnings."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        support.make_project(cls.tmp.name)
        cls.r = support.run(cls.tmp.name, "tools/check_tree.py", "--sources")
        cls.out = cls.r.stdout

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_errors_exit_zero(self):
        self.assertEqual(self.r.returncode, 0, self.out + self.r.stderr)
        self.assertIn("0 errors", self.out)
        self.assertNotIn("ERROR", self.out)

    def test_counts_and_next_ids(self):
        self.assertIn("11 people, 5 families; 11/11 people reachable from @I1@", self.out)
        self.assertIn("next free IDs @I12@ @F6@", self.out)

    def test_lifespan_warning(self):
        self.assertIn("WARNING @I11@ Ludwig /Brandt/: born 1880, died 1990", self.out)
        self.assertIn("1 warnings", self.out)

    def test_evidence_warnings(self):
        ev = [l for l in self.out.splitlines() if l.startswith("EVIDENCE")]
        self.assertEqual(len(ev), 5, ev)
        joined = "\n".join(ev)
        self.assertRegex(joined, r"@I6@ DEAT: 'direct, original' but no saved image")
        self.assertRegex(joined, r"@I6@ DEAT: 'direct' grade on index or online-tree citations only")
        self.assertRegex(joined, r"@I8@ DEAT: exact death date, but the notes give only a funeral or burial")
        self.assertRegex(joined, r"@I9@ BIRT: exact birth date, but the notes give only a baptism")
        self.assertRegex(joined, r"@I9@ BIRT: 'direct, original' but no saved image")
        # A direct, original grade with the image saved (Ernst's birth) is not flagged.
        self.assertNotRegex(joined, r"@I5@ BIRT")

    def test_evidence_warning_names_the_line(self):
        line = next(l for l in self.out.splitlines() if "@I8@ DEAT" in l)
        n = int(re.search(r"line (\d+)", line).group(1))
        with open(os.path.join(self.tmp.name, "tree.ged"), encoding="utf-8") as f:
            self.assertEqual(f.read().splitlines()[n - 1], "1 DEAT")

    def test_summary_line(self):
        s = summary(self.out)
        self.assertIn("6 dated facts without a citation", s)
        self.assertIn("1 marked 'source not recorded'", s)
        self.assertIn("8 without an Evidence: note", s)
        self.assertIn("0 cited images under 700 px", s)
        self.assertIn("1 citations without a kind-of-source label", s)
        self.assertIn("5 evidence warnings", s)

    def test_owner_source_note_counts_as_citation(self):
        # Bernd's birth has no SOUR line but "Source: the project owner" (the default owner).
        lists = self.out.split("Dated facts without a citation:")[1].split("Facts marked")[0]
        self.assertNotIn("@I2@", lists)
        self.assertIn("@F1@ MARR 1988", lists)
        self.assertIn("@I9@ CHR 1 MAR 1900", lists)

    def test_link_grades(self):
        s = summary(self.out)
        self.assertIn("1 established, 1 probable, 1 hypothesis (link kept), 1 not yet graded", s)
        ungraded = self.out.split("Direct-line people whose parent link has no 'Parents:' grade yet:")[1]
        self.assertTrue(ungraded.lstrip().startswith("@I3@"))

    def test_without_sources_flag_lists_are_hidden(self):
        r = support.run(self.tmp.name, "tools/check_tree.py")
        self.assertNotIn("Dated facts without a citation:", r.stdout)
        self.assertIn("details: --sources", r.stdout)


class StructuralErrors(unittest.TestCase):
    def check(self, tree, config=None, images=()):
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, tree=tree, config=config, images=images)
            return support.run(tmp, "tools/check_tree.py")

    def test_one_sided_and_missing_links(self):
        tree = support.fixture_text().replace("1 CHIL @I4@\n", "").replace("0 @F5@ FAM\n1 HUSB @I11@", "0 @F5@ FAM\n1 HUSB @I99@")
        r = self.check(tree)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ERROR @I4@ has FAMC @F1@, but @F1@ does not list @I4@", r.stdout)
        self.assertIn("ERROR @F5@ HUSB points to missing person @I99@", r.stdout)
        self.assertIn("ERROR @I11@ has FAMS @F5@, but @F5@ does not list @I11@", r.stdout)

    def test_missing_family(self):
        tree = support.fixture_text().replace("1 FAMC @F5@", "1 FAMC @F77@")
        r = self.check(tree)
        self.assertIn("ERROR @I7@ FAMC points to missing family @F77@", r.stdout)

    def test_unconnected_person(self):
        tree = support.fixture_text().replace("0 TRLR", "0 @I50@ INDI\n1 NAME Stray /Person/\n0 TRLR")
        r = self.check(tree)
        self.assertEqual(r.returncode, 1)
        self.assertIn("ERROR @I50@ is not connected to @I1@", r.stdout)
        self.assertIn("next free IDs @I51@", r.stdout)

    def test_missing_cited_file_is_an_error(self):
        r = self.check(support.fixture_text())  # no image saved
        self.assertIn(f"ERROR @I5@ cites {support.ERNST_IMAGE}, which does not exist", r.stdout)

    def test_archived_image_is_not_an_error(self):
        """An image listed in sources-manifest.tsv (kept in the S3 archive) counts as saved."""
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, images=())
            with open(os.path.join(tmp, "sources-manifest.tsv"), "w", encoding="utf-8") as f:
                f.write("# path\tbytes\tsha256\n" + support.ERNST_IMAGE + "\t100\t" + "0" * 64 + "\n")
            r = support.run(tmp, "tools/check_tree.py")
        self.assertNotIn("does not exist", r.stdout)
        self.assertNotIn("@I5@ BIRT: 'direct, original' but no saved image", r.stdout)

    def test_trailing_full_stop_after_path(self):
        tree = support.fixture_text().replace("Ernst-Adler.jpg)", "Ernst-Adler.jpg.)")
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, tree=tree, images=() if not HAS_PIL else (support.ERNST_IMAGE,))
            r = support.run(tmp, "tools/check_tree.py")
        if HAS_PIL:
            self.assertNotIn("does not exist", r.stdout)

    @unittest.skipUnless(HAS_PIL, "Pillow needed")
    def test_small_image_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, image_size=(600, 200))
            r = support.run(tmp, "tools/check_tree.py", "--sources")
        self.assertIn("1 cited images under 700 px", r.stdout)
        self.assertIn(f"{support.ERNST_IMAGE} 600x200 (first cited by @I5@)", r.stdout)

    def test_root_from_project_json(self):
        tree = support.fixture_text()
        r = self.check(tree, config={"root": "@I5@"}, images=())
        # From Ernst, everyone is still connected through the families.
        self.assertIn("reachable from @I5@", r.stdout)

    def test_missing_root(self):
        r = self.check(support.fixture_text(), config={"root": "@I404@"}, images=())
        self.assertIn("ERROR root person @I404@ is missing", r.stdout)

    def test_owner_name_from_config(self):
        tree = support.fixture_text().replace("2 NOTE Source: the project owner", "2 NOTE Source: Jane")
        r1 = self.check(tree, config={"owner": "Jane"}, images=())
        r2 = self.check(tree, config={"owner": "Someone else"}, images=())
        self.assertIn("6 dated facts without a citation", r1.stdout)
        self.assertIn("7 dated facts without a citation", r2.stdout)

    def test_parent_age_and_posthumous_birth_warnings(self):
        tree = (support.fixture_text()
                .replace("1 NAME Johann /Adler/\n1 SEX M\n1 BIRT\n2 DATE 1 MAR 1900",
                         "1 NAME Johann /Adler/\n1 SEX M\n1 DEAT\n2 DATE 1920\n1 BIRT\n2 DATE 1 MAR 1925"))
        r = self.check(tree, images=())
        self.assertIn("WARNING @F4@ parent Johann /Adler/ born 1925, child Ernst /Adler/ born 1930", r.stdout)
        self.assertIn("WARNING @F4@ child Ernst /Adler/ born 1930, after parent Johann /Adler/ died 1920", r.stdout)

    def test_explicit_path_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, images=())
            other = os.path.join(tmp, "other.ged")
            with open(other, "w", encoding="utf-8") as f:
                f.write("0 HEAD\n0 @I1@ INDI\n1 NAME Only /One/\n0 TRLR\n")
            r = support.run(tmp, "tools/check_tree.py", other)
        self.assertIn("1 people, 0 families", r.stdout)

    def test_unparseable_line_is_warned(self):
        tree = support.fixture_text().replace("1 SEX M\n", "1 SEX M\nthis is not gedcom\n", 1)
        r = self.check(tree, images=())
        self.assertIn("WARNING unparseable line: 'this is not gedcom'", r.stdout)


class EvidenceCheckUnit(unittest.TestCase):
    """evidence_check() on hand-built facts, without a tree file."""

    def ev(self, tag, sub, base="/nonexistent"):
        return check_tree.evidence_check("@I1@", tag, sub, 10, base)

    def test_two_evidence_notes(self):
        out = self.ev("BIRT", [(2, "DATE", "1900"), (2, "NOTE", "Evidence: index."), (2, "NOTE", "Evidence: secondary.")])
        self.assertTrue(any("2 Evidence: notes on one fact" in o for o in out))

    def test_direct_original_with_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "sources"))
            open(os.path.join(tmp, "sources", "a.pdf"), "w").close()
            out = self.ev("BIRT", [(2, "DATE", "1900"), (2, "SOUR", "Register, with image (sources/a.pdf)"),
                                   (2, "NOTE", "Evidence: direct, original.")], base=tmp)
        self.assertEqual(out, [])

    def test_direct_informant_on_index_only(self):
        out = self.ev("BIRT", [(2, "DATE", "1900"), (2, "SOUR", "Index, index only"),
                               (2, "NOTE", "Evidence: direct, informant.")])
        self.assertTrue(any("'direct' grade on index" in o for o in out))

    def test_baptism_with_birth_stated_is_fine(self):
        out = self.ev("BIRT", [(2, "DATE", "1 MAR 1900"), (2, "SOUR", "Church book, index only"),
                               (2, "NOTE", "Evidence: index. Born 28 Feb, baptized 1 Mar.")])
        self.assertFalse(any("baptism" in o for o in out))

    def test_birth_source_named_in_citation_is_fine(self):
        out = self.ev("BIRT", [(2, "DATE", "1 MAR 1900"), (2, "SOUR", "Civil birth register, index only"),
                               (2, "NOTE", "Evidence: index. Baptism also recorded.")])
        self.assertFalse(any("baptism" in o for o in out))

    def test_german_burial_words(self):
        out = self.ev("DEAT", [(2, "DATE", "3 JAN 1800"), (2, "SOUR", "Kirchenbuch, index only"),
                               (2, "NOTE", "Evidence: index. Begraben am 3. Januar.")])
        self.assertTrue(any("funeral or burial" in o for o in out))

    def test_death_with_death_word_is_fine(self):
        out = self.ev("DEAT", [(2, "DATE", "3 JAN 1800"), (2, "SOUR", "Kirchenbuch, index only"),
                               (2, "NOTE", "Evidence: index. Died 1 Jan, buried 3 Jan.")])
        self.assertFalse(any("funeral or burial" in o for o in out))

    def test_year_only_dates_are_not_checked(self):
        out = self.ev("DEAT", [(2, "DATE", "1800"), (2, "NOTE", "Evidence: index. Buried.")])
        self.assertEqual(out, [])


class ParseUnit(unittest.TestCase):
    def test_parse_skips_blank_lines_and_keeps_line_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "t.ged")
            with open(p, "w", encoding="utf-8") as f:
                f.write("0 HEAD\n\n0 @I1@ INDI\n1 NAME A /B/\n\n1 SEX F\n0 TRLR\n")
            recs = check_tree.parse(p)
        self.assertEqual(recs["@I1@"]["type"], "INDI")
        self.assertEqual(recs["@I1@"]["lines"], [(1, "NAME", "A /B/"), (1, "SEX", "F")])
        self.assertEqual(recs["@I1@"]["linenos"], [4, 6])

    def test_first_year(self):
        lines = [(1, "BIRT", ""), (2, "DATE", "ABT 1850"), (1, "DEAT", ""), (2, "PLAC", "X")]
        self.assertEqual(check_tree.first_year(lines, "BIRT"), 1850)
        self.assertIsNone(check_tree.first_year(lines, "DEAT"))
        self.assertIsNone(check_tree.first_year(lines, "BURI"))


if __name__ == "__main__":
    unittest.main()
