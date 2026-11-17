"""Tests for family-app/app.py, the sign-in app of the family website.

Each test imports a fresh copy of the module from its path (the folder name has a
hyphen) with the environment pointing at temporary folders, because the settings
are read at import time. Mail is captured by patching send_email, so nothing is
sent. The whole file skips when fastapi or httpx is missing. The people are
fictional (I1 to I14).
"""
import contextlib
import importlib.util
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import time
import unittest
import warnings
from unittest import mock

import support

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    try:
        import python_multipart  # noqa: F401
    except ImportError:
        import multipart  # noqa: F401
    from fastapi.testclient import TestClient
    HAVE_APP_DEPS = True
except ImportError:
    HAVE_APP_DEPS = False

APP_PATH = os.path.join(support.FRAMEWORK, "family-app", "app.py")
BASE = "https://fam.example.org"
ADMIN = "wanda@example.org"
SETTINGS = ("BASE_URL", "ADMIN_EMAIL", "DATA_DIR", "PRIVATE_DIR", "PUBLIC_JSON", "EMAIL_BACKEND", "SITE_NAME",
            "OWNER_NAME", "ADMIN_PERSON", "SITE_DIR", "REPLY_TO", "MAIL_FROM")

# Hugo (I1) and Ida (I2) have Karl (I3) and Lena (I4). Karl and Mia (I5) have Nora (I6) and Otto (I7); Nora has Sam (I10).
# Lena has Paul (I8), whose spouse is Rita (I9). Theo (I11) is Hugo's father and Uma (I12) his daughter. Vera (I13) is unrelated.
NAMES = {"I3": "Karl Adler", "I4": "Lena Clarke", "I5": "Mia Adler", "I6": "Nora Adler", "I7": "Otto Adler", "I8": "Paul Clarke",
         "I9": "Rita Dunn", "I10": "Sam Adler", "I12": "Uma Adler", "I13": "Vera Eaton"}
LIVING = sorted(NAMES)
GRAPH = {
    "I1": {"p": ["I11"], "c": ["I3", "I4"], "s": ["I2"]},
    "I2": {"p": [], "c": ["I3", "I4"], "s": ["I1"]},
    "I3": {"p": ["I1", "I2"], "c": ["I6", "I7"], "s": ["I5"]},
    "I4": {"p": ["I1", "I2"], "c": ["I8"], "s": []},
    "I5": {"p": [], "c": ["I6", "I7"], "s": ["I3"]},
    "I6": {"p": ["I3", "I5"], "c": ["I10"], "s": []},
    "I7": {"p": ["I3", "I5"], "c": [], "s": []},
    "I8": {"p": ["I4"], "c": [], "s": ["I9"]},
    "I9": {"p": [], "c": [], "s": ["I8"]},
    "I10": {"p": ["I6"], "c": [], "s": []},
    "I11": {"p": [], "c": ["I1", "I12"], "s": []},
    "I12": {"p": ["I11"], "c": [], "s": []},
    "I13": {"p": [], "c": [], "s": []},
}
FAMS = {"I3": ["F1"], "I5": ["F1"], "I8": ["F2"], "I9": ["F2"], "I12": ["F3"], "I13": ["F4"]}


def make_private():
    people = {k: {"n": NAMES[k], "fams": FAMS.get(k, [])} for k in LIVING}
    people["I6"]["img"] = ["pic6"]
    people["I13"]["img"] = ["pic13"]
    people["I2"] = {"n": "Ida Adler"}
    people["I11"] = {"n": "Theo Adler"}
    return {"people": people,
            "fams": {"F1": {"m": ["I3", "I5"]}, "F2": {"m": ["I8", "I9"]}, "F3": {"m": ["I12"]}, "F4": {"m": ["I13"]}},
            "images": {"pic6": {"c": "Nora"}, "pic13": {"c": "Vera"}},
            "graph": GRAPH, "living": LIVING, "audience": {"I2": ["I8"], "I11": ["I12"]}, "names": NAMES}


def make_public():
    return {"root": "I1",
            "site": {"name": "The Adler Family", "owner_name": "Wanda Brandt",
                     "request_example": "for example, \"Hugo's granddaughter\""},
            "people": {"I1": {"n": "Hugo Adler", "by": 1900, "dy": 1970}, "I2": {"n": "Ida Adler", "by": 1902, "dy": 1980},
                       "I11": {"n": "Theo Adler", "by": 1870, "dy": 1940}}}


@unittest.skipUnless(HAVE_APP_DEPS, "fastapi, httpx and python-multipart are needed for the family app tests")
class AppCase(unittest.TestCase):
    public = "default"   # "default", a dict, or None for no public.json at all
    env_extra = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.data = os.path.join(t, "data")
        self.priv = os.path.join(t, "private")
        os.makedirs(os.path.join(self.priv, "img"))
        with open(os.path.join(self.priv, "private.json"), "w", encoding="utf-8") as f:
            json.dump(make_private(), f)
        for name in ("pic6.jpg", "pic6_t.jpg", "pic13.jpg"):
            with open(os.path.join(self.priv, "img", name), "wb") as f:
                f.write(b"JPEG " + name.encode())
        with open(os.path.join(t, "secret.txt"), "w") as f:
            f.write("secret")
        self.public_file = os.path.join(t, "public.json")
        if self.public is not None:
            with open(self.public_file, "w", encoding="utf-8") as f:
                json.dump(make_public() if self.public == "default" else self.public, f)
        env = {"BASE_URL": BASE, "ADMIN_EMAIL": ADMIN, "DATA_DIR": self.data, "PRIVATE_DIR": self.priv,
               "PUBLIC_JSON": self.public_file, "EMAIL_BACKEND": "console"}
        env.update(self.env_extra)
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SETTINGS:
            if k not in env:
                os.environ.pop(k, None)
        self.mod = self.load_module()
        self.sent = []
        p = mock.patch.object(self.mod, "send_email", side_effect=lambda to, subject, text: self.sent.append((to, subject, text)))
        p.start()
        self.addCleanup(p.stop)
        self.clients = []
        self.web = self.client()   # runs the startup handler
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)

    @staticmethod
    def load_module():
        spec = importlib.util.spec_from_file_location("family_app_under_test", APP_PATH)
        mod = importlib.util.module_from_spec(spec)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            spec.loader.exec_module(mod)
        for name in ("family", "httpx"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        return mod

    # ---- helpers

    def client(self, email=None, origin=True, headers=None):
        """A client; with email, it holds a live session of that reader."""
        hd = {"origin": BASE} if origin else {}
        if email:
            tok = secrets.token_urlsafe(16)
            now = time.time()
            self.sql("insert into sessions values(?,?,?,?,?)", (self.mod.h(tok), email, now, now + 3600, now))
            hd["cookie"] = f"{self.mod.COOKIE}={tok}"
        hd.update(headers or {})
        c = TestClient(self.mod.app, base_url=BASE, headers=hd)
        self.addCleanup(c.close)
        return c

    def sql(self, query, args=()):
        with contextlib.closing(sqlite3.connect(os.path.join(self.data, "family.db"))) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(query, args).fetchall()
            con.commit()
            return rows

    def add_reader(self, email, person=None, status="approved", name="Some Reader"):
        self.sql("insert into readers(email, name, person, status, created, decided) values(?,?,?,?,0,0)", (email, name, person, status))

    def last_link(self, to):
        for m_to, _s, text in reversed(self.sent):
            if m_to == to:
                return re.search(r"/auth/login\?t=(\S+)", text).group(1)
        self.fail("no sign-in link was sent to " + to)

    def review_token(self):
        for m_to, _s, text in self.sent:
            if m_to == ADMIN:
                m = re.search(r"/review/(\S+)", text)
                if m:
                    return m.group(1)
        self.fail("no review link was sent to the admin")


class Startup(AppCase):
    def test_admin_inserted_with_owner_name_and_root_person(self):
        r = self.sql("select * from readers where email = ?", (ADMIN,))[0]
        self.assertEqual((r["name"], r["person"], r["status"]), ("Wanda Brandt", "I1", "approved"))

    def test_startup_reapproves_a_denied_admin_and_keeps_one_row(self):
        self.sql("update readers set status = 'denied' where email = ?", (ADMIN,))
        with self.client():
            pass
        with TestClient(self.mod.app, base_url=BASE):
            pass
        rows = self.sql("select status from readers where email = ?", (ADMIN,))
        self.assertEqual([r["status"] for r in rows], ["approved"])

    def test_schema_has_all_tables_and_birth_name_column(self):
        names = {r["name"] for r in self.sql("select name from sqlite_master where type = 'table'")}
        self.assertLessEqual({"readers", "requests", "links", "sessions", "hits", "contributions"}, names)
        self.assertIn("birth_name", [r["name"] for r in self.sql("pragma table_info(requests)")])

    def test_old_requests_table_gains_birth_name(self):
        self.sql("drop table requests")
        self.sql("create table requests (id integer primary key, email text not null, name text, relation text, "
                 "message text, created real, status text not null default 'pending', review_hash text)")
        with TestClient(self.mod.app, base_url=BASE):
            pass
        self.assertIn("birth_name", [r["name"] for r in self.sql("pragma table_info(requests)")])

    def test_no_admin_email_means_no_admin_row(self):
        os.environ["ADMIN_EMAIL"] = ""
        mod = self.load_module()
        with tempfile.TemporaryDirectory() as d:
            mod.DATA_DIR = d
            with TestClient(mod.app, base_url=BASE):
                pass
            with contextlib.closing(sqlite3.connect(os.path.join(d, "family.db"))) as con:
                self.assertEqual(con.execute("select count(*) from readers").fetchone()[0], 0)

    def test_helpers(self):
        m = self.mod
        self.assertEqual(m.norm("  Ann@Example.ORG "), "ann@example.org")
        self.assertTrue(m.valid_email("ann@example.org"))
        for bad in ("", "a@b", "ann example@x.org", "<a@b.org>", "a@b.org,c@d.org", "nodomain", "a" * 260 + "@b.org"):
            self.assertFalse(m.valid_email(bad), bad)
        self.assertNotEqual(m.h("a"), m.h("b"))


class AuthCheck(AppCase):
    def test_401_without_session_for_api_paths(self):
        c = self.client(origin=False)
        for uri in ("/data/public.json", "/api/private", "/img/x.jpg", "/pimg/x.jpg"):
            r = c.get("/auth/check", headers={"x-forwarded-uri": uri})
            self.assertEqual(r.status_code, 401, uri)

    def test_page_without_session_redirects_to_signin(self):
        r = self.client(origin=False).get("/auth/check", headers={"x-forwarded-uri": "/index.html"}, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/signin?next=/index.html")

    def test_200_class_with_session(self):
        self.add_reader("amy@example.org")
        r = self.client("amy@example.org").get("/auth/check", headers={"x-forwarded-uri": "/data/public.json"})
        self.assertEqual(r.status_code, 204)

    def test_garbage_cookie_expired_session_and_denied_reader_are_refused(self):
        h = {"x-forwarded-uri": "/data/x"}
        self.assertEqual(self.client(headers={"cookie": "fh_session=nope"}).get("/auth/check", headers=h).status_code, 401)
        self.add_reader("old@example.org")
        c = self.client("old@example.org")
        self.sql("update sessions set expires = ? where email = ?", (time.time() - 5, "old@example.org"))
        self.assertEqual(c.get("/auth/check", headers=h).status_code, 401)
        self.add_reader("bad@example.org", status="denied")
        self.assertEqual(self.client("bad@example.org").get("/auth/check", headers=h).status_code, 401)

    def test_check_updates_last_seen(self):
        self.add_reader("amy@example.org")
        c = self.client("amy@example.org")
        self.sql("update sessions set seen = 1")
        c.get("/auth/check")
        self.assertGreater(self.sql("select seen from sessions")[0]["seen"], 1000)


class SignIn(AppCase):
    def test_form_page_uses_site_name(self):
        r = self.client(origin=False).get("/signin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("The Adler Family", r.text)
        self.assertIn('name="website"', r.text)

    def test_end_to_end(self):
        self.add_reader("amy@example.org", name="Amy")
        c = self.client()
        r = c.post("/signin", data={"email": " Amy@Example.org "})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Check your email", r.text)
        self.assertEqual(len(self.sent), 1)
        to, subject, text = self.sent[0]
        self.assertEqual(to, "amy@example.org")
        self.assertIn("The Adler Family", subject)
        self.assertIn(BASE + "/auth/login?t=", text)
        tok = self.last_link(to)

        # GET shows a button and leaves the link unused (mail scanners open links).
        g = c.get("/auth/login", params={"t": tok})
        self.assertIn("<button", g.text)
        self.assertIsNone(self.sql("select used from links")[0]["used"])
        self.assertNotIn("set-cookie", g.headers)

        # POST uses it, sets the cookie and redirects.
        r = c.post("/auth/login", data={"t": tok}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/")
        cookie = r.headers["set-cookie"]
        self.assertIn("fh_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("samesite=lax", cookie.lower())
        self.assertEqual(c.get("/auth/check").status_code, 204)
        self.assertEqual(c.get("/api/me").json()["email"], "amy@example.org")

        # Only the hash of the link and of the session is stored.
        self.assertNotIn(tok, json.dumps([dict(x) for x in self.sql("select * from links")]))

        # The link works once.
        again = self.client().post("/auth/login", data={"t": tok})
        self.assertIn("expired", again.text)
        self.assertNotIn("set-cookie", again.headers)

    def test_expired_link_fails(self):
        self.add_reader("amy@example.org")
        url = self.mod.new_link("amy@example.org", -1)
        tok = url.split("t=")[1]
        r = self.client().post("/auth/login", data={"t": tok})
        self.assertIn("expired", r.text)
        self.assertNotIn("set-cookie", r.headers)
        self.assertEqual(self.sql("select count(*) n from sessions")[0]["n"], 0)

    def test_unknown_token_fails(self):
        self.assertIn("expired", self.client().post("/auth/login", data={"t": "nonsense"}).text)
        self.assertIn("expired", self.client().post("/auth/login", data={}).text)

    def test_unknown_address_gets_request_form(self):
        r = self.client().post("/signin", data={"email": "new@example.org"})
        self.assertIn("Ask to join", r.text)
        self.assertIn("new@example.org", r.text)
        self.assertEqual(self.sent, [])

    def test_denied_address_gets_request_form_and_no_link(self):
        self.add_reader("no@example.org", status="denied")
        r = self.client().post("/signin", data={"email": "no@example.org"})
        self.assertIn("Ask to join", r.text)
        self.assertEqual(self.sent, [])

    def test_invalid_email_shows_form_again(self):
        r = self.client().post("/signin", data={"email": "not an address"})
        self.assertIn("Send me a link", r.text)
        self.assertEqual(self.sent, [])

    def test_honeypot_sends_nothing_even_for_approved_address(self):
        self.add_reader("amy@example.org")
        r = self.client().post("/signin", data={"email": "amy@example.org", "website": "http://spam.example"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.sql("select count(*) n from links")[0]["n"], 0)

    def test_per_address_limit_is_five_an_hour(self):
        self.add_reader("amy@example.org")
        c = self.client()
        for i in range(5):
            self.assertIn("Check your email", c.post("/signin", data={"email": "amy@example.org"}).text, i)
        self.assertIn("Please wait", c.post("/signin", data={"email": "amy@example.org"}).text)
        self.assertEqual(len(self.sent), 5)

    def test_per_ip_limit_is_twenty_an_hour(self):
        c = self.client(headers={"x-forwarded-for": "203.0.113.9"})
        for i in range(20):
            self.assertNotIn("Please wait", c.post("/signin", data={"email": f"u{i}@example.org"}).text, i)
        self.assertIn("Please wait", c.post("/signin", data={"email": "u99@example.org"}).text)
        other = self.client(headers={"x-forwarded-for": "203.0.113.10"})
        self.assertNotIn("Please wait", other.post("/signin", data={"email": "u98@example.org"}).text)

    def test_limited_forgets_old_hits(self):
        m = self.mod
        self.assertFalse(m.limited("k", 1, 60))
        self.assertTrue(m.limited("k", 1, 60))
        self.sql("update hits set at = ?", (time.time() - 120,))
        self.assertFalse(m.limited("k", 1, 60))

    def test_bad_origin_refused(self):
        self.add_reader("amy@example.org")
        evil = self.client(origin=False, headers={"origin": "https://evil.example"})
        none = self.client(origin=False)
        referer_ok = self.client(origin=False, headers={"referer": BASE + "/signin"})
        for c in (evil, none):
            self.assertEqual(c.post("/signin", data={"email": "amy@example.org"}).status_code, 403)
            self.assertEqual(c.post("/request", data={"email": "n@example.org", "name": "N N"}).status_code, 403)
            self.assertEqual(c.post("/auth/login", data={"t": "x"}).status_code, 403)
        self.assertEqual(self.sent, [])
        self.assertEqual(referer_ok.post("/signin", data={"email": "amy@example.org"}).status_code, 200)
        self.assertEqual(len(self.sent), 1)

    def test_origin_must_match_host_not_prefix(self):
        c = self.client(origin=False, headers={"origin": BASE + ".evil.example"})
        self.assertEqual(c.post("/signin", data={"email": "amy@example.org"}).status_code, 403)

    def test_signout_removes_session_and_cookie(self):
        self.add_reader("amy@example.org")
        c = self.client("amy@example.org")
        self.assertEqual(c.get("/auth/check").status_code, 204)
        r = c.get("/auth/signout")
        self.assertIn("signed out", r.text)
        self.assertEqual(self.sql("select count(*) n from sessions")[0]["n"], 0)
        self.assertEqual(self.client(headers={"cookie": c.headers["cookie"]}).get("/auth/check", headers={"x-forwarded-uri": "/api/x"}).status_code, 401)

    def test_signout_without_cookie_is_fine(self):
        self.assertEqual(self.client(origin=False).get("/auth/signout").status_code, 200)

    def test_http_base_url_gives_non_secure_cookie(self):
        os.environ["BASE_URL"] = "http://localhost:8000/"
        mod = self.load_module()
        self.assertFalse(mod.SECURE)
        self.assertEqual(mod.BASE_URL, "http://localhost:8000")


class AccessRequests(AppCase):
    def post_request(self, c=None, **kw):
        data = {"email": "newbie@example.org", "name": "Nora Smith", "relation": "Karl's daughter", "birth_name": "Adler"}
        data.update(kw)
        return (c or self.client()).post("/request", data=data)

    def test_request_emails_admin_once_with_review_link(self):
        r = self.post_request()
        self.assertIn("Thank you", r.text)
        self.assertIn("Wanda Brandt", r.text)
        self.assertEqual([m[0] for m in self.sent], [ADMIN])
        text = self.sent[0][2]
        self.assertIn("Nora Smith <newbie@example.org>", text)
        self.assertIn("Karl's daughter", text)
        self.assertIn("probably Nora Adler [I6]", text)
        self.assertIn(BASE + "/review/", text)
        self.assertEqual(self.sql("select count(*) n from requests")[0]["n"], 1)
        self.post_request()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sql("select count(*) n from requests")[0]["n"], 2)

    def test_request_form_text_comes_from_public_json(self):
        r = self.client().post("/signin", data={"email": "newbie@example.org"})
        self.assertIn("Tell Wanda Brandt who you are", r.text)
        self.assertIn("Hugo&#x27;s granddaughter", r.text)

    def test_denied_address_does_not_email_admin_again(self):
        self.post_request()
        tok = self.review_token()
        self.assertEqual(self.client().post(f"/review/{tok}", data={"decision": "denied"}).status_code, 200)
        self.sent.clear()
        r = self.post_request()
        self.assertIn("Thank you", r.text)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.sql("select status from requests order by id desc")[0]["status"], "ignored")

    def test_approved_address_gets_a_sign_in_link_instead(self):
        self.add_reader("newbie@example.org")
        self.post_request()
        self.assertEqual([m[0] for m in self.sent], ["newbie@example.org"])
        self.assertIn("already have access", self.sent[0][2])
        self.assertEqual(self.sql("select status from requests")[0]["status"], "duplicate")

    def test_honeypot_stores_nothing(self):
        r = self.post_request(website="x")
        self.assertIn("Thank you", r.text)
        self.assertEqual(self.sql("select count(*) n from requests")[0]["n"], 0)
        self.assertEqual(self.sent, [])

    def test_missing_name_or_bad_email_shows_form(self):
        self.assertIn("Send my request", self.post_request(name="  ").text)
        self.assertIn("Send my request", self.post_request(email="bad").text)
        self.assertEqual(self.sql("select count(*) n from requests")[0]["n"], 0)

    def test_request_limit_is_three_a_day_per_address(self):
        for _ in range(5):
            self.assertIn("Thank you", self.post_request().text)
        self.assertEqual(self.sql("select count(*) n from requests")[0]["n"], 3)

    def test_long_fields_are_cut(self):
        self.post_request(name="N" * 500, relation="r" * 900, message="m" * 5000)
        q = self.sql("select * from requests")[0]
        self.assertEqual((len(q["name"]), len(q["relation"]), len(q["message"])), (120, 300, 1000))

    def test_html_is_escaped_in_request_form(self):
        r = self.post_request(name="<script>alert(1)</script>", email="bad")
        self.assertNotIn("<script>alert(1)</script>", r.text)

    def test_no_admin_email_sends_no_mail(self):
        self.mod.ADMIN_EMAIL = ""
        self.post_request()
        self.assertEqual(self.sent, [])


class Review(AppCase):
    def setUp(self):
        super().setUp()
        self.client().post("/request", data={"email": "newbie@example.org", "name": "Nora Smith", "birth_name": "Adler"})
        self.tok = self.review_token()
        self.sent.clear()

    def test_review_page_preselects_single_guess(self):
        r = self.client(origin=False).get(f"/review/{self.tok}")
        self.assertIn("Nora Smith asks to join", r.text)
        self.assertIn("<option value='I6' selected>", r.text)
        self.assertIn("Chosen from the name: Nora Adler [I6]", r.text)
        self.assertIn("Hugo Adler (1900", r.text)   # deceased people from public.json can be chosen too

    def test_unknown_review_token(self):
        self.assertIn("not found", self.client(origin=False).get("/review/zzz").text)

    def test_approve_links_person_and_emails_welcome(self):
        r = self.client().post(f"/review/{self.tok}", data={"decision": "approved", "person": "I6"})
        self.assertIn("Approved", r.text)
        row = self.sql("select * from readers where email = 'newbie@example.org'")[0]
        self.assertEqual((row["status"], row["person"], row["name"]), ("approved", "I6", "Nora Smith"))
        self.assertEqual(self.sql("select status from requests")[0]["status"], "approved")
        self.assertEqual([m[0] for m in self.sent], ["newbie@example.org"])
        subject, text = self.sent[0][1], self.sent[0][2]
        self.assertIn("The Adler Family", subject)
        self.assertIn("Wanda Brandt has added you", text)
        # The welcome link signs in once.
        c = self.client()
        r = c.post("/auth/login", data={"t": self.last_link("newbie@example.org")}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(c.get("/api/me").json()["anchor"], "I6")

    def test_welcome_link_lasts_seven_days(self):
        self.client().post(f"/review/{self.tok}", data={"decision": "approved"})
        exp = self.sql("select expires from links")[0]["expires"]
        self.assertAlmostEqual(exp - time.time(), 7 * 86400, delta=60)

    def test_approve_without_person_leaves_reader_unlinked(self):
        self.client().post(f"/review/{self.tok}", data={"decision": "approved", "person": ""})
        self.assertIsNone(self.sql("select person from readers where email = 'newbie@example.org'")[0]["person"])

    def test_deny_sends_nothing_and_blocks_sign_in(self):
        r = self.client().post(f"/review/{self.tok}", data={"decision": "denied"})
        self.assertIn("Denied", r.text)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.sql("select status from readers where email = 'newbie@example.org'")[0]["status"], "denied")
        self.assertIn("Ask to join", self.client().post("/signin", data={"email": "newbie@example.org"}).text)
        self.assertEqual(self.sent, [])

    def test_second_decision_shows_already_decided(self):
        self.client().post(f"/review/{self.tok}", data={"decision": "denied"})
        r = self.client().post(f"/review/{self.tok}", data={"decision": "approved", "person": "I6"})
        self.assertIn("Already decided", r.text)
        self.assertEqual(self.sql("select status from readers where email = 'newbie@example.org'")[0]["status"], "denied")
        self.assertIn("Already decided", self.client(origin=False).get(f"/review/{self.tok}").text)

    def test_bad_decision_or_origin_refused(self):
        self.assertEqual(self.client().post(f"/review/{self.tok}", data={"decision": "maybe"}).status_code, 400)
        evil = self.client(origin=False, headers={"origin": "https://evil.example"})
        self.assertEqual(evil.post(f"/review/{self.tok}", data={"decision": "approved"}).status_code, 400)
        self.assertEqual(self.sql("select count(*) n from readers where email = 'newbie@example.org'")[0]["n"], 0)

    def test_guess_people(self):
        m = self.mod
        self.assertEqual(m.guess_people("Nora Adler"), ["I6"])
        self.assertEqual(m.guess_people("Nora Smith", "Adler"), ["I6"])
        self.assertEqual(m.guess_people("Nóra Adler"), ["I6"])
        self.assertEqual(m.guess_people("Nora"), [])
        self.assertEqual(m.guess_people("Zed Adler"), [])
        self.assertEqual(m.single_guess("Nora Adler"), "I6")
        self.assertIn("no living person", m.guess_text("Zed Adler"))
        self.assertIn("Choose them by hand", m.guess_note("Zed Adler"))


class Admin(AppCase):
    def setUp(self):
        super().setUp()
        self.add_reader("amy@example.org", "I6")

    def test_only_admin_opens_admin_page(self):
        for c in (self.client(origin=False), self.client("amy@example.org", origin=False)):
            r = c.get("/admin", follow_redirects=False)
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r.headers["location"], "/signin?next=/admin")
        r = self.client(ADMIN, origin=False).get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Family site admin", r.text)
        self.assertIn("amy@example.org", r.text)
        self.assertIn("10 living people", r.text)

    def test_decide_requires_admin_and_origin(self):
        data = {"email": "x@example.org", "decision": "approved"}
        self.assertEqual(self.client().post("/admin/decide", data=data).status_code, 403)
        self.assertEqual(self.client("amy@example.org").post("/admin/decide", data=data).status_code, 403)
        self.assertEqual(self.client(ADMIN, origin=False).post("/admin/decide", data=data).status_code, 403)
        evil = self.client(ADMIN, origin=False, headers={"origin": "https://evil.example"})
        self.assertEqual(evil.post("/admin/decide", data=data).status_code, 403)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.sql("select count(*) n from readers where email = 'x@example.org'")[0]["n"], 0)

    def test_admin_adds_reader_and_welcome_mail_goes_out(self):
        r = self.client(ADMIN).post("/admin/decide", data={"email": " X@Example.org ", "name": " Xena ", "decision": "approved", "person": "I8"},
                                    follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        row = self.sql("select * from readers where email = 'x@example.org'")[0]
        self.assertEqual((row["name"], row["person"], row["status"]), ("Xena", "I8", "approved"))
        self.assertEqual([m[0] for m in self.sent], ["x@example.org"])

    def test_admin_removes_reader_and_ends_sessions(self):
        c = self.client("amy@example.org")
        self.assertEqual(c.get("/auth/check").status_code, 204)
        self.client(ADMIN).post("/admin/decide", data={"email": "amy@example.org", "decision": "denied"})
        self.assertEqual(c.get("/auth/check", headers={"x-forwarded-uri": "/api/x"}).status_code, 401)
        self.assertEqual(self.sql("select status from readers where email = 'amy@example.org'")[0]["status"], "denied")
        self.assertEqual(self.sql("select count(*) n from sessions where email = 'amy@example.org'")[0]["n"], 0)
        self.assertEqual(self.sent, [])

    def test_decide_refuses_bad_input_and_the_admin_address(self):
        c = self.client(ADMIN)
        for data in ({"email": "x@example.org", "decision": "maybe"}, {"email": "bad", "decision": "approved"},
                     {"email": ADMIN, "decision": "denied"}):
            self.assertEqual(c.post("/admin/decide", data=data).status_code, 400, data)
        self.assertEqual(self.sql("select status from readers where email = ?", (ADMIN,))[0]["status"], "approved")

    def test_pending_requests_listed(self):
        self.client().post("/request", data={"email": "newbie@example.org", "name": "Zed Zimmer"})
        r = self.client(ADMIN, origin=False).get("/admin")
        self.assertIn("Zed Zimmer", r.text)
        self.assertIn("Waiting for you (1)", r.text)

    def contribute(self, **kw):
        files = {"photo": ("a.png", b"\x89PNG data", "image/png")}
        return self.client("amy@example.org").post("/api/contribute", data={"message": "Hello", "person": "I3"}, files=files, **kw)

    def test_contribution_review_and_upload_are_admin_only(self):
        self.assertEqual(self.contribute().json(), {"ok": True})
        self.assertEqual(self.client("amy@example.org", origin=False).get("/admin/upload/1").status_code, 403)
        self.assertEqual(self.client(origin=False).get("/admin/upload/1").status_code, 403)
        admin = self.client(ADMIN, origin=False)
        up = admin.get("/admin/upload/1")
        self.assertEqual((up.status_code, up.content), (200, b"\x89PNG data"))
        self.assertEqual(up.headers["content-type"], "image/png")
        self.assertEqual(admin.get("/admin/upload/99").status_code, 404)
        self.assertIn("Contributions (1 new)", admin.get("/admin").text)
        self.assertEqual(self.client("amy@example.org").post("/admin/contrib/1").status_code, 403)
        self.assertEqual(self.client(ADMIN, origin=False).post("/admin/contrib/1").status_code, 403)
        self.assertEqual(self.client(ADMIN).post("/admin/contrib/1", follow_redirects=False).status_code, 303)
        self.assertEqual(self.sql("select status from contributions")[0]["status"], "entered")
        self.client(ADMIN).post("/admin/contrib/1")
        self.assertEqual(self.sql("select status from contributions")[0]["status"], "new")

    def test_upload_of_contribution_without_file_is_404(self):
        self.client("amy@example.org").post("/api/contribute", data={"message": "Hi", "person": "I3"})
        self.assertEqual(self.client(ADMIN, origin=False).get("/admin/upload/1").status_code, 404)


class GrandparentsRule(AppCase):
    def vis(self, person, email="r@example.org"):
        return self.mod.visible_living({"email": email, "person": person})

    def test_reader_in_the_middle(self):
        # Ancestors and descendants, descendants of parents and grandparents, spouses of those; not Uma (a great-uncle's line) or Vera.
        self.assertEqual(self.vis("I6"), {"I3", "I4", "I5", "I6", "I7", "I8", "I9", "I10"})

    def test_cousin_sees_the_same_family(self):
        self.assertEqual(self.vis("I8"), {"I3", "I4", "I5", "I6", "I7", "I8", "I9", "I10"})

    def test_grandchild_does_not_see_great_aunts(self):
        self.assertEqual(self.vis("I10"), {"I3", "I5", "I6", "I7", "I10"})

    def test_unrelated_reader_sees_only_own_branch(self):
        self.assertEqual(self.vis("I13"), {"I13"})

    def test_unlinked_or_unknown_person_sees_nobody(self):
        self.assertEqual(self.vis(None), set())
        self.assertEqual(self.vis(""), set())
        self.assertEqual(self.vis("I99"), set())

    def test_admin_sees_everyone_living(self):
        self.assertEqual(self.vis(None, ADMIN), set(LIVING))

    def test_api_private_for_reader(self):
        self.add_reader("amy@example.org", "I6")
        r = self.client("amy@example.org").get("/api/private")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r.headers["cache-control"])
        d = r.json()
        self.assertEqual(set(d["people"]), {"I3", "I4", "I5", "I6", "I7", "I8", "I9", "I10", "I2"})   # I2: deceased, audience I8
        self.assertEqual(set(d["fams"]), {"F1", "F2"})
        self.assertEqual(set(d["images"]), {"pic6"})

    def test_api_private_for_unlinked_reader_is_empty(self):
        self.add_reader("amy@example.org", None)
        self.assertEqual(self.client("amy@example.org").get("/api/private").json(), {"people": {}, "fams": {}, "images": {}})

    def test_api_private_for_admin_has_everyone(self):
        d = self.client(ADMIN).get("/api/private").json()
        self.assertEqual(set(d["people"]), set(LIVING) | {"I2", "I11"})
        self.assertEqual(set(d["fams"]), {"F1", "F2", "F3", "F4"})
        self.assertEqual(set(d["images"]), {"pic6", "pic13"})

    def test_api_private_needs_session(self):
        self.assertEqual(self.client(origin=False).get("/api/private").status_code, 401)

    def test_missing_private_json_gives_empty_data(self):
        os.remove(os.path.join(self.priv, "private.json"))
        self.assertEqual(self.client(ADMIN).get("/api/private").json(), {"people": {}, "fams": {}, "images": {}})

    def test_private_json_is_reread_when_it_changes(self):
        self.assertEqual(self.vis(None, ADMIN), set(LIVING))
        path = os.path.join(self.priv, "private.json")
        d = make_private()
        d["living"] = ["I13"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.utime(path, (time.time() + 10, time.time() + 10))
        self.assertEqual(self.vis(None, ADMIN), {"I13"})

    def test_api_me(self):
        self.assertEqual(self.client(origin=False).get("/api/me").status_code, 401)
        self.add_reader("amy@example.org", "I6", name="Amy")
        self.add_reader("lone@example.org", None, name="Lone")
        self.assertEqual(self.client("amy@example.org").get("/api/me").json(),
                         {"email": "amy@example.org", "name": "Amy", "anchor": "I6", "self": True, "admin": False})
        lone = self.client("lone@example.org").get("/api/me").json()
        self.assertEqual((lone["anchor"], lone["self"], lone["admin"]), ("I1", False, False))
        me = self.client(ADMIN).get("/api/me").json()
        self.assertEqual((me["name"], me["anchor"], me["admin"]), ("Wanda Brandt", "I1", True))


class PrivateImages(AppCase):
    def setUp(self):
        super().setUp()
        self.add_reader("amy@example.org", "I6")
        self.amy = self.client("amy@example.org", origin=False)

    def test_reader_gets_own_image_and_thumbnail(self):
        for name in ("pic6.jpg", "pic6_t.jpg"):
            r = self.amy.get("/pimg/" + name)
            self.assertEqual((r.status_code, r.content), (200, b"JPEG " + name.encode()), name)
            self.assertIn("private", r.headers["cache-control"])

    def test_image_the_reader_may_not_see_is_404_but_admin_gets_it(self):
        self.assertEqual(self.amy.get("/pimg/pic13.jpg").status_code, 404)
        self.assertEqual(self.client(ADMIN, origin=False).get("/pimg/pic13.jpg").status_code, 200)

    def test_unknown_image_is_404(self):
        self.assertEqual(self.amy.get("/pimg/nothing.jpg").status_code, 404)

    def test_no_session_is_401(self):
        self.assertEqual(self.client(origin=False).get("/pimg/pic6.jpg").status_code, 401)

    def test_path_traversal_is_refused(self):
        for path in ("/pimg/..%2Fsecret.txt", "/pimg/..%2f..%2fsecret.txt", "/pimg/pic6.jpg%2F..%2F..%2Fsecret.txt",
                     "/pimg/pic6..jpg", "/pimg/pic6.%2E%2E%2Fsecret.txt", "/pimg/../secret.txt"):
            r = self.amy.get(path)
            self.assertIn(r.status_code, (404, 400), path)
            self.assertNotIn(b"secret", r.content, path)
        # Also with a name that passes the image check by prefix and then carries a path.
        r = self.mod.pimg("pic6.jpg/../../secret.txt", mock.Mock(cookies=self.amy.headers and {self.mod.COOKIE: self.amy.headers["cookie"].split("=", 1)[1]}))
        self.assertEqual(r.status_code, 404)


class Contribute(AppCase):
    def setUp(self):
        super().setUp()
        self.add_reader("amy@example.org", "I6", name="Amy")
        self.amy = self.client("amy@example.org")

    def post(self, c=None, data=None, files=None):
        return (c or self.amy).post("/api/contribute", data={"message": "Grandpa Karl kept bees.", "person": "I3"} if data is None else data, files=files)

    def count(self):
        return self.sql("select count(*) n from contributions")[0]["n"]

    def test_note_without_file(self):
        r = self.post()
        self.assertEqual((r.status_code, r.json()), (200, {"ok": True}))
        row = self.sql("select * from contributions")[0]
        self.assertEqual((row["email"], row["name"], row["person"], row["status"], row["file"]), ("amy@example.org", "Amy", "I3", "new", None))
        self.assertEqual([m[0] for m in self.sent], [ADMIN])
        self.assertIn("Karl Adler", self.sent[0][1])
        self.assertIn("Grandpa Karl kept bees.", self.sent[0][2])

    def test_every_allowed_type_is_saved_with_its_extension(self):
        for i, (ctype, ext) in enumerate(self.mod.UPLOAD_TYPES.items(), 1):
            r = self.post(files={"photo": ("whatever", b"bytes" + ctype.encode(), ctype)})
            self.assertEqual(r.status_code, 200, ctype)
            path = os.path.join(self.data, "uploads", f"{i}{ext}")
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"bytes" + ctype.encode())
        self.assertIn("A file is attached", self.sent[-1][2])

    def test_deceased_person_name_comes_from_public_json(self):
        self.post(data={"message": "Hi", "person": "I1"})
        self.assertIn("Hugo Adler", self.sent[0][1])

    def test_unknown_person_name_falls_back_to_the_id(self):
        self.post(data={"message": "Hi", "person": "I77"})
        self.assertIn("I77", self.sent[0][1])

    def test_bad_type_refused(self):
        for ctype in ("text/html", "application/x-msdownload", ""):
            r = self.post(files={"photo": ("a.bin", b"x", ctype)})
            self.assertEqual(r.status_code, 400, ctype)
        self.assertEqual(self.count(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.data, "uploads")))

    def test_oversized_file_refused_and_limit_is_exact(self):
        limit = self.mod.MAX_UPLOAD
        r = self.post(files={"photo": ("a.png", b"x" * (limit + 1), "image/png")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("15 MB", r.text)
        self.assertEqual(self.count(), 0)
        r = self.post(files={"photo": ("a.png", b"x" * limit, "image/png")})
        self.assertEqual(r.status_code, 200)

    def test_bad_person_id_refused(self):
        for person in ("", "X1", "I", "I1a", "i1", "I1\n<script>", "../I1"):
            self.assertEqual(self.post(data={"message": "Hi", "person": person}).status_code, 400, repr(person))
        self.assertEqual(self.post(data={"message": "Hi"}).status_code, 400)
        self.assertEqual(self.count(), 0)

    def test_empty_message_refused(self):
        self.assertEqual(self.post(data={"message": "   ", "person": "I3"}).status_code, 400)
        self.assertEqual(self.count(), 0)

    def test_long_message_is_cut(self):
        self.post(data={"message": "m" * 6000, "person": "I3"})
        self.assertEqual(len(self.sql("select message from contributions")[0]["message"]), 5000)

    def test_no_session_is_401(self):
        r = self.post(self.client(origin=True))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.count(), 0)

    def test_denied_reader_is_401(self):
        self.add_reader("bad@example.org", status="denied")
        self.assertEqual(self.post(self.client("bad@example.org")).status_code, 401)

    def test_bad_origin_refused(self):
        self.assertEqual(self.post(self.client("amy@example.org", origin=False)).status_code, 403)
        self.assertEqual(self.count(), 0)

    def test_twenty_a_day(self):
        for _ in range(20):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 429)
        self.assertEqual(self.count(), 20)

    def test_file_with_empty_filename_counts_as_no_file(self):
        r = self.post(files={"photo": ("", b"", "application/octet-stream")})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.sql("select file_type from contributions")[0]["file_type"])


class Settings(AppCase):
    def signin_page(self):
        return self.client(origin=False).get("/signin").text

    def request_page(self):
        return self.client().post("/signin", data={"email": "new@example.org"}).text


class NamesFromPublicJson(Settings):
    def test_names_and_texts_come_from_public_json(self):
        self.assertEqual(self.mod.site_name(), "The Adler Family")
        self.assertEqual(self.mod.owner_name(), "Wanda Brandt")
        self.assertEqual(self.mod.admin_person(), "I1")
        self.assertIn("The Adler Family", self.signin_page())
        page = self.request_page()
        self.assertIn("Wanda Brandt", page)
        self.assertIn("Hugo&#x27;s granddaughter", page)

    def test_mail_uses_site_name(self):
        self.add_reader("amy@example.org")
        self.client().post("/signin", data={"email": "amy@example.org"})
        self.assertIn("The Adler Family", self.sent[0][1])


class EnvOverrides(Settings):
    env_extra = {"SITE_NAME": "Override Family", "OWNER_NAME": "Olga Override", "ADMIN_PERSON": "I13"}

    def test_env_overrides_public_json(self):
        self.assertIn("Override Family", self.signin_page())
        self.assertNotIn("The Adler Family", self.signin_page())
        page = self.request_page()
        self.assertIn("Olga Override", page)
        self.assertNotIn("Wanda Brandt", page)
        self.assertIn("Hugo&#x27;s granddaughter", page)   # the example has no override

    def test_admin_row_uses_overrides(self):
        r = self.sql("select name, person from readers where email = ?", (ADMIN,))[0]
        self.assertEqual((r["name"], r["person"]), ("Olga Override", "I13"))
        self.assertEqual(self.client(ADMIN).get("/api/me").json()["anchor"], "I13")


class NoPublicJson(Settings):
    public = None

    def test_defaults_apply_without_crashing(self):
        self.assertEqual(self.mod.public_data(), {})
        self.assertEqual(self.mod.site_name(), "Our Family History")
        self.assertEqual(self.mod.owner_name(), "the family historian")
        self.assertEqual(self.mod.admin_person(), "I1")
        self.assertIn("Our Family History", self.signin_page())
        page = self.request_page()
        self.assertIn("the family historian", page)
        self.assertIn("Aunt Carol", page)
        r = self.sql("select name, person from readers where email = ?", (ADMIN,))[0]
        self.assertEqual((r["name"], r["person"]), ("the family historian", "I1"))

    def test_other_pages_still_work(self):
        c = self.client(ADMIN)
        self.assertEqual(c.get("/admin").status_code, 200)
        self.assertEqual(c.get("/api/me").status_code, 200)
        self.assertEqual(c.post("/api/contribute", data={"message": "Hi", "person": "I3"}).status_code, 200)
        self.assertIn("Karl Adler", self.sent[-1][1])

    def test_default_path_is_next_to_the_private_folder(self):
        os.environ.pop("PUBLIC_JSON", None)
        self.assertEqual(os.path.normpath(self.mod.public_path()), os.path.normpath(os.path.join(self.priv, "..", "site", "data", "public.json")))


class EmptyPublicJson(Settings):
    public = {}

    def test_empty_site_keys_fall_back(self):
        self.assertEqual(self.mod.site_name(), "Our Family History")
        self.assertEqual(self.mod.admin_person(), "I1")


class PublicJsonReread(Settings):
    def test_changed_public_json_is_picked_up(self):
        self.assertEqual(self.mod.site_name(), "The Adler Family")
        data = make_public()
        data["site"]["name"] = "Renamed"
        with open(self.public_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.utime(self.public_file, (time.time() + 10, time.time() + 10))
        self.assertEqual(self.mod.site_name(), "Renamed")


class Neutral(unittest.TestCase):
    def test_source_has_no_family_specific_names(self):
        with open(APP_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Our Family History", text)
        # No real address, domain or server: only example.com and localhost.
        for addr in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
            self.assertTrue(addr.endswith("@example.com"), addr)
        self.assertNotRegex(text, r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


if __name__ == "__main__":
    unittest.main()
