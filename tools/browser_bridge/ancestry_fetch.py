#!/usr/bin/env python3
"""Save Ancestry viewer images at full resolution without the Download button.

    BRIDGE_AGENT=<name> python3 tools/browser_bridge/ancestry_fetch.py \
        --page <tab id> --db 9061 --out scratch/<name>/ 004772388_00655 004772388_00656

The tab must be a signed-in Ancestry tab you have claimed (client.claim('ancestry', url)).
For each image id it asks the viewer's media-token endpoint for a fresh, short-lived
download URL and saves it through the bridge (client.download_file), so no Chrome
download prompt is involved. Tokens are never printed or written to disk.
Files are saved as <out>/<image id>.jpg; check and rename them before moving them
into sources/ (see site-notes.md).
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--page', type=int, required=True)
ap.add_argument('--db', required=True, help='Ancestry collection id, e.g. 9061')
ap.add_argument('--out', required=True)
ap.add_argument('--pause', type=float, default=5, help='seconds between images')
ap.add_argument('images', nargs='+')
a = ap.parse_args()
client.set_agent(os.environ.get('BRIDGE_AGENT') or sys.exit('set BRIDGE_AGENT'))
os.makedirs(a.out, exist_ok=True)
for n, img in enumerate(a.images):
    if n: time.sleep(a.pause)
    js = ("async () => { const r = await fetch('/imageviewer/api/media/token?dbId=%s&imageId=%s', "
          "{credentials: 'include'}); if (!r.ok) return 'HTTP ' + r.status; const j = await r.json(); "
          "return j.imageDownloadUrl ? 'https://www.ancestry.com' + j.imageDownloadUrl : 'no imageDownloadUrl'; }") % (a.db, img)
    url = str(client.evaluate(a.page, js, timeout=30)).strip('"')
    if not url.startswith('https://'):
        print(img, 'FAILED:', url); continue
    r = client.download_file(a.page, url, os.path.abspath(os.path.join(a.out, img + '.jpg')))
    print(img, r.get('status') if isinstance(r, dict) else r, r.get('size') if isinstance(r, dict) else '')
