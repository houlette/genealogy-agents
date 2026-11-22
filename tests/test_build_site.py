"""Tests of tools/build_site.py, the family website builder.

The pure functions are tested directly. The build is run as a subprocess
against a throwaway project made from the fictional fixture tree, with extra
events, images, stories and settings added per test, and its two outputs are
read back: site/data/public.json (what every approved reader may load) and
private/private.json (read only by the sign-in app). Most of these tests are
about privacy: living people must never reach the public data.
"""
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock

import support

import build_site as bs

try:
    import markdown  # noqa: F401
    from PIL import Image  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

# Ludwig Brandt (I11) is the only person in the fixture who is neither living
# nor "recent" (he has no living child or grandchild), so his details are public.
LUDWIG_EXTRA = """1 DEAT
2 DATE 1990
1 OCCU brickmaker
1 RESI
2 DATE 1900
2 PLAC Springfield, Ohio, USA
2 SOUR 1900 US census, NARA T623, Springfield, sheet 4, line 12, with image (sources/1900-06-census-Springfield-Ludwig-Brandt.jpg)
2 NOTE Evidence: direct, original. Census enumeration.
1 RESI
2 DATE 1940
2 PLAC Springfield, Ohio, USA
2 SOUR 1940 US census, NARA T627, Springfield, sheet 2, with image (sources/1940-04-census-Springfield-Ludwig-Brandt.jpg)
2 NOTE Evidence: direct, original. Census enumeration.
1 BURI
2 DATE 1990
2 PLAC Springfield, Ohio, USA
2 SOUR Find a Grave memorial 2, online tree, lead only, with image (sources/1990-headstone-Springfield-Ludwig-Brandt.jpg)
2 NOTE Evidence: lead only. Memorial text.
1 NOTE A research note about Ludwig.
1 FAMS @F5@"""

LUDWIG_IMAGES = ("sources/1900-06-census-Springfield-Ludwig-Brandt.jpg",
                 "sources/1940-04-census-Springfield-Ludwig-Brandt.jpg",
                 "sources/1990-headstone-Springfield-Ludwig-Brandt.jpg")

RIGHTS = {"show": ["\\bUS census\\b", "\\bNARA\\b", "Find a Grave"], "never": ["Find a Grave"]}


def tree_with_ludwig():
    text = support.fixture_text()
    old = "1 DEAT\n2 DATE 1990\n1 FAMS @F5@"
    assert old in text
    return text.replace(old, LUDWIG_EXTRA)


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if isinstance(text, str) else json.dumps(text))


def site_project(tmp, tree=None, site=None, rights=RIGHTS, extra_images=(), files=None):
    """A project with the front end copied in, ready for build_site.py."""
    support.make_project(tmp, tree=tree if tree is not None else tree_with_ludwig(),
                         config={"owner": "Alex", "root": "@I1@", "living_cutoff": 1926},
                         images=(support.ERNST_IMAGE, *LUDWIG_IMAGES, *extra_images))
    os.makedirs(os.path.join(tmp, "site-src"), exist_ok=True)
    for name in ("index.html", "app.js", "style.css"):
        shutil.copy(os.path.join(support.FRAMEWORK, "site-src", name), os.path.join(tmp, "site-src", name))
    if rights is not None:
        write(os.path.join(tmp, "site-src", "rights.json"), rights)
    if site is not None:
        write(os.path.join(tmp, "site-src", "site.json"), site)
    for rel, text in (files or {}).items():
        write(os.path.join(tmp, rel), text)
    return tmp


def build(project, *args):
    return support.run(project, "tools/build_site.py", *args)


def outputs(project):
    with open(os.path.join(project, "site", "data", "public.json"), encoding="utf-8") as f:
        pub = json.load(f)
    with open(os.path.join(project, "private", "private.json"), encoding="utf-8") as f:
        priv = json.load(f)
    return pub, priv


# ---------- pure functions ----------

class DateTests(unittest.TestCase):
    def test_sort_year(self):
        self.assertEqual(bs.sort_year("1900"), 1900)
        self.assertAlmostEqual(bs.sort_year("1 JUL 1900"), 1900.5, places=2)
        self.assertAlmostEqual(bs.sort_year("ABT 1850"), 1850)
        self.assertIsNone(bs.sort_year(""))
        self.assertIsNone(bs.sort_year(None))
        self.assertIsNone(bs.sort_year("Y"))

    def test_pretty_date(self):
        self.assertEqual(bs.pretty_date("ABT 1850"), "about 1850")
        self.assertEqual(bs.pretty_date("BEF 12 MAR 1700"), "before 12 Mar 1700")
        self.assertEqual(bs.pretty_date("AFT 1800"), "after 1800")
        self.assertEqual(bs.pretty_date("BET 1850 AND 1860"), "1850 to 1860")
        self.assertEqual(bs.pretty_date("FROM 1850 TO 1860"), "1850 to 1860")
        self.assertEqual(bs.pretty_date(None), "")

    def test_date_phrase_keeps_certainty(self):
        self.assertEqual(bs.date_phrase("12 MAY 1765"), "on 12 May 1765")
        self.assertEqual(bs.date_phrase("JUL 1839"), "in July 1839")
        self.assertEqual(bs.date_phrase("1839"), "in 1839")
        self.assertEqual(bs.date_phrase("ABT 1580"), "about 1580")
        self.assertEqual(bs.date_phrase("EST 1580"), "about 1580")
        self.assertEqual(bs.date_phrase("BEF 1700"), "before 1700")
        self.assertEqual(bs.date_phrase("AFT 3 JAN 1700"), "after 3 January 1700")
        self.assertEqual(bs.date_phrase("BET 1850 AND 1860"), "between 1850 and 1860")
        self.assertEqual(bs.date_phrase(""), "")
        self.assertEqual(bs.date_phrase("Y"), "")
        self.assertEqual(bs.date_phrase("about the war"), "")


class PlaceTests(unittest.TestCase):
    def test_short_place_drops_the_country_for_three_countries(self):
        self.assertEqual(bs.short_place("Springfield, Ohio, USA"), "Springfield, Ohio")
        self.assertEqual(bs.short_place("Musterdorf, Hessen, Germany"), "Musterdorf, Hessen")
        self.assertEqual(bs.short_place("Dublin, Ireland"), "Dublin, Ireland")
        self.assertEqual(bs.short_place(None), "")

    def test_place_phrase_keeps_first_and_last_parts(self):
        self.assertEqual(bs.place_phrase("Main Street, Springfield, Clark, Ohio, USA"), "Main Street, Ohio")
        self.assertEqual(bs.place_phrase("Springfield, Ohio, USA"), "Springfield, Ohio")

    def test_regions_use_the_configured_patterns(self):
        defaults = [(pat, code) for pat, code in bs.SITE_DEFAULTS["regions"]]
        with unittest.mock.patch.object(bs, "REGION_WORDS", defaults):
            self.check_default_regions()

    def check_default_regions(self):
        self.assertEqual(bs.regions("Musterdorf, Prussia"), ["de"])
        self.assertEqual(bs.regions("Springfield, Ohio, USA"), ["us"])
        self.assertEqual(bs.regions("Nowhere"), [])
        self.assertEqual(bs.regions(None), [])


class HedgeTests(unittest.TestCase):
    def hedge(self, note):
        return bs.hedge({"notes": [note]})

    def test_hedge_words_follow_the_evidence_grade(self):
        self.assertEqual(self.hedge("Evidence: direct, original. Seen."), "")
        self.assertEqual(self.hedge("Evidence: index. Index only."), "")
        self.assertEqual(self.hedge("Evidence: secondary. A county history."), "reportedly ")
        self.assertEqual(self.hedge("Evidence: inferred, probable. Argued."), "probably ")
        self.assertEqual(self.hedge("Evidence: conflict. Two dates."), "probably ")
        self.assertEqual(self.hedge("Evidence: inferred, possible. Weak."), "possibly ")
        self.assertEqual(self.hedge("Evidence: lead only. A tree."), "possibly ")
        self.assertEqual(self.hedge("Evidence: source not recorded."), "possibly ")
        self.assertEqual(bs.hedge({"notes": []}), "")


class SiteConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "site.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_a_file(self):
        conf = bs.load_site_config(self.path)
        self.assertEqual(conf["name"], "Our Family History")
        self.assertEqual(conf["owner_name"], bs.cfg.OWNER)
        self.assertEqual(conf["root_name"], conf["owner_name"])
        self.assertTrue(conf["regions"])
        self.assertTrue(all(1 <= c["color"] <= 6 for c in conf["countries"]))

    def test_file_overrides_and_ignores_notes(self):
        write(self.path, {"_note": "x", "name": "The Adler Family", "owner_name": "Alex", "regions": [["Ohio", "oh"]]})
        conf = bs.load_site_config(self.path)
        self.assertEqual(conf["name"], "The Adler Family")
        self.assertEqual(conf["owner_name"], "Alex")
        self.assertEqual(conf["root_name"], "Alex")
        self.assertEqual(conf["regions"], [["Ohio", "oh"]])
        self.assertNotIn("_note", conf)

    def test_null_names_fall_back(self):
        write(self.path, {"owner_name": None, "root_name": None})
        conf = bs.load_site_config(self.path)
        self.assertEqual(conf["owner_name"], bs.cfg.OWNER)

    def test_default_patterns_compile_and_shared_syntax(self):
        import re
        for pat, _ in bs.SITE_DEFAULTS["regions"]:
            re.compile(pat)
        for c in bs.SITE_DEFAULTS["countries"]:
            re.compile(c["match"])
            self.assertNotIn("(?P<", c["match"])  # Python-only syntax would break the page


class ImageHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        os.makedirs("sources")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def touch(self, rel, text=""):
        write(rel, text)

    def test_files_in_finds_cited_images_and_images_beside_a_cited_note(self):
        self.touch("sources/1900-census-A.jpg")
        self.touch("sources/1901-birth-B.md")
        self.touch("sources/1901-birth-B.png")
        self.touch("sources/1901-birth-B-crop.jpg")
        self.touch("sources/1901-birth-B-p01.jpg")  # a page of a multi-page item: left out
        c = "Census, with image (sources/1900-census-A.jpg); register (sources/1901-birth-B.md); missing (sources/none.jpg)."
        self.assertEqual(bs.files_in(c), ["sources/1900-census-A.jpg", "sources/1901-birth-B-crop.jpg", "sources/1901-birth-B.png"])

    def test_files_in_caps_at_six_and_removes_duplicates(self):
        names = [f"sources/1900-x-{i}.jpg" for i in range(8)]
        for n in names:
            self.touch(n)
        c = " ".join(names + names[:2])
        self.assertEqual(bs.files_in(c), names[:6])

    def test_allowed_needs_show_and_not_never(self):
        bs.RIGHTS = {"show": ["census"], "never": ["Find a Grave"]}
        try:
            self.assertTrue(bs.allowed("1900 US Census, NARA"))
            self.assertFalse(bs.allowed("Find a Grave census photo"))
            self.assertFalse(bs.allowed("Parish register"))
        finally:
            bs.RIGHTS = {"show": [], "never": []}

    def test_sidecar_of_strips_trailing_parts(self):
        self.touch("sources/1850-baptism-X.md")
        self.assertEqual(bs.sidecar_of("sources/1850-baptism-X-crop-2.jpg"), "sources/1850-baptism-X.md")
        self.assertIsNone(bs.sidecar_of("sources/other.jpg"))

    def test_caption_uses_the_note_heading_and_translation_only_for_old_records(self):
        self.touch("sources/1850-03-04-baptism-Musterdorf-X.md", "# Baptism of X, 1850\n\n- Translation: On 4 March was baptized X.\n")
        cap = bs.caption_of("sources/1850-03-04-baptism-Musterdorf-X.jpg", "Church book")
        self.assertEqual(cap["title"], "Baptism of X, 1850")
        self.assertEqual(cap["group"], "Births and baptisms")
        self.assertEqual(cap["yr"], 1850)
        self.assertEqual(cap["tr"], "On 4 March was baptized X.")
        self.touch("sources/1950-04-census-Y.md", "# Census\n\nTranslation: names a living person\n")
        cap = bs.caption_of("sources/1950-04-census-Y.jpg", "1950 census")
        self.assertEqual(cap["tr"], "")
        self.assertEqual(cap["group"], "Censuses")

    def test_caption_without_a_note_is_built_from_the_file_name(self):
        cap = bs.caption_of("sources/1917-06-05-draft-card-Z.jpg", "WWI draft registration")
        self.assertEqual(cap["title"], "Draft card, 5 June 1917")
        self.assertEqual(cap["group"], "Military")
        cap = bs.caption_of("sources/scan.jpg", "1880 US census, Springfield, sheet 3")
        self.assertEqual(cap["title"], "1880 US census, Springfield")
        cap = bs.caption_of("sources/scan.jpg", "Something")
        self.assertEqual((cap["title"], cap["group"]), ("Record", "Other records"))


# ---------- the build ----------

@unittest.skipUnless(HAVE_DEPS, "needs the markdown and Pillow packages")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.p = site_project(os.path.join(cls.tmp.name, "p"), site={"name": "The Adler & Brandt Family", "owner_name": "Alex"})
        cls.result = build(cls.p, "--report")
        if cls.result.returncode == 0:
            cls.pub, cls.priv = outputs(cls.p)
            with open(os.path.join(cls.p, "site", "data", "public.json"), encoding="utf-8") as f:
                cls.pub_text = f.read()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)

    def test_report(self):
        self.assertIn("site/: 11 people (3 living as stubs, 7 recent)", self.result.stdout)
        self.assertIn("Places not on the map:", self.result.stdout)

    def test_living_people_are_stubs_only(self):
        for k in ("I1", "I2", "I4"):
            self.assertEqual(set(self.pub["people"][k]), {"lv", "sx", "famc", "fams"}, k)
        for name in ("Alex", "Bernd", "Dana", "Springfield cemetery"):
            if name == "Alex":
                # the owner's name is in the site settings by design, but nowhere else
                self.assertNotIn("Alex", json.dumps(self.pub["people"]) + json.dumps(self.pub["fams"]))
            else:
                self.assertNotIn(name, json.dumps(self.pub["people"]), name)
        self.assertNotIn("4 May 1990", self.pub_text)

    def test_living_details_are_in_the_private_file(self):
        self.assertEqual(sorted(self.priv["living"]), ["I1", "I2", "I4"])
        self.assertEqual(self.priv["names"]["I1"], "Alex Adler")
        self.assertEqual(self.priv["people"]["I1"]["n"], "Alex Adler")
        born = [e for e in self.priv["people"]["I1"]["ev"] if e["t"] == "Born"][0]
        self.assertEqual(born["d"], "4 May 1990")

    def test_family_of_living_parents_has_no_public_events(self):
        self.assertEqual(self.pub["fams"]["F1"]["ev"], [])
        self.assertIn("F1", self.priv["fams"])
        self.assertEqual(self.pub["fams"]["F1"]["c"], ["I1", "I4"])

    def test_recent_people_keep_names_but_not_notes_or_citations(self):
        clara = self.pub["people"]["I3"]
        self.assertEqual(clara["n"], "Clara Brandt")
        self.assertEqual(clara["rc"], 1)
        self.assertEqual(clara["notes"], [])
        for e in clara["ev"]:
            self.assertNotIn("src", e)
            self.assertNotIn("ev", e)
        self.assertIn("I3", self.priv["people"])
        died = [e for e in self.priv["people"]["I3"]["ev"] if e["t"] == "Died"][0]
        self.assertIn("obituary", died["src"][0])
        self.assertEqual(self.priv["audience"]["I3"], ["I1", "I4"])

    def test_public_people_keep_notes_citations_and_evidence(self):
        lud = self.pub["people"]["I11"]
        self.assertEqual(lud["rc"], 0)
        self.assertEqual(lud["notes"], ["A research note about Ludwig."])
        res = [e for e in lud["ev"] if e["t"] == "Lived"][0]
        self.assertTrue(res["ev"].startswith("Evidence: direct, original"))
        self.assertIn("NARA T623", res["src"][0])
        self.assertEqual(res["age"], 20)

    def test_images_follow_rights_and_privacy(self):
        files = {im["file"]: im for im in self.pub["images"].values()}
        self.assertIn("1900-06-census-Springfield-Ludwig-Brandt.jpg", files)
        self.assertNotIn("1940-04-census-Springfield-Ludwig-Brandt.jpg", files)  # 1940 census: private
        self.assertNotIn("1990-headstone-Springfield-Ludwig-Brandt.jpg", files)  # "never" wins over "show"
        pfiles = {im["file"] for im in self.priv["images"].values()}
        self.assertIn("1940-04-census-Springfield-Ludwig-Brandt.jpg", pfiles)
        self.assertNotIn("1990-headstone-Springfield-Ludwig-Brandt.jpg", pfiles)
        im = files["1900-06-census-Springfield-Ludwig-Brandt.jpg"]
        self.assertTrue(im["src"].startswith("img/"))
        self.assertEqual(im["people"], ["I11"])
        self.assertEqual(im["group"], "Censuses")
        self.assertTrue(os.path.exists(os.path.join(self.p, "site", im["src"])))
        self.assertTrue(os.path.exists(os.path.join(self.p, "site", im["th"])))
        self.assertLessEqual(max(im["w"], im["h"]), 2000)
        priv_im = [i for i in self.priv["images"].values() if i["file"].startswith("1940")][0]
        self.assertTrue(priv_im["src"].startswith("pimg/"))
        self.assertTrue(os.path.exists(os.path.join(self.p, "private", "img", os.path.basename(priv_im["src"]))))
        self.assertFalse(os.path.exists(os.path.join(self.p, "site", "img", os.path.basename(priv_im["src"]))))

    def test_recent_persons_images_are_private(self):
        # Ernst (I5) is recent; his register image is not in 'show' anyway, so no image anywhere.
        self.assertEqual(self.pub["people"]["I5"]["img"], [])

    def test_site_settings_root_and_name(self):
        self.assertEqual(self.pub["root"], "I1")
        self.assertEqual(self.pub["site"]["name"], "The Adler & Brandt Family")
        self.assertEqual(self.pub["site"]["owner_name"], "Alex")
        self.assertEqual(self.pub["site"]["root_name"], "Alex")
        self.assertEqual(set(self.pub["site"]), {"name", "owner_name", "root_name", "story_footer", "history_footer",
                                                  "origins_note", "request_example", "regions", "countries"})

    def test_page_gets_the_name_and_version_stamps(self):
        with open(os.path.join(self.p, "site", "index.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("<title>The Adler &amp; Brandt Family</title>", page)
        self.assertNotIn("{{SITE_NAME}}", page)
        self.assertRegex(page, r'"app\.js\?v=[0-9a-f]{10}"')
        self.assertRegex(page, r'"style\.css\?v=[0-9a-f]{10}"')
        for f in ("app.js", "style.css"):
            self.assertTrue(os.path.exists(os.path.join(self.p, "site", f)))

    def test_lines_start_from_the_root(self):
        lines = {l["sn"]: l for l in self.pub["lines"]}
        self.assertEqual(lines["Adler"]["founder"], "I9")
        self.assertEqual(lines["Adler"]["chain"], ["I9", "I5"])  # living people left out of the chain
        self.assertEqual(lines["Brandt"]["founder"], "I11")

    def test_regions_and_intro(self):
        self.assertIn("de", self.pub["people"]["I5"]["regions"])
        intro = self.pub["people"]["I11"]["intro"]
        self.assertTrue(intro.startswith("Brickmaker. Born in 1880"), intro)
        self.assertIn("father of one child in the tree", intro)
        self.assertIn("died in 1990, aged about 110", intro)

    def test_intro_hedges_weak_evidence(self):
        frieda = self.pub["people"]["I6"]["intro"]
        self.assertTrue(frieda.startswith("Reportedly born in 1932"), frieda)

    def test_missing_optional_inputs(self):
        self.assertEqual(self.pub["history"], [])
        self.assertEqual(self.pub["service"], [])
        self.assertEqual(self.pub["stories"], [])
        self.assertEqual(self.pub["places"], {})


@unittest.skipUnless(HAVE_DEPS, "needs the markdown and Pillow packages")
class BuildInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.tmp.name, "p")

    def tearDown(self):
        self.tmp.cleanup()

    PLACES = {"Springfield, Ohio, USA": {"lat": 39.92, "lon": -83.81, "precision": "place"},
              "Musterdorf, Hessen, Germany": {"lat": 50.5, "lon": 9.0, "precision": "region"}}

    def test_places_and_overrides(self):
        site_project(self.p, files={
            "site-src/places.json": self.PLACES,
            "site-src/places-overrides.json": {"_note": "x", "Musterdorf, Hessen, Germany": {"precision": "place", "approx": True,
                                                                                          "label": "Musterdorf"}}})
        r = build(self.p)
        self.assertEqual(r.returncode, 0, r.stderr)
        pub, _ = outputs(self.p)
        self.assertEqual(pub["places"]["Springfield, Ohio, USA"], {"lat": 39.92, "lon": -83.81, "name": "Springfield, Ohio", "approx": False})
        self.assertEqual(pub["places"]["Musterdorf, Hessen, Germany"]["name"], "Musterdorf")
        self.assertTrue(pub["places"]["Musterdorf, Hessen, Germany"]["approx"])
        self.assertNotIn("_note", pub["places"])

    def test_history_events_are_copied(self):
        ev = [{"y": 1914, "regions": ["world"], "title": "War", "text": "x", "sources": []}]
        site_project(self.p, files={"site-src/history-events.json": {"_note": "x", "events": ev}})
        self.assertEqual(build(self.p).returncode, 0)
        self.assertEqual(outputs(self.p)[0]["history"], ev)

    def test_story_chapters(self):
        story = """---
title: The Brickmaker
subtitle: Ludwig Brandt in Springfield
line: Brandt
people: I11
---

<!-- Draft history: kept as a note. TODO: find the 1910 census. Source: the 1900 census. -->

{{step place="Springfield, Ohio, USA" year=1900 person=I11 zoom=10}}

Ludwig made **bricks**.

<!-- The 1900 census, sheet 4. TODO: check the street. -->

{{step place="Musterdorf, Hessen, Germany" year=1880}}

Not on the map.

{{note}}

A closing note.
"""
        site_project(self.p, files={"stories/01-the-brickmaker.md": story, "site-src/places.json": self.PLACES})
        r = build(self.p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("place not on the map: Musterdorf, Hessen, Germany", r.stdout)
        st = outputs(self.p)[0]["stories"][0]
        self.assertEqual((st["slug"], st["title"], st["line"], st["people"]), ("the-brickmaker", "The Brickmaker", "Brandt", ["I11"]))
        self.assertEqual([s["kind"] for s in st["steps"]], ["step", "step", "note"])
        first = st["steps"][0]
        self.assertIn("<strong>bricks</strong>", first["html"])
        self.assertEqual((first["lat"], first["lon"], first["person"], first["year"], first["zoom"]), (39.92, -83.81, "I11", 1900.0, 10))
        self.assertIn("The 1900 census, sheet 4.", first["src"])
        self.assertNotIn("TODO", first["src"])
        self.assertNotIn("<!--", first["html"])
        self.assertNotIn("lat", st["steps"][1])

    def test_story_naming_a_living_person_stops_the_build(self):
        story = "---\ntitle: T\n---\n\n{{step place=\"Springfield, Ohio, USA\" year=1990 person=I1}}\n\nText.\n"
        site_project(self.p, files={"stories/01-t.md": story, "site-src/places.json": self.PLACES})
        r = build(self.p)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("I1 is living", r.stderr)

    def test_story_with_unknown_person_is_reported(self):
        story = "---\ntitle: T\n---\n\n{{step place=\"Springfield, Ohio, USA\" year=1990 person=I99}}\n\nText.\n"
        site_project(self.p, files={"stories/01-t.md": story, "site-src/places.json": self.PLACES})
        r = build(self.p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no such person I99", r.stdout)

    def test_service_records(self):
        svc = {"service": [{"person": "I11", "war": "x", "stops": []}, {"person": "I99", "war": "y", "stops": []}]}
        site_project(self.p, files={"site-src/service.json": svc})
        r = build(self.p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("service.json: no such person I99", r.stdout)
        self.assertEqual([s["person"] for s in outputs(self.p)[0]["service"]], ["I11"])

    def test_service_record_of_a_living_person_stops_the_build(self):
        site_project(self.p, files={"site-src/service.json": {"service": [{"person": "I2", "stops": []}]}})
        r = build(self.p)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("I2 is living", r.stderr)

    def test_biography_sections_become_stories_for_public_people_only(self):
        bio = """# Biographies

## The Brandt line

### Ludwig Brandt (1880 to 1990)

Ludwig made bricks. See [the census](sources/1900-06-census-Springfield-Ludwig-Brandt.md).

### Clara Brandt (1962 to 2020)

Clara's story names living relatives.

### Nobody Known (1700)

Unmatched.
"""
        site_project(self.p, files={"biographies.md": bio})
        r = build(self.p, "--report")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 headings unmatched", r.stdout)
        self.assertIn("Nobody Known (1700)", r.stdout)
        pub, priv = outputs(self.p)
        story = pub["people"]["I11"]["story"][0]
        self.assertEqual(story["head"], "Ludwig Brandt (1880 to 1990)")
        self.assertIn("the census", story["html"])
        self.assertNotIn("sources/", story["html"])  # local links become plain text
        self.assertEqual(pub["people"]["I3"]["story"], [])  # recent: private
        self.assertIn("living relatives", priv["people"]["I3"]["story"][0]["html"])

    def test_root_missing_is_an_error(self):
        site_project(self.p)
        with open(os.path.join(self.p, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"root": "@I999@"}, f)
        r = build(self.p)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("root person @I999@ is missing", r.stderr)

    def test_other_root_changes_the_lines(self):
        site_project(self.p)
        with open(os.path.join(self.p, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"root": "@I3@", "living_cutoff": 1926}, f)
        self.assertEqual(build(self.p).returncode, 0)
        pub, _ = outputs(self.p)
        self.assertEqual(pub["root"], "I3")
        self.assertEqual([l["sn"] for l in pub["lines"]], ["Brandt"])

    def test_living_cutoff_from_the_project(self):
        site_project(self.p)
        with open(os.path.join(self.p, "project.json"), "w", encoding="utf-8") as f:
            json.dump({"root": "@I1@", "living_cutoff": 1950}, f)
        self.assertEqual(build(self.p).returncode, 0)
        pub, priv = outputs(self.p)
        self.assertEqual(sorted(priv["living"]), ["I1", "I2", "I4"])  # born 1960 and later
        self.assertNotIn("lv", pub["people"]["I8"])

    def test_rebuild_reuses_images(self):
        site_project(self.p)
        self.assertEqual(build(self.p).returncode, 0)
        imgs = os.path.join(self.p, "site", "img")
        before = {f: os.path.getmtime(os.path.join(imgs, f)) for f in os.listdir(imgs)}
        self.assertEqual(build(self.p).returncode, 0)
        after = {f: os.path.getmtime(os.path.join(imgs, f)) for f in os.listdir(imgs)}
        self.assertEqual(before, after)

    def test_without_rights_file_no_images_are_shown(self):
        site_project(self.p, rights=None)
        self.assertEqual(build(self.p).returncode, 0)
        pub, priv = outputs(self.p)
        self.assertEqual(pub["images"], {})
        self.assertEqual(priv["images"], {})


class FrontEndTests(unittest.TestCase):
    """The front end has no build step, so check what the builder relies on."""

    def read(self, name):
        with open(os.path.join(support.FRAMEWORK, "site-src", name), encoding="utf-8") as f:
            return f.read()

    def test_page_has_the_name_placeholder_and_the_assets(self):
        page = self.read("index.html")
        self.assertEqual(page.count("{{SITE_NAME}}"), 2)
        self.assertIn('"app.js"', page)
        self.assertIn('"style.css"', page)

    def test_palette_has_six_slots_in_light_and_dark(self):
        css = self.read("style.css")
        for n in range(1, 7):
            self.assertEqual(css.count(f"--c-{n}:"), 3, n)
        self.assertEqual(css.count("--c-un:"), 3)
        self.assertEqual(css.count("--map-europe:"), 3)

    @unittest.skipUnless(shutil.which("node"), "needs Node.js")
    def test_app_js_parses(self):
        import subprocess
        r = subprocess.run(["node", "--check", os.path.join(support.FRAMEWORK, "site-src", "app.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_app(self, script):
        """Run site-src/app.js in Node with small stand-ins for the browser, then script; return its JSON result."""
        import subprocess
        runner = (
            "const vm = require('vm'), fs = require('fs');"
            "const ctx = {console, document: {addEventListener() {}}, addEventListener() {},"
            " getComputedStyle: () => ({getPropertyValue: () => ''})};"
            "vm.createContext(ctx);"
            "vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);"
            "process.stdout.write(vm.runInContext(process.argv[2], ctx));")
        r = subprocess.run(["node", "-e", runner, os.path.join(support.FRAMEWORK, "site-src", "app.js"), script],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @unittest.skipUnless(shutil.which("node"), "needs Node.js")
    def test_timeline_bands_estimates_and_living(self):
        # c's father f and grandfather ff form one band (ff has no birth year, so it is worked out from f's);
        # c's mother m sits in her own father's band; a living stub (no name) is left out.
        out = self.run_app("""
          P = {c: {n: "Carl Berg", sn: "Berg", sx: "M", by: 1900, famc: ["F1"], fams: []},
               f: {n: "Fritz Berg", sn: "Berg", sx: "M", by: 1870, dy: 1940, famc: ["F2"], fams: ["F1"]},
               m: {n: "Mia Lang", sn: "Lang", sx: "F", famc: ["F3"], fams: ["F1"]},
               ff: {n: "Franz Berg", sn: "Berg", sx: "M", dy: 1880, famc: [], fams: ["F2"]},
               mf: {n: "Max Lang", sn: "Lang", sx: "M", by: 1835, famc: [], fams: ["F3"]},
               mm: {lv: 1, sx: "F", famc: [], fams: ["F3"]}};
          F = {F1: {h: "f", w: "m", c: ["c"]}, F2: {h: "ff", w: null, c: ["f"]}, F3: {h: "mf", w: "mm", c: ["m"]}};
          H = [];
          const d = livesData("c");
          JSON.stringify({bands: d.bands.map(b => [b.sn, b.ks, b.first]), ff: d.yr.ff, m: d.yr.m, mm: "mm" in d.yr, y0: d.y0})""")
        self.assertEqual(out["bands"], [["Berg", ["ff", "f", "c"], "ff"], ["Lang", ["mf", "m"], "mf"]])
        self.assertEqual(out["ff"]["b"], 1840)
        self.assertEqual(out["ff"]["d"], 1880)
        self.assertTrue(out["ff"]["est"])
        self.assertEqual(out["m"]["b"], 1870)
        self.assertFalse(out["mm"])
        self.assertEqual(out["y0"], 1830)

    @unittest.skipUnless(shutil.which("node"), "needs Node.js")
    def test_fan_labels_shorten_to_fit(self):
        out = self.run_app("""JSON.stringify([
          fanLabel("Anna Berg", "Anna", "Berg", 200, 60, 22, true),
          fanLabel("Anna Maria Berg", "Anna", "Berg", 60, 20, 9.5, false),
          fanLabel("Anna Berg", "Anna", "Berg", 3, 3, 9.5, false),
          fanLabel("Anna Berg", "Anna", "Berg", 0.3, 0.2, 9.5, false)])""")
        self.assertEqual(out[0]["lines"], ["Anna", "Berg"])
        self.assertEqual(out[1]["lines"], ["A. Berg"])
        self.assertEqual(out[2]["lines"], ["AB"])
        self.assertIsNone(out[3])


@unittest.skipUnless(HAVE_DEPS, "needs the markdown and Pillow packages")
class DeepPedigreeTests(unittest.TestCase):
    """tests/fixtures/deep-pedigree.ged, the invented 27-generation tree for the fan chart and the timeline."""

    FIXTURES = os.path.join(support.FRAMEWORK, "tests", "fixtures")

    def test_fixture_matches_its_generator(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_deep", os.path.join(self.FIXTURES, "make_deep_pedigree.py"))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch("builtins.print"):
            gen.OUT = os.path.join(tmp, "deep.ged")
            gen.main()
            with open(gen.OUT, encoding="utf-8") as f:
                fresh = f.read()
        with open(os.path.join(self.FIXTURES, "deep-pedigree.ged"), encoding="utf-8") as f:
            self.assertEqual(f.read(), fresh, "run python3 tests/fixtures/make_deep_pedigree.py")

    def test_build_keeps_every_generation_and_hides_the_living(self):
        with open(os.path.join(self.FIXTURES, "deep-pedigree.ged"), encoding="utf-8") as f:
            tree = f.read()
        with tempfile.TemporaryDirectory() as tmp:
            site_project(tmp, tree=tree, rights=None)
            r = build(tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            pub, _ = outputs(tmp)
        people, fams = pub["people"], pub["fams"]
        parents = lambda k: [x for f in people[k]["famc"] for x in (fams[f]["h"], fams[f]["w"]) if x]
        depth, paths, queue = {pub["root"]: 0}, {pub["root"]: 1}, [pub["root"]]
        while queue:   # breadth first: each person's generation, and the number of lines that reach them
            k = queue.pop(0)
            for p in parents(k):
                if p not in depth:
                    depth[p] = depth[k] + 1
                    queue.append(p)
        order = sorted(depth, key=depth.get)
        for k in order:
            for p in parents(k):
                paths[p] = paths.get(p, 0) + paths[k]
        self.assertEqual(max(depth.values()), 27)
        self.assertTrue(any(n > 1 for n in paths.values()), "the pedigree collapse is missing")
        living = [k for k, p in people.items() if p.get("lv")]
        self.assertEqual(len(living), 3)
        for k in living:
            self.assertNotIn("n", people[k])


if __name__ == "__main__":
    unittest.main()
