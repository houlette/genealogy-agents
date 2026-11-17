"""Tests for person.py, frontier.py, living.py, make-tree-view.py and project_config.py."""
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import support

import person  # noqa: E402
import living  # noqa: E402


def load_mtv():
    spec = importlib.util.spec_from_file_location("mtv", os.path.join(support.FRAMEWORK, "make-tree-view.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Project(unittest.TestCase):
    """A fresh copy of the fixture project for each test class."""
    config = None
    tree = None

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.p = support.make_project(cls.tmpdir.name, tree=cls.tree, config=cls.config, images=())

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def run_tool(self, script, *args):
        r = support.run(self.p, script, *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout


class PersonTool(Project):
    def test_one_person_with_relatives_and_line_numbers(self):
        out = self.run_tool("tools/person.py", "@I5@")
        self.assertTrue(out.startswith("@I5@ Ernst Adler (2 FEB 1930 - 2001)"))
        self.assertRegex(out, r"\n\s+\d+  0 @I5@ INDI\n")
        self.assertIn("parents (@F4@): @I9@ Johann Adler (1 MAR 1900 - ?); @I10@ Katharina Ebert (? - ?)", out)
        self.assertIn("spouse (@F2@): @I6@ Frieda Clarke (1932 - 2010)", out)
        self.assertIn("  child @I2@ Bernd Adler (1960 - ?)", out)

    def test_line_numbers_match_the_file(self):
        out = self.run_tool("tools/person.py", "@I8@")
        m = re.search(r"^\s+(\d+)  1 NOTE For review", out, re.M)
        with open(os.path.join(self.p, "tree.ged"), encoding="utf-8") as f:
            self.assertTrue(f.read().splitlines()[int(m.group(1)) - 1].startswith("1 NOTE For review"))

    def test_brief_omits_gedcom_lines(self):
        out = self.run_tool("tools/person.py", "@I1@", "--brief")
        self.assertNotIn("0 @I1@ INDI", out)
        self.assertIn("parents (@F1@): @I2@ Bernd Adler (1960 - ?); @I3@ Clara Brandt (1962 - 12 JUN 2020)", out)

    def test_family_record(self):
        out = self.run_tool("tools/person.py", "@F1@")
        self.assertIn("1 MARR", out)
        self.assertIn("  husb  @I2@ Bernd Adler", out)
        self.assertIn("  wife  @I3@ Clara Brandt", out)
        self.assertIn("  chil  @I4@ Dana Adler (1993 - ?)", out)

    def test_name_search_is_case_insensitive(self):
        out = self.run_tool("tools/person.py", "brandt")
        self.assertEqual([l.split()[0] for l in out.split("\n") if l.strip()], ["@I3@", "@I7@", "@I11@"])

    def test_no_match(self):
        self.assertIn("no person matches 'Zzz'", self.run_tool("tools/person.py", "Zzz"))

    def test_no_arguments_prints_usage(self):
        r = support.run(self.p, "tools/person.py")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Look up people in tree.ged", r.stdout)

    def test_label_falls_back_to_christening_and_burial(self):
        recs = {"@I1@": {"type": "INDI", "lines": [(1, "0 @I1@ INDI"), (2, "1 NAME A /B/"), (3, "1 CHR"),
                                                   (4, "2 DATE 1700"), (5, "1 BURI"), (6, "2 DATE 1760")]}}
        self.assertEqual(person.label(recs, "@I1@"), "@I1@ A B (1700 - 1760)")
        self.assertEqual(person.label(recs, "@I9@"), "@I9@ (missing)")

    def test_tree_path_from_project_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, config={"tree": "data/family.ged"}, images=())
            os.makedirs(os.path.join(tmp, "data"))
            os.rename(os.path.join(tmp, "tree.ged"), os.path.join(tmp, "data", "family.ged"))
            r = support.run(tmp, "tools/person.py", "@I1@", "--brief")
        self.assertIn("@I1@ Alex Adler", r.stdout)


class FrontierTool(Project):
    def rows(self, *args):
        return [l for l in self.run_tool("tools/frontier.py", *args).splitlines() if re.match(r"^\s*\d+\s+@", l)]

    def test_frontier_rows_and_flags(self):
        rows = self.rows()
        self.assertEqual(len(rows), 6)
        joined = "\n".join(rows)
        self.assertRegex(joined, r"2  @I6@ Frieda Clarke .*missing 2 \| unverified")
        self.assertRegex(joined, r"2  @I7@ Georg Brandt .*missing 1 \| hypothesis")
        self.assertRegex(joined, r"2  @I8@ Hanna Doyle .*missing 2 \| for-review")
        self.assertRegex(joined, r"3  @I9@ Johann Adler .*line: Johann Adler \| missing 2 \| -")

    def test_nearest_generation_first(self):
        gens = [int(r.split()[0]) for r in self.rows()]
        self.assertEqual(gens, sorted(gens))

    def test_max_gen(self):
        self.assertEqual(len(self.rows("--max-gen", "2")), 3)

    def test_clean_keeps_hypotheses_only(self):
        rows = self.rows("--clean")
        joined = "\n".join(rows)
        self.assertNotIn("unverified", joined)
        self.assertNotIn("for-review", joined)
        self.assertIn("hypothesis", joined)

    def test_summary(self):
        self.assertIn("6 shown of 6 frontier ancestors (9 direct ancestors in all)", self.run_tool("tools/frontier.py"))


class FrontierWeakLine(Project):
    # Mark Ernst (gen 2) for review; his parents (gen 3) then sit on a weak line.
    tree = support.fixture_text().replace(
        "1 NOTE Parents: probable. Birth register", "1 NOTE For review: new link.\n1 NOTE Parents: probable. Birth register")

    def test_weak_line_propagates_upward(self):
        out = self.run_tool("tools/frontier.py")
        self.assertRegex(out, r"3  @I9@ Johann Adler .*weak-line")
        self.assertRegex(out, r"3  @I10@ Katharina Ebert .*weak-line")
        self.assertNotRegex(out, r"@I11@ Ludwig Brandt .*weak-line")


class FrontierOtherRoot(Project):
    config = {"root": "@I3@"}

    def test_root_from_config(self):
        out = self.run_tool("tools/frontier.py")
        self.assertNotIn("Frieda", out)
        self.assertRegex(out, r"1  @I8@ Hanna Doyle")


class LivingTool(Project):
    def test_possibly_living(self):
        out = self.run_tool("tools/living.py")
        cutoff = datetime.date.today().year - 100
        self.assertIn(f"born or estimated after {cutoff}): 3", out)
        self.assertIn("@I1@ Alex Adler, born 1990", out)
        self.assertIn("No birth year and no relative to estimate from (treated as living): 0", out)
        self.assertNotIn("Clara", out)  # died 2020

    def test_estimates_from_relatives(self):
        people = {
            "A": {"by": 1900, "died": True, "famc": [], "fams": ["F1"], "names": ["Anna"]},
            "B": {"by": None, "died": False, "famc": ["F1"], "fams": ["F2"], "names": ["Ben"]},
            "C": {"by": None, "died": False, "famc": [], "fams": ["F2"], "names": ["Cora"]},
            "D": {"by": None, "died": False, "famc": [], "fams": [], "names": ["Dirk"]},
            "E": {"by": None, "died": False, "famc": [], "fams": ["F3"], "names": ["Eva"]},
            "G": {"by": 1990, "died": False, "famc": ["F3"], "fams": [], "names": ["Gus"]},
        }
        fams = {"F1": {"husb": None, "wife": "A", "chil": ["B"]},
                "F2": {"husb": "B", "wife": "C", "chil": []},
                "F3": {"husb": None, "wife": "E", "chil": ["G"]}}
        est = living.estimates(people, fams)
        self.assertEqual(est["B"], (1925, "parent Anna"))
        self.assertEqual(est["C"], (1925, "spouse Ben"))
        self.assertEqual(est["E"], (1965, "child Gus"))
        self.assertNotIn("D", est)
        old = living.CUTOFF
        try:
            living.CUTOFF = 1950
            alive, unknown, _ = living.possibly_living(people, fams)
        finally:
            living.CUTOFF = old
        self.assertEqual(sorted(alive), ["E", "G"])
        self.assertEqual(unknown, ["D"])


class LivingCutoffFromConfig(Project):
    config = {"living_cutoff": 1991}

    def test_cutoff(self):
        out = self.run_tool("tools/living.py")
        self.assertIn("after 1991): 1", out)
        self.assertIn("@I4@ Dana Adler, born 1993", out)


class Viewer(Project):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.out = support.run(cls.p, "make-tree-view.py", cwd=tempfile.gettempdir())
        with open(os.path.join(cls.p, "tree-view.html"), encoding="utf-8") as f:
            cls.html = f.read()
        m = re.search(r"const DATA = (\{.*?\});\n", cls.html, re.S)
        cls.data = json.loads(m.group(1).replace("<\\/", "</"))

    def test_runs_from_any_folder_and_writes_into_project(self):
        self.assertEqual(self.out.returncode, 0, self.out.stderr)
        self.assertIn("tree-view.html: 11 people, 5 families", self.out.stdout)

    def test_data_is_embedded(self):
        self.assertEqual(self.data["root"], "I1")
        self.assertEqual(self.data["built"], datetime.date.today().isoformat())
        e = self.data["people"]["I5"]
        self.assertEqual((e["names"], e["by"], e["dy"], e["bplace"]),
                         (["Ernst Adler"], 1930, 2001, "Musterdorf, Hessen, Germany"))
        self.assertEqual(e["link"], "probable")
        self.assertEqual(self.data["people"]["I7"]["link"], "hypothesis")
        self.assertEqual(self.data["fams"]["F1"]["chil"], ["I1", "I4"])

    def test_no_personal_names_in_template(self):
        # Branch chips are built from the root's grandparents at run time, not written into the page.
        self.assertNotIn('data-b="0"', self.html)
        self.assertIn("surname(r)", self.html)
        self.assertIn('id="home">Back to start<', self.html)

    def test_script_close_tags_are_escaped(self):
        tree = support.fixture_text().replace("1 NAME Alex /Adler/", "1 NAME Alex /Adler/\n1 NOTE a </script> b")
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, tree=tree, images=())
            support.run(tmp, "make-tree-view.py")
            with open(os.path.join(tmp, "tree-view.html"), encoding="utf-8") as f:
                html = f.read()
        self.assertEqual(html.count("</script>"), 1)

    def test_root_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, config={"root": "@I5@"}, images=())
            support.run(tmp, "make-tree-view.py")
            with open(os.path.join(tmp, "tree-view.html"), encoding="utf-8") as f:
                self.assertIn('"root": "I5"', f.read())

    @unittest.skipUnless(subprocess.run(["which", "node"], capture_output=True).returncode == 0, "node not installed")
    def test_script_parses(self):
        script = self.html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        try:
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        finally:
            os.unlink(f.name)
        self.assertEqual(r.returncode, 0, r.stderr)


class ViewerFacts(unittest.TestCase):
    def test_fact_fields(self):
        mtv = load_mtv()
        recs = mtv.parse(
            "0 @I1@ INDI\n1 NAME Ann /Lee/\n2 NSFX Jr.\n1 SEX F\n1 RESI\n2 DATE 1900\n2 PLAC Town\n2 ADDR 1 Main St\n"
            "1 DEAT\n2 CAUS fever\n2 SOUR Register\n1 EVEN\n2 TYPE Archival Extinction\n2 NOTE No earlier records\n"
            "2 SOUR Inventory\n0 TRLR\n")
        people, _ = mtv.build(recs)
        p = people["I1"]
        self.assertEqual(p["nsfx"], "Jr.")
        resi = next(e for e in p["events"] if e["tag"] == "RESI")
        self.assertEqual((resi["label"], resi["date"], resi["place"]), ("Lived", "1900", "1 Main St, Town"))
        deat = next(e for e in p["events"] if e["tag"] == "DEAT")
        self.assertEqual((deat["notes"], deat["sources"]), (["Cause: fever"], ["Register"]))
        self.assertTrue(p["died"])
        self.assertIsNone(p["dy"])
        self.assertTrue(p["extinct"])
        self.assertEqual((p["extinct_reason"], p["extinct_sources"]), ("No earlier records", ["Inventory"]))


class ProjectConfig(unittest.TestCase):
    def config_of(self, project_json):
        with tempfile.TemporaryDirectory() as tmp:
            if project_json is not None:
                with open(os.path.join(tmp, "project.json"), "w") as f:
                    f.write(project_json)
            r = support.run(tmp, "tools/project_config.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout), tmp

    def test_defaults_without_project_json(self):
        c, tmp = self.config_of(None)
        self.assertEqual(c["root"], "@I1@")
        self.assertEqual(c["owner"], "the project owner")
        self.assertEqual(c["bridge"]["port"], 9333)
        self.assertIsNone(c["bridge"]["profile"])
        self.assertEqual(c["_living_cutoff_in_force"], datetime.date.today().year - 100)
        self.assertEqual(os.path.realpath(c["_project_dir"]), os.path.realpath(tmp))

    def test_nested_merge_keeps_other_defaults(self):
        c, _ = self.config_of('{"owner": "Jane", "bridge": {"profile": "Research"}, "_note": "x", "living_cutoff": 1920}')
        self.assertEqual(c["owner"], "Jane")
        self.assertEqual(c["bridge"], {"port": 9333, "profile": "Research", "downloads": "~/Downloads"})
        self.assertNotIn("_note", c)
        self.assertEqual(c["_living_cutoff_in_force"], 1920)

    def test_module_constants(self):
        code = ("import project_config as c, json; print(json.dumps([c.OWNER, c.ROOT, c.TREE, c.KEYFILE, c.LIVING_CUTOFF]))")
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "project.json"), "w") as f:
                json.dump({"owner": "Jane", "root": "@I7@", "tree": "x.ged", "keyfile": "~/k.env"}, f)
            r = subprocess.run([sys.executable, "-c", code], cwd=support.TOOLS, env=support.env_for(tmp),
                               capture_output=True, text=True)
        owner, root, tree, keyfile, cutoff = json.loads(r.stdout)
        self.assertEqual((owner, root), ("Jane", "@I7@"))
        self.assertEqual(tree, os.path.join(tmp, "x.ged"))
        self.assertEqual(keyfile, os.path.expanduser("~/k.env"))
        self.assertEqual(cutoff, datetime.date.today().year - 100)

    def test_bad_json_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "project.json"), "w") as f:
                f.write("{not json")
            r = support.run(tmp, "tools/project_config.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("JSONDecodeError", r.stderr)


class MissingRoot(unittest.TestCase):
    """Every tree tool reports a missing root person instead of crashing."""

    def test_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.make_project(tmp, config={"root": "@I404@"}, images=())
            for script in ("tools/check_tree.py", "tools/frontier.py", "make-tree-view.py", "tools/living.py"):
                r = support.run(tmp, script)
                self.assertNotIn("Traceback", r.stderr, script)
            self.assertIn("root person @I404@ is missing", support.run(tmp, "tools/frontier.py").stdout +
                          support.run(tmp, "tools/frontier.py").stderr)


if __name__ == "__main__":
    unittest.main()
