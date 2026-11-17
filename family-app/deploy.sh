#!/bin/sh
# Build the family website on this machine and copy everything to the server:
# the static site, the private data, and the sign-in app. The app container is
# rebuilt only when its code has changed, so a site-only deploy restarts nothing.
#
# Usage: family-app/deploy.sh [ssh-host]
#
# Settings come from "deploy" in site-src/site.json:
#   ssh_host     user@host to copy to (the argument overrides it)
#   remote_dir   folder on the server for site/, private/ and app/; a relative
#                path is in the SSH user's home (default: family)
#   url          the site's address; the script ends by checking its sign-in page
#   env_file     settings file on the server for docker compose (default: /etc/family.env)
# sources/ never leaves this machine, and the server's env file is not touched.
set -e
cd "$(dirname "$0")/.."
setting() {
  python3 -c 'import json, sys
try:
    with open("site-src/site.json", encoding="utf-8") as f:
        d = json.load(f).get("deploy") or {}
except OSError:
    d = {}
print(d.get(sys.argv[1]) or sys.argv[2])' "$1" "$2"
}
HOST="${1:-$(setting ssh_host '')}"
DIR="$(setting remote_dir family)"
URL="$(setting url '')"
ENV_FILE="$(setting env_file /etc/family.env)"
if [ -z "$HOST" ]; then
  echo "deploy: no server. Set deploy.ssh_host in site-src/site.json or pass user@host." >&2
  exit 2
fi
python3 tools/build_site.py
rsync -az --delete site/ "$HOST:$DIR/site/"
rsync -az --delete private/ "$HOST:$DIR/private/"
CHANGED=$(rsync -az --itemize-changes --exclude __pycache__ --exclude family.env --exclude data/ family-app/ "$HOST:$DIR/app/" | grep -v '/$' || true)
if [ -n "$CHANGED" ]; then
  echo "App code changed:"; echo "$CHANGED"
  ssh "$HOST" "cd $DIR/app && docker compose --env-file $ENV_FILE up -d --build"
  sleep "${DEPLOY_WAIT:-4}"
  ssh "$HOST" 'docker ps --filter name=family-api --format "family-api: {{.Status}}"'
else
  echo "App code unchanged; no rebuild."
fi
if [ -z "$URL" ]; then
  echo "No deploy.url in site-src/site.json, so the sign-in page was not checked."
  exit 0
fi
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${URL%/}/signin" || true)
echo "Sign-in page: HTTP $STATUS"
[ "$STATUS" = "200" ] || { echo "Warning: the sign-in page did not answer 200."; exit 1; }
