"""Tests of tools/geocode_places.py. The OpenStreetMap service is never called:
query() is replaced with a fake that records what would have been sent."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import support

import geocode_places as gp


# Inside a project the module loads the project's own list from site-src/site.json; test the defaults.
@mock.patch.object(gp, "REWRITE", gp.DEFAULT_REWRITE)
class RewriteTests(unittest.TestCase):
    def test_street_address_is_dropped(self):
        self.assertEqual(gp.rewrite("12 Main Street, Springfield, Ohio, USA"), "Springfield, Ohio, USA")
        self.assertEqual(gp.rewrite("1204 1/2 Elm Ave., Springfield, Ohio, USA"), "Springfield, Ohio, USA")

    def test_brackets_and_parish(self):
        self.assertEqual(gp.rewrite("Musterdorf (now Neudorf), Hessen, Germany"), "Musterdorf, Hessen, Germany")
        self.assertEqual(gp.rewrite("Musterdorf parish, Kreis Altkreis, Prussia"), "Kreis Altkreis")

    def test_old_state_names(self):
        self.assertEqual(gp.rewrite("Musterdorf, Hessen-Darmstadt"), "Musterdorf, Hessen, Germany")
        self.assertEqual(gp.rewrite("Musterdorf, Baden"), "Musterdorf, Baden-Württemberg, Germany")
        self.assertEqual(gp.rewrite("Musterdorf, Heiliges Römisches Reich"), "Musterdorf, Germany")
        self.assertEqual(gp.rewrite("Ville, Normandie, France"), "Ville, France")

    def test_rules_are_applied_in_order(self):
        self.assertEqual(gp.rewrite("a, b", [("a", "b"), ("b", "c")]), "c, c")
        self.assertEqual(gp.rewrite("a, b", []), "a, b")

    def test_project_list_replaces_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "site.json")
            self.assertEqual(gp.load_rewrites(path), gp.DEFAULT_REWRITE)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"geocode_rewrites": [["^Oldname, ", "Newname, "]]}, f)
            self.assertEqual(gp.load_rewrites(path), [("^Oldname, ", "Newname, ")])
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"name": "x"}, f)
            self.assertEqual(gp.load_rewrites(path), gp.DEFAULT_REWRITE)

    def test_template_list_keeps_the_address_rule_first(self):
        template = os.path.join(support.FRAMEWORK, "templates", "site-src", "site.json")
        if not os.path.exists(template):
            self.skipTest("runs only in the framework repository, not in a synced project")
        with open(template, encoding="utf-8") as f:
            rules = json.load(f)["geocode_rewrites"]
        self.assertEqual(rules[0][0], gp.DEFAULT_REWRITE[0][0])
        self.assertEqual([tuple(r) for r in rules], gp.DEFAULT_REWRITE)


def osm(rank, lat="39.92", lon="-83.81", name="Springfield, Ohio, United States"):
    return {"lat": lat, "lon": lon, "place_rank": rank, "display_name": name}


@mock.patch.object(gp, "REWRITE", gp.DEFAULT_REWRITE)
class GeocodeTests(unittest.TestCase):
    def fake(self, answers):
        self.sent = []

        def query(q):
            self.sent.append(q)
            return answers.get(q)
        return mock.patch.object(gp, "query", side_effect=query)

    def test_town_match_is_a_place(self):
        with self.fake({"Springfield, Ohio, USA": osm(16)}):
            r = gp.geocode("Springfield, Ohio, USA")
        self.assertEqual(r["precision"], "place")
        self.assertEqual((r["lat"], r["lon"]), (39.92, -83.81))
        self.assertEqual(r["dropped"], "")
        self.assertEqual(r["query"], "Springfield, Ohio, USA")

    def test_parts_are_dropped_from_the_front_until_a_match(self):
        with self.fake({"Ohio, USA": osm(8)}):
            r = gp.geocode("Old Cemetery, Springfield, Ohio, USA")
        self.assertEqual(self.sent, ["Old Cemetery, Springfield, Ohio, USA", "Springfield, Ohio, USA", "Ohio, USA"])
        self.assertEqual(r["precision"], "region")
        self.assertEqual(r["dropped"], "Old Cemetery, Springfield")

    def test_no_match(self):
        with self.fake({}):
            r = gp.geocode("Nowhere, Atlantis")
        self.assertEqual(r, {"precision": "none", "query": "Nowhere, Atlantis"})

    def test_error_stops_the_lookup(self):
        with self.fake({"Springfield, Ohio, USA": "error"}):
            r = gp.geocode("Springfield, Ohio, USA")
        self.assertEqual(r, {"precision": "error", "query": "Springfield, Ohio, USA"})
        self.assertEqual(len(self.sent), 1)

    def test_street_address_never_reaches_the_service(self):
        with self.fake({}):
            gp.geocode("12 Main Street, Springfield, Ohio, USA")
        self.assertTrue(self.sent)
        self.assertFalse(any("Main Street" in q for q in self.sent))


class QueryTests(unittest.TestCase):
    def test_query_parses_and_waits(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = json.dumps([osm(16)]).encode()
        with mock.patch.object(gp.urllib.request, "urlopen", return_value=resp) as op, \
                mock.patch.object(gp.time, "sleep") as sleep:
            r = gp.query("Springfield, Ohio, USA")
        self.assertEqual(r["place_rank"], 16)
        req = op.call_args[0][0]
        self.assertIn("q=Springfield%2C+Ohio%2C+USA", req.full_url)
        self.assertIn("nominatim.openstreetmap.org", req.full_url)
        self.assertTrue(req.get_header("User-agent"))
        sleep.assert_called_with(1.1)  # the service asks for at most one request a second

    def test_empty_answer_is_none(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = b"[]"
        with mock.patch.object(gp.urllib.request, "urlopen", return_value=resp), mock.patch.object(gp.time, "sleep"):
            self.assertIsNone(gp.query("x"))

    def test_three_failures_give_error(self):
        with mock.patch.object(gp.urllib.request, "urlopen", side_effect=OSError("down")) as op, \
                mock.patch.object(gp.time, "sleep"), mock.patch.object(sys, "stderr"):
            self.assertEqual(gp.query("x"), "error")
        self.assertEqual(op.call_count, 3)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.make_project(self.tmp.name, images=())
        self.out = os.path.join(self.tmp.name, "site-src", "places.json")
        self.patches = [mock.patch.object(gp, "OUT", self.out),
                        mock.patch.object(gp.cfg, "TREE", os.path.join(self.tmp.name, "tree.ged"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_main(self, *argv):
        calls = []

        def geocode(p):
            calls.append(p)
            return {"precision": "place", "lat": 1.0, "lon": 2.0, "query": p}
        with mock.patch.object(gp, "geocode", side_effect=geocode), mock.patch.object(sys, "argv", ["geocode_places.py", *argv]), \
                mock.patch("builtins.print"):
            gp.main()
        with open(self.out, encoding="utf-8") as f:
            return calls, json.load(f)

    def test_looks_up_each_place_once_and_caches(self):
        calls, cache = self.run_main()
        self.assertEqual(sorted(calls), ["Musterdorf, Hessen, Germany", "Springfield, Ohio, USA"])
        self.assertEqual(set(cache), set(calls))
        calls, _ = self.run_main()
        self.assertEqual(calls, [])

    def test_errors_are_retried_and_redo_redoes_all(self):
        os.makedirs(os.path.dirname(self.out), exist_ok=True)
        with open(self.out, "w", encoding="utf-8") as f:
            json.dump({"Springfield, Ohio, USA": {"precision": "error"}, "Musterdorf, Hessen, Germany": {"precision": "place"}}, f)
        calls, _ = self.run_main()
        self.assertEqual(calls, ["Springfield, Ohio, USA"])
        calls, _ = self.run_main("--redo")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
