This is how to run the family website on a server of your own. The site itself is described in `site-src/README.md`. Record your own server's details (its address, where things are, the state of email and DNS) in `family-app/SERVER.md`, which the framework never touches.

The setup assumes a Linux server you can reach over SSH, with Docker, and a Caddy web server that handles HTTPS and routes each site from its own file in a `conf.d` folder, with the app containers on a shared Docker network called `web`. Nothing is built on the server: `tools/build_site.py` runs on your own machine, where `tree.ged` and `sources/` live, and `family-app/deploy.sh` copies only the built `site/` and `private/` folders and the app code up with rsync. `sources/` never leaves your machine.

Every request for a page, image or data file goes through Caddy's `forward_auth` to the app first, so nothing is served to anyone without a valid session. Living relatives' details are never in the static files; the app hands each reader only the ones the grandparents rule allows (`site-src/README.md`, "Living relatives").

## The files

| File | Belongs to | What it is |
|---|---|---|
| `app.py`, `Dockerfile`, `requirements.txt` | the framework | the sign-in app |
| `deploy.sh` | the framework | builds the site and copies it up |
| `docker-compose.yml` | the project | the app's container, settings and folders |
| `family.env.example` | the project | the template for `/etc/family.env` on the server |
| `family.caddy` | the project | the Caddy site file |
| `aws/` | the project | IAM policies for sending mail through Amazon SES |

## Setting it up

1. Choose an address for the site, such as `family.example.com`, and point a DNS A record at the server.
2. Fill in `deploy` in `site-src/site.json`: `ssh_host` (`user@host`), `remote_dir` (the folder for `site/`, `private/` and `app/`; a relative path is in the SSH user's home), and `url`.
3. Choose how email is sent. `EMAIL_BACKEND=console` writes each email to the container's log (`docker logs family-api`), which is enough to test and to sign yourself in. To invite relatives, set up a sending service. The app supports any service that accepts SMTP over TLS (`EMAIL_BACKEND=smtp` with `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` and `SMTP_PASSWORD`; for Resend's free plan, 100 emails a day, these are `smtp.resend.com`, `465`, `resend` and a sending-only API key), and Amazon SES (`EMAIL_BACKEND=ses`), with the policies and steps in `aws/README.md`. A new SES account starts in a sandbox that sends only to addresses verified one by one, and AWS may refuse to lift it for a small personal site, as it did for the first site built with this framework in November 2026; Resend asked only for three DNS records. Plan for SPF, DKIM and DMARC records on the domain, or the emails will land in spam.
4. Copy `family-app/` to the server, then on the server: `sudo install -m 640 -o root -g "$USER" <remote_dir>/app/family.env.example /etc/family.env`, edit it, and `sudo mkdir -p /data/family`.
5. Run `family-app/deploy.sh` on your machine. It builds the site, copies it and the app up, starts the container, and checks the sign-in page.
6. Edit `family.caddy` (the address and the bind mount), copy it into Caddy's `conf.d`, add the mount of `<remote_dir>/site` as `/srv/family` to the Caddy container, validate the Caddyfile, and restart Caddy.

The env file takes one `NAME=value` per line with no spaces around `=`, and comments on their own lines only.

## After each research update

```bash
family-app/deploy.sh
```

The script builds the site, copies `site/`, `private/` and `family-app/` (never `/etc/family.env`, and never a local `family-app/data/`), rebuilds the `family-api` container only when the app code changed, and ends by checking that the sign-in page answers 200. The app notices a new `private.json` or `public.json` by its modification time. Deploying publishes the tree's current state to every approved reader, so an agent runs it only when the owner asks.

## First sign-in

Open the site, enter `ADMIN_EMAIL`, and use the link (from the inbox, or from `docker logs family-api` while the console backend is on). The admin page is `/admin`: requests waiting for a decision, a form to add someone directly, the contributions relatives have sent, and the list of readers with a Remove button. When approving, choose who the reader is in the tree; the site opens on that person, and the choice decides which living relatives they see.

The request email to the admin has a link to a review page. The link alone never approves anyone, because email security scanners open links automatically; the decision needs a button press. A denied address may ask again, but its requests are recorded without emailing the admin.

## Testing locally

```bash
python3 tools/build_site.py
cd family-app
pip install -r requirements.txt
SITE_DIR=../site PRIVATE_DIR=../private DATA_DIR=./data ADMIN_EMAIL=you@example.com BASE_URL=http://localhost:8000 \
  uvicorn app:app --port 8000
```

With `SITE_DIR` set, the app serves the static site itself behind the same session check, so Caddy is not needed. Sign-in links appear in the terminal. `family-app/data/` is git-ignored.

## What the app stores

`/data/family/family.db` holds readers (email, name, person in the tree, approved or denied), access requests, single-use sign-in links (stored as hashes; 30 minutes, or 7 days for the welcome link), sessions (hashes, 90 days), contributions, and a rate-limit table. Attached photos and PDFs are in `/data/family/uploads/`, served only to the admin. Back up `/data/family/` with the server's other data. Removing a reader on the admin page also ends their sessions.

## Known limits

- The sign-in page tells a visitor whether an address is on the list, because it shows the request form if it is not. That seemed acceptable for a family site; hide it if your family would mind.
- Rate limits are per address and per IP: 5 sign-in emails an hour for an address, 20 requests an hour from an IP. There is no CAPTCHA, only a hidden honeypot field. If bots find the form, add Cloudflare Turnstile or similar.
- The sign-in code is the project's own, built from standard pieces (single-use expiring links stored as hashes, an HttpOnly cookie, an Origin check on every form). Have someone review it before you invite people whose data is sensitive.
