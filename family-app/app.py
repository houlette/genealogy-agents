"""Sign-in app for the family website (site-src/README.md and family-app/DEPLOYING.md).

Caddy asks GET /auth/check before serving any file of the static site; this app
answers from its session table. The app also runs the email sign-in, the access
requests the admin approves or denies, the admin page, and the per-reader data:
/api/me, /api/private (living relatives under the grandparents rule) and
/pimg/<file> (private images).

Settings come from the environment (see DEPLOYING.md). The site's name, the
admin's name and the root person default to what tools/build_site.py wrote
into public.json from site-src/site.json and project.json. Claude, 9 November
2026; made project-neutral for genealogy-agents, 17 November 2026.
"""
import hashlib
import html
import json
import logging
import os
import secrets
import sqlite3
import time
import unicodedata
from contextlib import closing
from urllib.parse import quote, urlparse

import re

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
DATA_DIR = os.environ.get("DATA_DIR", "./data")
PRIVATE_DIR = os.environ.get("PRIVATE_DIR", "../private")
SITE_DIR = os.environ.get("SITE_DIR", "")  # set only for local testing without Caddy
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "console")
MAIL_FROM = os.environ.get("MAIL_FROM", "Family History <family@example.com>")
REPLY_TO = os.environ.get("REPLY_TO", ADMIN_EMAIL)
SES_REGION = os.environ.get("SES_REGION", "us-east-1")
SECURE = BASE_URL.startswith("https://")

COOKIE = "fh_session"
SESSION_DAYS = 90
LINK_MINUTES = 30
WELCOME_LINK_DAYS = 7

DEFAULT_REQUEST_EXAMPLE = "for example, \"Aunt Carol's daughter\""


def public_path():
    return os.environ.get("PUBLIC_JSON") or os.path.join(SITE_DIR or os.path.join(PRIVATE_DIR, "..", "site"), "data", "public.json")


_public = {"mtime": None, "data": {}}


def public_data():
    """public.json from the build, reread when it changes; empty if it is missing."""
    path = public_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _public["mtime"] != mtime:
        with open(path, encoding="utf-8") as f:
            _public.update(mtime=mtime, data=json.load(f))
    return _public["data"]


def site_setting(key, default):
    return (public_data().get("site") or {}).get(key) or default


def site_name():
    return os.environ.get("SITE_NAME") or site_setting("name", "Our Family History")


def owner_name():
    """The admin, as relatives know them: "Your request has gone to <owner>"."""
    return os.environ.get("OWNER_NAME") or site_setting("owner_name", "the family historian")


def request_example():
    return site_setting("request_example", DEFAULT_REQUEST_EXAMPLE)


def admin_person():
    return os.environ.get("ADMIN_PERSON") or public_data().get("root") or "I1"


log = logging.getLogger("family")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# ---------- storage ----------

def db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(os.path.join(DATA_DIR, "family.db"))
    con.row_factory = sqlite3.Row
    return con


SCHEMA = """
create table if not exists readers (email text primary key, name text, person text, status text not null,
  note text, created real, decided real);
create table if not exists requests (id integer primary key, email text not null, name text, relation text,
  message text, created real, status text not null default 'pending', review_hash text);
create table if not exists links (hash text primary key, email text not null, expires real not null, used real);
create table if not exists sessions (hash text primary key, email text not null, created real, expires real, seen real);
create table if not exists hits (key text not null, at real not null);
create table if not exists contributions (id integer primary key, email text not null, name text, person text, message text,
  file text, file_type text, created real, status text not null default 'new');
"""


@app.on_event("startup")
def startup():
    with closing(db()) as con:
        con.executescript(SCHEMA)
        if "birth_name" not in [c[1] for c in con.execute("pragma table_info(requests)")]:
            con.execute("alter table requests add column birth_name text")
        if ADMIN_EMAIL:
            con.execute("insert into readers(email, name, person, status, created, decided) values(?, ?, ?, 'approved', ?, ?) "
                        "on conflict(email) do update set status='approved'", (ADMIN_EMAIL, owner_name(), admin_person(), time.time(), time.time()))
        con.commit()


def h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def norm(email):
    return (email or "").strip().lower()


def valid_email(email):
    return 3 < len(email) < 255 and "@" in email and "." in email.split("@")[-1] and not any(c in email for c in " <>,;\"'")


def limited(key, n, seconds):
    """True if `key` has been hit n times in the last `seconds`; records a hit otherwise."""
    now = time.time()
    with closing(db()) as con:
        con.execute("delete from hits where at < ?", (now - 86400,))
        count = con.execute("select count(*) from hits where key = ? and at > ?", (key, now - seconds)).fetchone()[0]
        if count >= n:
            con.commit()
            return True
        con.execute("insert into hits values(?, ?)", (key, now))
        con.commit()
    return False


def reader_for(request):
    tok = request.cookies.get(COOKIE)
    if not tok:
        return None
    now = time.time()
    with closing(db()) as con:
        row = con.execute("select r.* from sessions s join readers r on r.email = s.email "
                          "where s.hash = ? and s.expires > ? and r.status = 'approved'", (h(tok), now)).fetchone()
        if row:
            con.execute("update sessions set seen = ? where hash = ?", (now, h(tok)))
            con.commit()
    return dict(row) if row else None


def same_origin(request):
    """Reject cross-site form posts. Browsers send Origin on POST; fall back to Referer."""
    src = request.headers.get("origin") or request.headers.get("referer") or ""
    return urlparse(src).netloc == urlparse(BASE_URL).netloc


def client_ip(request):
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")


# ---------- email ----------

def send_email(to, subject, text):
    if EMAIL_BACKEND == "ses":
        import boto3
        boto3.client("sesv2", region_name=SES_REGION).send_email(
            FromEmailAddress=MAIL_FROM, Destination={"ToAddresses": [to]},
            ReplyToAddresses=[REPLY_TO] if REPLY_TO else [],
            Content={"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": text}}}})
        log.info("email sent to %s: %s", to, subject)
    else:
        log.info("EMAIL (console backend)\nTo: %s\nSubject: %s\n\n%s\n", to, subject, text)


def new_link(email, minutes):
    tok = secrets.token_urlsafe(32)
    with closing(db()) as con:
        con.execute("insert into links values(?, ?, ?, null)", (h(tok), email, time.time() + minutes * 60))
        con.commit()
    return f"{BASE_URL}/auth/login?t={tok}"


# ---------- pages ----------

STYLE = """<style>
:root{--bg:#f6f2ea;--surface:#fffdf8;--ink:#2b2620;--muted:#6f665a;--line:#ddd4c4;--accent:#b5562f;--accent-ink:#fffdf8}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#1b1916;--surface:#24211d;--ink:#ece5d8;--muted:#a59b8b;--line:#3a352e;--accent:#e08b63;--accent-ink:#1b1916}}
:root[data-theme="dark"]{--bg:#1b1916;--surface:#24211d;--ink:#ece5d8;--muted:#a59b8b;--line:#3a352e;--accent:#e08b63;--accent-ink:#1b1916}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 "Source Sans 3",system-ui,sans-serif}
main{max-width:560px;margin:8vh auto;padding:0 16px}.box{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:28px}
h1{font:600 1.7rem/1.2 Fraunces,Georgia,serif;margin:0 0 12px}p{margin:0 0 14px}.muted{color:var(--muted);font-size:.92rem}
label{display:block;font-weight:600;margin:14px 0 4px}input,select,textarea{width:100%;font:inherit;padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
button,.btn{display:inline-block;margin-top:18px;font:600 1rem inherit;font-family:inherit;color:var(--accent-ink);background:var(--accent);border:0;border-radius:999px;padding:10px 22px;cursor:pointer;text-decoration:none}
button.ghost{background:transparent;color:var(--ink);border:1px solid var(--line)}.hp{position:absolute;left:-5000px}
table{width:100%;border-collapse:collapse;font-size:.92rem}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.wide{max-width:980px}a{color:var(--accent)}form.inline{display:inline}form.inline button{margin-top:0;padding:5px 14px;font-size:.88rem}
</style>"""


def page(title, body, wide=False):
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>{html.escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=Source+Sans+3:wght@400;600&display=swap" rel="stylesheet">{STYLE}</head>
<body><main class="{'wide' if wide else ''}"><div class="box">{body}</div></main></body></html>""")


E = html.escape


# The part of an address after "#" (which person or chapter) never reaches the server, so the sign-in page saves
# it in the browser and the site opens that page after sign-in (site-src/app.js, boot).
REMEMBER_PAGE = """<script>try{if(location.hash.length>2)localStorage.setItem("family_next",JSON.stringify({h:location.hash,t:Date.now()}))}catch(e){}</script>"""


@app.get("/signin")
def signin_form(email: str = "", next: str = "/"):
    return page(f"Sign in: {site_name()}", f"""<h1>{E(site_name())}</h1>
<p>This is a private family history. Enter your email address and we will send you a link to sign in. There is no password.</p>
<form method="post" action="/signin"><label for="e">Email address</label><input id="e" name="email" type="email" required autocomplete="email" value="{E(email)}">
<input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true"><button type="submit">Send me a link</button></form>
{REMEMBER_PAGE}""")


@app.post("/signin")
def signin(request: Request, email: str = Form(""), website: str = Form("")):
    if not same_origin(request):
        return Response("Bad origin", 403)
    email = norm(email)
    if website or not valid_email(email):
        return signin_form(email)
    if limited("ip:" + client_ip(request), 20, 3600) or limited("signin:" + email, 5, 3600):
        return page("Please wait", "<h1>Please wait a little</h1><p>Too many sign-in attempts. Try again in an hour.</p>")
    with closing(db()) as con:
        r = con.execute("select status from readers where email = ?", (email,)).fetchone()
    if r and r["status"] == "approved":
        send_email(email, f"Your sign-in link for {site_name()}",
                   f"Here is your link to sign in to {site_name()}:\n\n{new_link(email, LINK_MINUTES)}\n\n"
                   f"It works once and expires in {LINK_MINUTES} minutes. If you did not ask for it, ignore this email.")
        return page("Check your email", f"<h1>Check your email</h1><p>We sent a sign-in link to <b>{E(email)}</b>. It expires in {LINK_MINUTES} minutes.</p>"
                    "<p class=muted>Nothing there? Check your spam folder, or <a href='/signin'>try again</a>.</p>")
    return request_form(email)


def request_form(email, name="", relation="", message="", note="", birth_name=""):
    return page(f"Request access: {site_name()}", f"""<h1>Ask to join</h1>
<p><b>{E(email)}</b> is not on the family list yet. Tell {E(owner_name())} who you are, and {E(owner_name())} will let you in.</p>{note}
<form method="post" action="/request"><input type="hidden" name="email" value="{E(email)}">
<label for="n">Your full name</label><input id="n" name="name" required maxlength="120" autocomplete="name" value="{E(name)}">
<label for="b">Your surname at birth, if different <span class="muted">(so we can find you in the tree)</span></label>
<input id="b" name="birth_name" maxlength="120" value="{E(birth_name)}">
<label for="r">How are you related? <span class="muted">({E(request_example())})</span></label>
<input id="r" name="relation" maxlength="300" value="{E(relation)}">
<label for="m">Anything else <span class="muted">(optional)</span></label><textarea id="m" name="message" rows="3" maxlength="1000">{E(message)}</textarea>
<input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true"><button type="submit">Send my request</button></form>
<p class="muted" style="margin-top:16px">Wrong address? <a href="/signin">Go back</a>.</p>""")


@app.post("/request")
def request_access(request: Request, email: str = Form(""), name: str = Form(""), relation: str = Form(""),
                   message: str = Form(""), website: str = Form(""), birth_name: str = Form("")):
    if not same_origin(request):
        return Response("Bad origin", 403)
    email = norm(email)
    thanks = page("Request sent", f"<h1>Thank you</h1><p>Your request has gone to {E(owner_name())}. Once it is approved, "
                  "you will get an email with a link to the site.</p>")
    if website or not valid_email(email) or not name.strip():
        return thanks if website else request_form(email, name, relation, message, birth_name=birth_name)
    if limited("ip:" + client_ip(request), 20, 3600) or limited("request:" + email, 3, 86400):
        return thanks
    with closing(db()) as con:
        r = con.execute("select status from readers where email = ?", (email,)).fetchone()
        pending = con.execute("select 1 from requests where email = ? and status = 'pending'", (email,)).fetchone()
        tok = secrets.token_urlsafe(32)
        con.execute("insert into requests(email, name, relation, message, created, review_hash, birth_name) values(?,?,?,?,?,?,?)",
                    (email, name.strip()[:120], relation.strip()[:300], message.strip()[:1000], time.time(), h(tok), birth_name.strip()[:120]))
        if r and r["status"] in ("denied", "approved"):
            con.execute("update requests set status = ? where id = last_insert_rowid()", ("ignored" if r["status"] == "denied" else "duplicate",))
        con.commit()
    if r and r["status"] == "approved":
        send_email(email, f"Your sign-in link for {site_name()}", f"You already have access. Sign in here:\n\n{new_link(email, LINK_MINUTES)}")
    elif not r and not pending and ADMIN_EMAIL:
        send_email(ADMIN_EMAIL, f"Family site: {name.strip()} asks to join",
                   f"{name.strip()} <{email}> asks to join {site_name()}.\n\nRelation: {relation.strip() or '(not given)'}\n"
                   f"Surname at birth: {birth_name.strip() or '(same)'}\nIn the tree: {guess_text(name, birth_name)}\n"
                   f"Message: {message.strip() or '(none)'}\n\nApprove or deny: {BASE_URL}/review/{tok}\n\nAll requests: {BASE_URL}/admin")
    return thanks


@app.get("/auth/login")
def login_confirm(t: str = ""):
    # A button press is needed because email security scanners open links and would use them up.
    return page("Sign in", f"""<h1>{E(site_name())}</h1><p>Press the button to finish signing in on this device.</p>
<form method="post" action="/auth/login"><input type="hidden" name="t" value="{E(t)}"><button type="submit">Sign in</button></form>""")


@app.post("/auth/login")
def login(request: Request, t: str = Form("")):
    if not same_origin(request):
        return Response("Bad origin", 403)
    now = time.time()
    with closing(db()) as con:
        row = con.execute("select email from links where hash = ? and used is null and expires > ?", (h(t), now)).fetchone()
        if not row:
            return page("Link expired", "<h1>That link has expired</h1><p>Sign-in links work once and expire after a while.</p><a class=btn href='/signin'>Send a new link</a>")
        con.execute("update links set used = ? where hash = ?", (now, h(t)))
        tok = secrets.token_urlsafe(32)
        con.execute("insert into sessions values(?, ?, ?, ?, ?)", (h(tok), row["email"], now, now + SESSION_DAYS * 86400, now))
        con.commit()
    resp = RedirectResponse("/", 303)
    resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, secure=SECURE, samesite="lax")
    return resp


@app.get("/auth/signout")
def signout(request: Request):
    tok = request.cookies.get(COOKIE)
    if tok:
        with closing(db()) as con:
            con.execute("delete from sessions where hash = ?", (h(tok),))
            con.commit()
    resp = page("Signed out", "<h1>You are signed out</h1><a class=btn href='/signin'>Sign in again</a>")
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/auth/check")
def check(request: Request):
    """Caddy forward_auth target. 2xx lets the request through; anything else is returned to the browser."""
    if reader_for(request):
        return Response(status_code=204)
    uri = request.headers.get("x-forwarded-uri", "/")
    if uri.startswith(("/data/", "/api/", "/img/", "/pimg/")):
        return Response("Sign in first", 401)
    return RedirectResponse("/signin?next=" + quote(uri), 302)


# ---------- private data ----------

_priv = {"mtime": 0, "data": None}


def private_data():
    path = os.path.join(PRIVATE_DIR, "private.json")
    m = os.path.getmtime(path) if os.path.exists(path) else 0
    if m != _priv["mtime"] or _priv["data"] is None:
        if m:
            with open(path, encoding="utf-8") as f:
                _priv["data"] = json.load(f)
        else:
            _priv["data"] = {"people": {}, "fams": {}, "images": {}, "graph": {}, "living": [], "audience": {}, "names": {}}
        _priv["mtime"] = m
    return _priv["data"]


def visible_living(reader):
    """The grandparents rule: living people who are the reader's ancestors or descendants, descendants of the
    reader's parents and grandparents, and spouses of all of these. The admin sees everyone."""
    d = private_data()
    living = set(d["living"])
    if reader.get("email") == ADMIN_EMAIL:
        return living
    me, g = reader.get("person"), d["graph"]
    if not me or me not in g:
        return set()
    seen, core = set(), set()

    def ancestors(k):
        for p in g[k]["p"]:
            if p not in seen:
                seen.add(p)
                ancestors(p)
    ancestors(me)
    core |= seen
    tops = {me}
    frontier = {me}
    for _ in range(2):
        frontier = {p for k in frontier for p in g[k]["p"]}
        tops |= frontier
    stack = list(tops)
    while stack:
        k = stack.pop()
        if k in core and k not in tops:
            continue
        core.add(k)
        stack += [c for c in g[k]["c"] if c not in core]
    core |= {s for k in list(core) for s in g[k]["s"]}
    return core & living


def private_for(reader):
    d = private_data()
    vis = visible_living(reader)
    people = {k: v for k, v in d["people"].items() if k in vis or (k not in d["living"] and set(d["audience"].get(k, [])) & vis)}
    # A family with a living member is shown only if one of its living members is visible.
    members = {}
    for k, p in d["people"].items():
        for f in p.get("fams", []) if k in d["living"] else []:
            members.setdefault(f, set()).add(k)
    fams = {f: v for f, v in d["fams"].items() if members.get(f, set()) & vis}
    imgs = {i for p in people.values() for i in p.get("img", [])}
    return {"people": people, "fams": fams, "images": {k: v for k, v in d["images"].items() if k in imgs}}


@app.get("/api/me")
def me(request: Request):
    r = reader_for(request)
    if not r:
        return JSONResponse({}, 401)
    return {"email": r["email"], "name": r["name"], "anchor": r["person"] or admin_person(), "self": bool(r["person"]),
            "admin": r["email"] == ADMIN_EMAIL}


@app.get("/api/private")
def api_private(request: Request):
    r = reader_for(request)
    if not r:
        return JSONResponse({}, 401)
    return JSONResponse(private_for(r), headers={"Cache-Control": "private, no-store"})


@app.get("/pimg/{name}")
def pimg(name: str, request: Request):
    r = reader_for(request)
    if not r:
        return Response(status_code=401)
    key = name.split(".")[0].removesuffix("_t")
    if key not in private_for(r)["images"] or "/" in name or ".." in name:
        return Response(status_code=404)
    path = os.path.join(PRIVATE_DIR, "img", name)
    if not os.path.isfile(path):  # listed in private.json but not copied up yet
        return Response(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


# ---------- contributions from relatives ----------

UPLOAD_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
                "image/heic": ".heic", "image/heif": ".heif", "application/pdf": ".pdf"}
MAX_UPLOAD = 15 * 1024 * 1024


def person_name(pid):
    d = private_data()
    if pid in d.get("names", {}):
        return d["names"][pid]
    return (public_data().get("people") or {}).get(pid, {}).get("n") or pid


@app.post("/api/contribute")
async def contribute(request: Request, message: str = Form(""), person: str = Form(""), photo: UploadFile | None = File(None)):
    r = reader_for(request)
    if not r:
        return Response("Please sign in again.", 401)
    if not same_origin(request):
        return Response("Bad origin", 403)
    message = message.strip()[:5000]
    if not message or not re.fullmatch(r"I\d+", person or ""):
        return Response("Please write something first.", 400)
    if limited("contrib:" + r["email"], 20, 86400):
        return Response("That is a lot for one day; please send the rest tomorrow.", 429)
    fname = ftype = None
    if photo is not None and photo.filename:
        ftype = (photo.content_type or "").lower()
        if ftype not in UPLOAD_TYPES:
            return Response("Please send a photo (JPEG, PNG, HEIC) or a PDF.", 400)
        data = await photo.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return Response("That file is over 15 MB; please send a smaller one.", 400)
    with closing(db()) as con:
        cur = con.execute("insert into contributions(email, name, person, message, file_type, created) values(?,?,?,?,?,?)",
                          (r["email"], r["name"], person, message, ftype, time.time()))
        cid = cur.lastrowid
        if ftype:
            os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)
            fname = f"{cid}{UPLOAD_TYPES[ftype]}"
            with open(os.path.join(DATA_DIR, "uploads", fname), "wb") as fh:
                fh.write(data)
            con.execute("update contributions set file = ? where id = ?", (fname, cid))
        con.commit()
    who = r["name"] or r["email"]
    if ADMIN_EMAIL:
        send_email(ADMIN_EMAIL, f"Family site: {who} wrote about {person_name(person)}",
                   f"{who} <{r['email']}> wrote about {person_name(person)} ({person}):\n\n{message}\n\n"
                   f"{'A file is attached on the site. ' if fname else ''}See it on the admin page: {BASE_URL}/admin#contributions")
    return {"ok": True}


@app.get("/admin/upload/{cid}")
def admin_upload(cid: int, request: Request):
    if not admin_or_none(request):
        return Response(status_code=403)
    with closing(db()) as con:
        row = con.execute("select file, file_type from contributions where id = ?", (cid,)).fetchone()
    if not row or not row["file"]:
        return Response(status_code=404)
    return FileResponse(os.path.join(DATA_DIR, "uploads", row["file"]), media_type=row["file_type"],
                        headers={"Cache-Control": "private, no-store", "Content-Disposition": f'inline; filename="contribution-{cid}{os.path.splitext(row["file"])[1]}"'})


@app.post("/admin/contrib/{cid}")
def admin_contrib(cid: int, request: Request):
    if not admin_or_none(request) or not same_origin(request):
        return Response("Forbidden", 403)
    with closing(db()) as con:
        con.execute("update contributions set status = case status when 'new' then 'entered' else 'new' end where id = ?", (cid,))
        con.commit()
    return RedirectResponse("/admin#contributions", 303)


# ---------- admin ----------

def admin_or_none(request):
    r = reader_for(request)
    return r if r and r["email"] == ADMIN_EMAIL else None


def _tokens(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z]+", s)


def guess_people(name, birth_name=""):
    """Living people in the tree whose first given name and surname match what the requester typed. The
    surname may be the last word of the name or the surname at birth. The admin still confirms the choice,
    because the linked person is where the site opens and decides which living relatives the reader sees."""
    t = _tokens(name)
    if len(t) < 2:
        return []
    given, surnames = t[0], {t[-1]} | set(_tokens(birth_name)[-1:])
    out = []
    for k, n in private_data().get("names", {}).items():
        nt = _tokens(n)
        if len(nt) >= 2 and nt[0] == given and nt[-1] in surnames:
            out.append(k)
    return out


def single_guess(name, birth_name=""):
    g = guess_people(name, birth_name)
    return g[0] if len(g) == 1 else ""


def guess_text(name, birth_name=""):
    g = guess_people(name, birth_name)
    if not g:
        return "no living person of that name; choose by hand"
    return ("probably " if len(g) == 1 else "one of ") + ", ".join(f"{person_name(k)} [{k}]" for k in g)


def guess_note(name, birth_name=""):
    g = guess_people(name, birth_name)
    if len(g) == 1:
        return f"<p class=muted>Chosen from the name: {E(person_name(g[0]))} [{E(g[0])}]. Change it if that is wrong.</p>"
    if g:
        return f"<p class=muted>More than one person has that name: {E(', '.join(person_name(k) + ' [' + k + ']' for k in g))}. Choose one.</p>"
    return "<p class=muted>No living person in the tree has that name. Choose them by hand, or leave them not linked.</p>"


def person_options(selected=""):
    d = private_data()
    names = dict(d.get("names", {}))
    for k, p in (public_data().get("people") or {}).items():
        if p.get("n"):
            names.setdefault(k, f"{p['n']} ({p.get('by') or '?'}–{p.get('dy') or '?'})")
    living = set(d["living"])
    opts = sorted(names.items(), key=lambda kv: (kv[0] not in living, kv[1]))
    return "<option value=''>(not linked: sees no living relatives)</option>" + "".join(
        f"<option value='{E(k)}'{' selected' if k == selected else ''}>{E(v)}{' (living)' if k in living else ''} [{E(k)}]</option>" for k, v in opts)


def decide(email, name, decision, person):
    now = time.time()
    with closing(db()) as con:
        con.execute("insert into readers(email, name, person, status, created, decided) values(?,?,?,?,?,?) "
                    "on conflict(email) do update set status = excluded.status, person = excluded.person, name = coalesce(excluded.name, readers.name), decided = excluded.decided",
                    (email, name, person or None, decision, now, now))
        con.execute("update requests set status = ? where email = ? and status = 'pending'", (decision, email))
        con.commit()
    if decision == "approved":
        send_email(email, f"You're in: {site_name()}",
                   f"Hello {name or ''},\n\n{owner_name()} has added you to {site_name()}. Use this link to sign in (it works once, for {WELCOME_LINK_DAYS} days):\n\n"
                   f"{new_link(email, WELCOME_LINK_DAYS * 1440)}\n\nAfter that, go to {BASE_URL} and enter your email address to get a new link whenever you need one.")


@app.get("/review/{tok}")
def review(tok: str):
    with closing(db()) as con:
        q = con.execute("select * from requests where review_hash = ?", (h(tok),)).fetchone()
    if not q:
        return page("Not found", "<h1>Request not found</h1>")
    if q["status"] != "pending":
        return page("Already decided", f"<h1>Already decided</h1><p>{E(q['email'])}: {E(q['status'])}.</p><a href='/admin'>Admin page</a>")
    return page("Review request", f"""<h1>{E(q['name'])} asks to join</h1><p><b>{E(q['email'])}</b></p>
<p>Surname at birth: {E(q['birth_name'] or '(same)')}</p><p>Relation: {E(q['relation'] or '(not given)')}</p><p>{E(q['message'] or '')}</p>
<form method="post" action="/review/{E(tok)}"><label for="p">Who are they in the tree? The site opens on this person, and it decides which living relatives they see.</label>
<select id="p" name="person">{person_options(single_guess(q['name'], q['birth_name']))}</select>{guess_note(q['name'], q['birth_name'])}
<button name="decision" value="approved" type="submit">Approve</button> <button class="ghost" name="decision" value="denied" type="submit">Deny</button></form>
<p class="muted" style="margin-top:14px">Denying sends nothing, and later requests from this address will not email you.</p>""")


@app.post("/review/{tok}")
def review_post(tok: str, request: Request, decision: str = Form(""), person: str = Form("")):
    if not same_origin(request) or decision not in ("approved", "denied"):
        return Response("Bad request", 400)
    with closing(db()) as con:
        q = con.execute("select * from requests where review_hash = ? and status = 'pending'", (h(tok),)).fetchone()
    if not q:
        return review(tok)
    decide(q["email"], q["name"], decision, person)
    return page("Done", f"<h1>{'Approved' if decision == 'approved' else 'Denied'}</h1><p>{E(q['email'])}</p><a href='/admin'>Admin page</a>")


@app.get("/admin")
def admin(request: Request):
    if not admin_or_none(request):
        return RedirectResponse("/signin?next=/admin", 302)
    with closing(db()) as con:
        reqs = con.execute("select * from requests where status = 'pending' order by created").fetchall()
        readers = con.execute("select r.*, (select max(seen) from sessions s where s.email = r.email) seen from readers r order by status, email").fetchall()
        contribs = con.execute("select * from contributions order by status = 'entered', created desc limit 200").fetchall()
    names = {**private_data().get("names", {})}
    rq = "".join(f"""<tr><td><b>{E(q['name'])}</b><br>{E(q['email'])}</td><td>{E(q['relation'] or '')}<br><span class=muted>{E(q['message'] or '')}</span></td>
<td><form method="post" action="/admin/decide"><input type="hidden" name="email" value="{E(q['email'])}"><input type="hidden" name="name" value="{E(q['name'])}">
<select name="person">{person_options(single_guess(q['name'], q['birth_name']))}</select>{guess_note(q['name'], q['birth_name'])}<button name="decision" value="approved">Approve</button> <button class="ghost" name="decision" value="denied">Deny</button></form></td></tr>""" for q in reqs)
    rd = "".join(f"""<tr><td>{E(r['name'] or '')}<br>{E(r['email'])}</td><td>{E(r['status'])}</td><td>{E(r['person'] or '')}</td>
<td>{time.strftime('%Y-%m-%d', time.localtime(r['seen'])) if r['seen'] else ''}</td><td>{'' if r['email'] == ADMIN_EMAIL else f'''<form class="inline" method="post" action="/admin/decide">
<input type="hidden" name="email" value="{E(r['email'])}"><input type="hidden" name="person" value="{E(r['person'] or '')}">
<button class="ghost" name="decision" value="{'denied' if r['status'] == 'approved' else 'approved'}">{'Remove' if r['status'] == 'approved' else 'Approve'}</button></form>'''}</td></tr>""" for r in readers)
    return page("Admin", f"""<h1>Family site admin</h1><h2>Waiting for you ({len(reqs)})</h2>
<table><tr><th>Who</th><th>Says</th><th>Decide</th></tr>{rq or '<tr><td colspan=3 class=muted>No requests waiting.</td></tr>'}</table>
<h2 id="contributions" style="margin-top:28px">Contributions ({sum(1 for c in contribs if c['status'] == 'new')} new)</h2>
<p class="muted">Enter each one in tree.ged as family knowledge, with the relative's name and the date, then mark it entered.</p>
<table><tr><th>From</th><th>About</th><th>What they wrote</th><th></th></tr>{''.join(f"""<tr><td>{E(c['name'] or '')}<br><span class=muted>{E(c['email'])}<br>{time.strftime('%Y-%m-%d', time.localtime(c['created']))}</span></td>
<td>{E(person_name(c['person']))}<br><span class=muted>{E(c['person'])}</span></td><td style="white-space:pre-wrap">{E(c['message'])}{f'<br><a href="/admin/upload/{c["id"]}" target="_blank">Open the attached file</a>' if c['file'] else ''}</td>
<td><form class="inline" method="post" action="/admin/contrib/{c['id']}"><button class="{'ghost' if c['status'] == 'entered' else ''}">{'Entered (undo)' if c['status'] == 'entered' else 'Mark entered'}</button></form></td></tr>""" for c in contribs) or '<tr><td colspan=4 class=muted>None yet.</td></tr>'}</table>
<h2 style="margin-top:28px">Add someone directly</h2><form method="post" action="/admin/decide"><label>Email</label><input name="email" type="email" required>
<label>Name</label><input name="name" id="addname"><label>Who are they in the tree? <span class=muted>(chosen from the name as you type; check it)</span></label><select name="person" id="addperson">{person_options()}</select>
<button name="decision" value="approved">Add and send welcome email</button></form>
<script>
(() => {{
  const t = s => (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().match(/[a-z]+/g) || [];
  const nm = document.getElementById("addname"), sel = document.getElementById("addperson");
  nm.addEventListener("input", () => {{
    const w = t(nm.value); if (w.length < 2) return;
    const hits = [...sel.options].filter(o => o.text.includes("(living)") && (n => n.length > 1 && n[0] === w[0] && n[n.length - 1] === w[w.length - 1])(t(o.text.split(" (")[0])));
    if (hits.length === 1) sel.value = hits[0].value;
  }});
}})();
</script>
<h2 style="margin-top:28px">Readers</h2><table><tr><th>Who</th><th>Status</th><th>Tree person</th><th>Last seen</th><th></th></tr>{rd}</table>
<p class="muted" style="margin-top:18px">{len(names)} living people in the build. <a href="/">Back to the site</a></p>""", wide=True)


@app.post("/admin/decide")
def admin_decide(request: Request, email: str = Form(""), name: str = Form(""), decision: str = Form(""), person: str = Form("")):
    if not admin_or_none(request) or not same_origin(request):
        return Response("Forbidden", 403)
    email = norm(email)
    if decision not in ("approved", "denied") or not valid_email(email) or email == ADMIN_EMAIL:
        return Response("Bad request", 400)
    if decision == "denied":
        with closing(db()) as con:
            con.execute("delete from sessions where email = ?", (email,))
            con.commit()
    decide(email, name.strip() or None, decision, person)
    return RedirectResponse("/admin", 303)


# ---------- local testing only: serve the static site behind the same check ----------

if SITE_DIR:
    @app.get("/{path:path}")
    def static(path: str, request: Request):
        if not reader_for(request):
            return RedirectResponse("/signin", 302)
        full = os.path.realpath(os.path.join(SITE_DIR, path or "index.html"))
        if not full.startswith(os.path.realpath(SITE_DIR)) or not os.path.isfile(full):
            return Response(status_code=404)
        return FileResponse(full)
