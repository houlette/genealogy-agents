"""Persistent browser bridge: an HTTP server in front of one chrome-devtools-mcp
process connected to the owner's Chrome.

Several agents may use the bridge at once, under one rule: one agent per site
at a time, each in its own tab. Requests carry an
`agent` name; `claims.py` decides who may use which tab and site. See README.md.

Usage: python3 tools/browser_bridge/daemon.py [--port 9333]
The port can also be set with BRIDGE_PORT, and the claim lapse time (seconds)
with BRIDGE_CLAIM_TTL.
"""

import argparse
import base64
import glob
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from claims import SPARE, ClaimError, Claims, DEFAULT_TTL, normalize_site, site_of

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import project_config as cfg  # noqa: E402

OWNER = cfg.OWNER
PROFILE = cfg.BRIDGE.get('profile')
DOWNLOADS = os.path.expanduser(cfg.BRIDGE.get('downloads') or '~/Downloads')

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = Path(HERE).parent.parent
SOURCES = os.path.abspath(os.path.join(HERE, '..', '..', 'sources'))

MCP_PROC = None
REQ_ID = 0
PENDING_RESPONSES = {}
LOCK = threading.Lock()          # guards REQ_ID and PENDING_RESPONSES
WRITE_LOCK = threading.Lock()    # one JSON-RPC message on stdin at a time
DOWNLOADS_LOCK = threading.Lock()  # the downloads folder is shared; one download at a time
CLAIMS = None
# After a failed Chrome connection, browser calls are refused for a while.
# Every new connection attempt makes Chrome show its "Allow remote
# debugging?" prompt again, so agents retrying in a loop pile up prompts
# One attempt, then wait for the owner.
CONNECT_BACKOFF = int(os.environ.get('BRIDGE_CONNECT_BACKOFF', 120))
BACKOFF_UNTIL = 0.0
CHROME_STATE = 'not yet connected'
CAFFEINATE = None  # keeps the Mac awake while any site is claimed
MCP_VERSION = os.environ.get('BRIDGE_MCP_VERSION', '1.13.0')
MCP_STDERR = deque(maxlen=20)

# Where /claim opens a tab when it is given a site but no url.
HOME_URLS = {
    'familysearch': 'https://www.familysearch.org/',
    'ancestry': 'https://www.ancestry.com/',
    'archion': 'https://www.archion.de/',
    'geneanet': 'https://www.geneanet.org/',
    'matricula': 'https://data.matricula-online.eu/',
}


def log(msg):
    print(time.strftime('%H:%M:%S'), msg, file=sys.stderr, flush=True)


def reader_thread():
    while True:
        try:
            line = MCP_PROC.stdout.readline()
            if not line:
                break
            msg = json.loads(line)
            if msg.get('method') == 'roots/list' and 'id' in msg:
                _send({'jsonrpc': '2.0', 'id': msg['id'], 'result': {
                    'roots': [{'uri': WORKSPACE.as_uri(), 'name': WORKSPACE.name}]}})
                continue
            req_id = msg.get('id')
            if req_id is not None:
                with LOCK:
                    q = PENDING_RESPONSES.get(req_id)
                if q:
                    q.put(msg)
        except Exception as e:
            print("Reader exception:", e, flush=True)


def stderr_thread():
    while True:
        try:
            line = MCP_PROC.stderr.readline()
            if not line:
                break
            line = line.rstrip()
            MCP_STDERR.append(line)
            log(f'MCP stderr: {line}')
        except Exception:
            break


def _send(msg):
    with WRITE_LOCK:
        MCP_PROC.stdin.write(json.dumps(msg) + '\n')
        MCP_PROC.stdin.flush()


def init_mcp():
    global MCP_PROC, REQ_ID
    # --page-id-routing makes every page tool act on the pageId it is given,
    # not on whichever tab was selected last. The version is pinned because
    # @latest updated the shared npx cache under a running daemon on
    # 11 November 2026 (1.12.0 to 1.13.0); raise it deliberately and re-run
    # the two-agent test in README.md.
    MCP_PROC = subprocess.Popen(
        ['npx', '-y', f'chrome-devtools-mcp@{MCP_VERSION}', '--auto-connect', '--page-id-routing'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid
    )
    threading.Thread(target=reader_thread, daemon=True).start()
    threading.Thread(target=stderr_thread, daemon=True).start()

    REQ_ID = 1
    q = queue.Queue()
    with LOCK:
        PENDING_RESPONSES[REQ_ID] = q
    _send({
        'jsonrpc': '2.0',
        'id': REQ_ID,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {'roots': {'listChanged': False}},
            'clientInfo': {'name': 'persistent-bridge', 'version': '2.0'}
        }
    })
    q.get(timeout=15)
    print("MCP Init response received", flush=True)
    _send({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}})


class ChromeNotConnected(RuntimeError):
    pass


CONNECT_FAILURE_MARKS = ('waiting for the browser connection', 'Could not connect to Chrome')


def call_tool(name, args, timeout=30):
    global BACKOFF_UNTIL, CHROME_STATE
    wait = BACKOFF_UNTIL - time.time()
    if wait > 0:
        raise ChromeNotConnected(
            'Chrome is not connected: its last connection attempt was not approved. '
            f'Ask {OWNER} to click Allow on the remote-debugging prompt in Chrome (or unlock the computer); '
            f'the bridge will try once more in {int(wait)} s. Do not retry in a loop: each attempt '
            'makes Chrome ask again. This is a failed retrieval, not a search result.')
    try:
        result = _call_tool(name, args, timeout)
    except (TimeoutError, RuntimeError) as e:
        if isinstance(e, TimeoutError) or any(m in str(e) for m in CONNECT_FAILURE_MARKS):
            if name == 'list_pages' or any(m in str(e) for m in CONNECT_FAILURE_MARKS):
                BACKOFF_UNTIL = time.time() + CONNECT_BACKOFF
                CHROME_STATE = 'connection failed at ' + time.strftime('%H:%M:%S') + '; waiting for approval'
                log(f'Chrome connection failed; refusing browser calls for {CONNECT_BACKOFF} s')
        raise
    CHROME_STATE = 'connected'
    return result


def _call_tool(name, args, timeout=30):
    global REQ_ID
    with LOCK:
        REQ_ID += 1
        req_id = REQ_ID
        q = queue.Queue()
        PENDING_RESPONSES[req_id] = q
    _send({
        'jsonrpc': '2.0',
        'id': req_id,
        'method': 'tools/call',
        'params': {'name': name, 'arguments': args}
    })
    try:
        res = q.get(timeout=timeout)
    except queue.Empty:
        # Do not leave a timed-out exclusive tool queued in the MCP server.
        _send({'jsonrpc': '2.0', 'method': 'notifications/cancelled',
               'params': {'requestId': req_id, 'reason': 'Bridge timeout'}})
        diagnostic = f'; MCP exit code {MCP_PROC.poll()}' if MCP_PROC.poll() is not None else '; MCP process still running'
        if MCP_STDERR:
            diagnostic += '; recent MCP stderr: ' + ' | '.join(list(MCP_STDERR)[-3:])
        raise TimeoutError(f"Tool {name} timed out after {timeout}s{diagnostic}")
    finally:
        with LOCK:
            PENDING_RESPONSES.pop(req_id, None)

    if 'error' in res:
        raise RuntimeError(f"Tool error: {res['error']}")
    result = res.get('result', {})
    if result.get('isError'):
        text = '\n'.join(c.get('text', '') for c in result.get('content', []))
        raise RuntimeError(f"Tool failed: {text}")
    return result


def parse_pages(text):
    """Parse the page list that list_pages and new_page return:
    '33: Example Domain (https://example.com/) [selected]'."""
    pages = []
    for line in text.splitlines():
        parts = line.split(': ', 1)
        if len(parts) == 2 and parts[0].isdigit():
            rest = parts[1]
            selected = rest.endswith(' [selected]')
            if selected:
                rest = rest[:-len(' [selected]')]
            # The URL is the last parenthesized run without spaces, so a title
            # or URL that itself holds parentheses still splits correctly.
            m = re.match(r'^(.*?)\s*\((\S+)\)$', rest)
            if m:
                pages.append({'id': int(parts[0]), 'title': m.group(1).strip(),
                              'url': m.group(2), 'selected': selected})
    return pages


def list_pages():
    # MCP 1.13 allows 60 seconds for the initial Chrome connection. Give it
    # time to report its own error rather than dropping the response at 30s.
    res = call_tool('list_pages', {}, timeout=65)
    pages = parse_pages(res['content'][0]['text'])
    for p in pages:
        del p['selected']
    return pages


def page_url(page_id):
    for p in list_pages():
        if p['id'] == page_id:
            return p['url']
    raise ValueError(f"No open tab with pageId {page_id}")


def find_page(query, agent=None):
    """Fuzzy tab lookup. An agent finds only its own tabs, so a lookup by site
    name never lands in another agent's tab."""
    q = query.lower()
    for p in list_pages():
        if CLAIMS.page_owner(p['id']) != agent:
            continue  # an agent sees its own tabs; a nameless caller, unowned ones
        if q in p['url'].lower() or q in p['title'].lower():
            return p['id']
    raise ValueError(f"No tab matching '{query}'" + (f" owned by {agent}" if agent else ''))


def evaluate_script(page_id, js_func, timeout=20):
    clean_js = js_func.strip()
    if not (clean_js.startswith('()') or clean_js.startswith('async')
            or clean_js.startswith('function')):
        clean_js = f"() => {{ return ({clean_js}); }}"
    res = call_tool('evaluate_script', {
        'pageId': page_id,
        'function': clean_js,
        'waitForStableDom': False
    }, timeout=timeout)
    text = res['content'][0]['text']
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.find('```', start)
        val = text[start:end].strip()
        try:
            return json.loads(val)
        except Exception:
            return val
    return text


# FamilySearch full image without a Chrome download. Clicking the viewer's
# Download button makes Chrome show its download bubble, which brings the
# agents' Chrome window to the front and pulls the owner away from their
# own work. The viewer's image service returns the
# same full-size JPG (tested once: byte for byte identical to the button's file)
# for .../deepzoomcloud/dz/v1/<image ark>/dist.jpg when
# the page sends its own session token as a Bearer header; a fetch with cookies
# alone gets 401 or a CORS failure. The token stays inside the page and is
# never returned or written anywhere.
FS_FETCH_JS = """async () => {
    const want = %s;
    // The viewer's i= counts within the image group, which is not the film
    // image number on multi-item films, so also accept the number box.
    const box = () => document.querySelector('input[aria-label="Enter Image number"]');
    let ark = null, idx = null;
    for (let t = 0; t < 40; t++) {
        const m = location.pathname.match(/3:1:[A-Z0-9-]+/);
        const i = new URLSearchParams(location.search).get('i');
        const b = box();
        const atWant = (i === null ? want === 1 : Number(i) === want - 1) || (b && Number(b.value) === want);
        if (m && atWant) { ark = m[0]; idx = i; break; }
        await new Promise(r => setTimeout(r, 250));
    }
    if (!ark) throw new Error('viewer did not reach image ' + want + ' (url ' + location.pathname + location.search + ')');
    const res = performance.getEntriesByType('resource').map(e => e.name).filter(n => n.includes('/deepzoomcloud/dz/v1/'));
    const host = res.length ? new URL(res[0]).origin : 'https://sg30p0.familysearch.org';
    const tok = (document.cookie.match(/fssessionid=([^;]+)/) || [])[1];
    if (!tok) throw new Error('no FamilySearch session (signed out?)');
    const get = id => fetch(host + '/service/records/storage/deepzoomcloud/dz/v1/' + id + '/dist.jpg',
                            {credentials: 'omit', headers: {Authorization: 'Bearer ' + tok}});
    let r = await get(ark), used = ark;
    let type = r.headers.get('content-type') || '';
    let note = '';
    if (r.status === 200 && !type.startsWith('image/jpeg')) {
        // Seen 13 Nov 2026: an HTML page instead of the image. Keep its first
        // words for the log, then retry with the apid of the image the viewer
        // last loaded (its image.xml request).
        note = (await r.text()).replace(/\\s+/g, ' ').slice(0, 160);
        const xml = res.filter(n => n.endsWith('/image.xml'));
        const apid = xml.length ? xml[xml.length - 1].split('/dz/v1/')[1].split('/')[0] : null;
        if (apid) { r = await get(apid); used = apid; type = r.headers.get('content-type') || ''; }
    }
    if (r.status !== 200 || !type.startsWith('image/jpeg'))
        throw new Error('image service returned ' + r.status + ' ' + type + (note ? ' [first reply: ' + note + ']' : ''));
    const b = await r.blob();
    const data = await new Promise((resolve, reject) => {
        const fr = new FileReader(); fr.onloadend = () => resolve(fr.result); fr.onerror = reject; fr.readAsDataURL(b);
    });
    return {ark: ark, i: idx, used: used, retried: used !== ark, note: note, data: data};
}"""


def fetch_fs_image(page_id, img_num, dest_path):
    """Save the FamilySearch image shown in `page_id` (already moved to
    `img_num`) to `dest_path` by fetching it in the page. Returns
    (size, ark), or raises so the caller can fall back to the button."""
    res = evaluate_script(page_id, FS_FETCH_JS % int(img_num), timeout=90)
    if not isinstance(res, dict) or not str(res.get('data', '')).startswith('data:image/jpeg'):
        raise RuntimeError(f'unexpected result: {str(res)[:120]}')
    raw = base64.b64decode(res['data'].split(',', 1)[1])
    if raw[:3] != b'\xff\xd8\xff':
        raise RuntimeError('fetched data is not a JPEG')
    with open(dest_path, 'xb') as f:
        f.write(raw)
    if res.get('retried'):
        log(f"download_fs: dist.jpg for {res['ark']} gave HTML ({res.get('note', '')!r}); saved via {res.get('used')}")
    return len(raw), res['ark']


def open_page(agent, url, background=True):
    """Open a new tab at `url` for `agent` and return its pageId.

    With the profile check on, the tab is opened from the anchor tab (a tab
    known to be in the agents' profile) with window.open, so it lands in that
    profile whichever Chrome window is in front. Without an anchor, new_page
    opens it in whatever profile Chrome treats as current, and the marker
    check accepts or refuses it. new_page selects the tab it opens and runs
    exclusively inside the MCP, so the [selected] tab in its own response is
    the new one, even if the owner or another program opens a tab at the same
    moment (seen in testing, 11 November 2026)."""
    pages = list_pages()
    before = {p['id'] for p in pages}
    page_id = None
    if PROFILE_CHECK:
        page_id = reuse_spare(agent, url, pages)
        if page_id is not None:
            return page_id
        anchor = anchor_page(pages)
        if anchor is not None:
            try:
                page_id = spawn_from(anchor, before, 'tab')
            except Exception as e:
                log(f"anchor tab {anchor} failed to open a tab for {agent}: {e}")
            if page_id is None:
                log(f"anchor tab {anchor} opened no tab for {agent}; falling back to new_page")
    if page_id is None:
        page_id = open_with_new_page(agent, url, before, background)
    if PROFILE_CHECK:
        try:
            check_profile(page_id, url)
        except Exception:
            try:
                call_tool('close_page', {'pageId': page_id})
            except Exception:
                pass
            raise
    CLAIMS.own_page(agent, page_id)
    return page_id


# Spare tabs. Opening a tab with window.open brings Chrome to the front and
# can move the owner to its desktop, so
# a released tab is reset to the marker page and kept, up to SPARE_MAX, for the
# next claim. A spare tab already passed the profile check when it was first
# opened, and is checked again (marker_ok) before reuse, which also shows that
# it still answers; a tab that fails is closed. Spares left by an earlier daemon
# are found by SPARE_TAG in their URL.
SPARE_MAX = int(os.environ.get('BRIDGE_SPARE_TABS', 3))
SPARE_TAG = 'bridge-spare'


def reuse_spare(agent, url, pages):
    live = {p['id'] for p in pages}
    extra = [p['id'] for p in pages
             if p['url'].startswith(MARKER_URL) and SPARE_TAG in p['url']
             and CLAIMS.page_owner(p['id']) is None]
    for _ in range(SPARE_MAX + len(extra) + 1):
        page_id = CLAIMS.take_spare(agent, extra)
        if page_id is None:
            return None
        if page_id in live and marker_ok(page_id):
            evaluate_script(page_id, f"() => {{ window.location.href = {json.dumps(url)}; return true; }}")
            log(f"reused spare tab {page_id} for {agent}")
            return page_id
        CLAIMS.forget_page(page_id)
        if page_id in live:
            try:
                call_tool('close_page', {'pageId': page_id})
            except Exception:
                pass
        log(f"dropped spare tab {page_id}: gone or failed the profile check")
    return None


def make_spare(agent, page_id):
    """Reset `agent`'s tab to the marker page and put it in the pool.
    Returns True if pooled; False if the pool is full or the reset failed."""
    if not PROFILE_CHECK or len(CLAIMS.snapshot()['spare_tabs']) >= SPARE_MAX:
        return False
    target = f'{MARKER_URL}#{SPARE_TAG}-{uuid.uuid4().hex[:10]}'
    try:
        evaluate_script(page_id, f"() => {{ window.location.href = {json.dumps(target)}; return true; }}")
    except Exception:
        return False
    for _ in range(10):
        time.sleep(0.5)
        try:
            ready = page_url(page_id) == target and marker_ok(page_id)
        except Exception:
            return False  # the tab is gone
        if ready:
            return CLAIMS.pool_put(agent, page_id, SPARE_MAX)
    return False


def open_with_new_page(agent, url, before, background=True):
    # Check the profile before loading a research site. Opening the target
    # first can send its login flow through the wrong Chrome profile.
    initial_url = MARKER_URL if PROFILE_CHECK else url
    try:
        res = call_tool('new_page', {'url': initial_url, 'background': background, 'timeout': 30000}, timeout=50)
        text = '\n'.join(c.get('text', '') for c in res.get('content', []))
        selected = [p['id'] for p in parse_pages(text) if p['selected']]
        if len(selected) != 1:
            raise RuntimeError(f"Could not identify the new tab in new_page's response: {text[-300:]}")
    except Exception:
        # A failed open (often a slow site hitting the load timeout) can leave
        # the tab behind with no owner; seen once in testing, 11 November 2026.
        # Close new, unowned tabs on the same site, which are this call's.
        site = site_of(url)
        for p in list_pages():
            if (p['id'] not in before and CLAIMS.page_owner(p['id']) is None
                    and site and site_of(p['url']) == site):
                try:
                    call_tool('close_page', {'pageId': p['id']})
                    log(f"closed tab {p['id']} ({p['url'][:80]}) left by a failed open for {agent}")
                except Exception:
                    pass
        raise
    return selected[0]


# Agents must work in the Chrome profile named by "bridge.profile" in
# project.json, when it is set. Chrome profiles share one browser, and a new
# tab opens in whichever profile Chrome treats as current, so each new tab
# first loads MARKER_URL and reads a value stored there only in the agents'
# profile (set once by hand; see README.md, "Setting up"). A tab in any other
# profile is closed and refused. With no profile set, the check is off.
PROFILE_CHECK = bool(PROFILE) and os.environ.get('BRIDGE_PROFILE_CHECK', '1') != '0'
MARKER_URL = 'https://example.com/'
MARKER_KEY, MARKER_VALUE = 'bridge-profile', PROFILE or ''


class WrongProfile(RuntimeError):
    pass


def check_profile(page_id, url):
    evaluate_script(page_id, f"() => {{ window.location.href = {json.dumps(MARKER_URL)}; return true; }}")
    value = None
    for _ in range(20):
        time.sleep(0.5)
        try:
            value = evaluate_script(page_id, f"() => location.origin === 'https://example.com' ? (localStorage.getItem({json.dumps(MARKER_KEY)}) || '') : null")
        except Exception:
            value = None
        if value is not None:
            break
    if value != MARKER_VALUE:
        raise WrongProfile(f'The new tab did not open in Chrome\'s "{PROFILE}" profile (marker missing). '
                           f'Ask {OWNER} to bring a "{PROFILE}"-profile window to the front, then try once more. '
                           'This is a failed retrieval, not a search result.')
    if ANCHOR['id'] is None:
        plant_anchor(page_id)  # this tab is in the agents' profile and still on the marker page
    evaluate_script(page_id, f"() => {{ window.location.href = {json.dumps(url)}; return true; }}")


# The anchor tab. new_page cannot name a profile, so without an anchor every
# tab of a multi-worker run can be refused while another profile's window is
# in front. A tab opened with window.open
# belongs to the profile of the page that opened it. So the first tab that
# passes the marker check opens one more tab on the marker page, the anchor,
# which stays open and is owned by no agent; later tabs are opened from it.
# If the anchor is closed, or a daemon restart forgets it, an unowned tab on
# the marker page with the anchor tag is adopted, and failing that the next
# open falls back to new_page and plants a new anchor once it passes the check.
ANCHOR = {'id': None}
ANCHOR_TAG = 'bridge-anchor'
MARKER_READ = (f"() => location.origin === 'https://example.com' ? "
               f"(localStorage.getItem({json.dumps(MARKER_KEY)}) || '') : null")


def marker_ok(page_id):
    try:
        return evaluate_script(page_id, MARKER_READ) == MARKER_VALUE
    except Exception:
        return False


def spawn_from(page_id, before, tag):
    """Open a tab on the marker page from `page_id` and return the new pageId,
    or None if no new tab appeared (for example, the pop-up was blocked)."""
    target = f'{MARKER_URL}#{tag}-{uuid.uuid4().hex[:10]}'
    evaluate_script(page_id, f"() => {{ window.open({json.dumps(target)}, '_blank', 'noopener'); return true; }}")
    for _ in range(10):
        time.sleep(0.5)
        for p in list_pages():
            if p['id'] not in before and p['url'] == target:
                return p['id']
    return None


def anchor_page(pages):
    """The pageId of a tab shown to be in the agents' profile, or None."""
    by_id = {p['id']: p for p in pages}
    known = ANCHOR['id']
    if known in by_id and by_id[known]['url'].startswith(MARKER_URL) and marker_ok(known):
        return known
    ANCHOR['id'] = None
    for p in pages:
        if (p['url'].startswith(MARKER_URL) and ANCHOR_TAG in p['url']
                and CLAIMS.page_owner(p['id']) is None and marker_ok(p['id'])):
            ANCHOR['id'] = p['id']
            log(f"adopted tab {p['id']} as the profile anchor")
            return p['id']
    return None


def plant_anchor(page_id):
    try:
        new = spawn_from(page_id, {p['id'] for p in list_pages()}, ANCHOR_TAG)
        if new is not None and marker_ok(new):
            ANCHOR['id'] = new
            log(f"opened tab {new} as the profile anchor")
        elif new is not None:
            call_tool('close_page', {'pageId': new})
    except Exception as e:
        log(f"could not open an anchor tab: {e}")


class BridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == '/pages':
                agent = parse_qs(parsed.query).get('agent', [None])[0]
                pages = list_pages()
                for p in pages:
                    p['owner'] = CLAIMS.page_owner(p['id'])
                    p['site'] = site_of(p['url'])
                    p['site_holder'] = CLAIMS.holder(p['site']) if p['site'] else None
                if agent:
                    pages = [p for p in pages if p['owner'] == agent]
                self._send_json({'pages': pages})
            elif parsed.path == '/claims':
                self._send_json(CLAIMS.snapshot())
            elif parsed.path == '/ping':
                self._send_json({'status': 'ok', 'version': 2, 'chrome': CHROME_STATE,
                                 'retry_in_s': max(0, int(BACKOFF_UNTIL - time.time())),
                                 'keeping_mac_awake': CAFFEINATE is not None and CAFFEINATE.poll() is None})
            else:
                self._send_json({'error': 'Not found'}, 404)
        except Exception as e:
            self._send_json({'error': str(e)}, 500)

    def _page_for(self, payload, agent, target_url=None):
        """Resolve the request's tab and check it against the claims."""
        page_id = payload.get('pageId')
        if page_id is None and payload.get('page'):
            page_id = find_page(payload['page'], agent)
        if page_id is None:
            raise ValueError('pageId (or page) is required')
        page_id = int(page_id)
        CLAIMS.check_page(agent, page_id, page_url(page_id), target_url)
        return page_id

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(length).decode('utf-8', errors='replace')
        try:
            payload = json.loads(post_data) if post_data else {}
            if not isinstance(payload, dict):
                raise ValueError('request body must be a JSON object')
        except ValueError as e:
            self._send_json({'error': f'Bad request: {e}'}, 400)
            return
        agent = payload.get('agent') or None

        try:
            if self.path == '/claim':
                # Claim a site and open a tab on it.
                if not agent:
                    raise ClaimError('/claim needs an agent name')
                url = payload.get('url')
                site = normalize_site(payload.get('site') or url or '')
                if site is None:
                    raise ValueError('give a site (familysearch.org) or an http(s) url')
                if not url:
                    url = HOME_URLS.get(site, 'https://' + site + '/')
                if site_of(url) != site:
                    raise ValueError(f'url {url} is not on site {site}')
                already_held = CLAIMS.holder(site) == agent
                CLAIMS.claim_site(agent, site)
                try:
                    page_id = open_page(agent, url, payload.get('background', True))
                except Exception:
                    # A claim that could not open its tab (for example while
                    # Chrome is unreachable, 11 November 2026) would otherwise
                    # block the site for everyone until it lapsed.
                    if not already_held:
                        CLAIMS.release(agent, site)
                    raise
                self._send_json({'status': 'claimed', 'site': site, 'pageId': page_id})

            elif self.path == '/open':
                # Another tab for an agent, on a site it holds or a free one.
                if not agent:
                    raise ClaimError('/open needs an agent name')
                url = payload.get('url')
                site = site_of(url)
                if site:
                    CLAIMS.claim_site(agent, site)
                page_id = open_page(agent, url, payload.get('background', True))
                self._send_json({'status': 'opened', 'site': site, 'pageId': page_id})

            elif self.path == '/release':
                if not agent:
                    raise ClaimError('/release needs an agent name')
                site = payload.get('site')
                site = normalize_site(site) if site else None
                spare = []
                close_tabs = payload.get('closeTabs', True)
                mine = [int(p) for p, c in CLAIMS.snapshot()['pages'].items() if c['agent'] == agent]
                if site is not None:
                    # Releasing one site also gives up the agent's tabs on it.
                    # Otherwise a script still running on such a tab claimed
                    # the site again on its next request, and the claim sat
                    # idle until it lapsed (archion24, 13 November 2026).
                    try:
                        urls = {p['id']: p['url'] for p in list_pages()}
                    except Exception:
                        urls = {}
                    mine = [pid for pid in mine if site_of(urls.get(pid)) == site]
                if close_tabs:
                    # Keep the agent's tabs as spares where there is room
                    # (see SPARE_MAX); close the rest below.
                    for pid in mine:
                        if make_spare(agent, pid):
                            spare.append(pid)
                            log(f'kept tab {pid} as a spare on release by {agent}')
                sites, pages = CLAIMS.release(agent, site)
                if site is not None:
                    pages = mine
                closed = []
                if close_tabs:
                    for pid in pages:
                        if pid in spare:
                            continue
                        CLAIMS.forget_page(pid)
                        try:
                            call_tool('close_page', {'pageId': pid})
                            closed.append(pid)
                            log(f'closed tab {pid} on release by {agent}')
                        except Exception:
                            pass  # already closed, or the last tab in the window
                log(f"release by {agent}: sites {sites or 'none'}, spare {spare or 'none'}, closed {closed or 'none'}")
                self._send_json({'status': 'released', 'sites': sites, 'closedTabs': closed, 'spareTabs': spare})

            elif self.path == '/evaluate':
                page_id = self._page_for(payload, agent)
                res = evaluate_script(page_id, payload.get('function'),
                                      timeout=payload.get('timeout', 20))
                self._send_json({'result': res})

            elif self.path == '/navigate':
                url = payload.get('url')
                page_id = self._page_for(payload, agent, target_url=url)
                evaluate_script(page_id, f"() => {{ window.location.href = {json.dumps(url)}; return true; }}")
                self._send_json({'status': 'navigated', 'url': url})

            elif self.path == '/screenshot':
                page_id = self._page_for(payload, agent)
                res = call_tool('take_screenshot', {'pageId': page_id,
                                                    'filePath': payload.get('filePath'),
                                                    'fullPage': payload.get('fullPage', False)})
                self._send_json({'status': 'screenshot_taken', 'result': res})

            elif self.path == '/call_tool':
                name = payload.get('name')
                args = payload.get('args', {})
                if name in ('new_page', 'select_page'):
                    raise ClaimError(f'{name} is not allowed through /call_tool; use /claim or /open')
                if 'pageId' in args:
                    args['pageId'] = self._page_for({'pageId': args['pageId']}, agent,
                                                    target_url=args.get('url'))
                elif name not in ('list_pages',):
                    raise ClaimError(f'{name} through /call_tool needs a pageId')
                res = call_tool(name, args, timeout=payload.get('timeout', 30))
                if name == 'close_page':
                    CLAIMS.forget_page(args['pageId'])
                    log(f"closed tab {args['pageId']} by close_page from {agent}")
                self._send_json({'result': res})

            elif self.path == '/download_fs':
                if payload.get('pageId') is None and not payload.get('page'):
                    payload['page'] = 'familysearch'
                page_id = self._page_for(payload, agent)
                img_num = json.dumps(str(payload.get('imageNumber')))
                dest_filename = os.path.basename(payload.get('destFilename'))
                dest_path = os.path.join(SOURCES, dest_filename)
                if os.path.exists(dest_path):
                    raise FileExistsError(f'{dest_path} exists; refusing to overwrite')

                evaluate_script(page_id, f"""() => {{
                    const input = document.querySelector('input[aria-label="Enter Image number"]');
                    if (!input) throw new Error('FamilySearch image-number control not found');
                    if (input) {{
                        // The viewer is React: a plain `input.value =` may be ignored.
                        input.focus();
                        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, {img_num});
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        input.dispatchEvent(new KeyboardEvent('keydown', {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }}));
                        input.dispatchEvent(new KeyboardEvent('keyup', {{ key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }}));
                    }}
                    return true;
                }}""")
                time.sleep(2.5)

                # First choice: fetch the image in the page, with no Chrome
                # download (see FS_FETCH_JS). Fall back to the button only if
                # that fails, and say so in the log and the reply.
                try:
                    size, ark = fetch_fs_image(page_id, payload.get('imageNumber'), dest_path)
                    log(f"download_fs: {agent} saved image {payload.get('imageNumber')} ({ark}) by fetch, {size} bytes")
                    self._send_json({'status': 'saved', 'path': dest_path, 'size': size,
                                     'method': 'fetch', 'ark': ark})
                    return
                except Exception as e:
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    log(f"download_fs: fetch failed for {agent} image {payload.get('imageNumber')}: {e}; "
                        "using the download button (Chrome may come to the front)")

                # The browser saves to the downloads folder, which every tab shares, so
                # only one download may be in flight; otherwise one agent could
                # pick up another's file and save it under the wrong name.
                with DOWNLOADS_LOCK:
                    before_files = set(glob.glob(os.path.join(DOWNLOADS, '*.jpg')))
                    evaluate_script(page_id, """() => {
                        const btn = document.querySelector('button[data-testid="download-image-button"]');
                        if (!btn) throw new Error('FamilySearch image download button not found');
                        btn.click();
                        return true;
                    }""")
                    new_file = None
                    format_confirmed = False
                    for _ in range(20):  # up to 10 seconds
                        time.sleep(0.5)
                        diff = set(glob.glob(os.path.join(DOWNLOADS, '*.jpg'))) - before_files
                        if len(diff) == 1:
                            new_file = diff.pop()
                            break
                        if len(diff) > 1:
                            raise RuntimeError(f'More than one new file in the downloads folder: {sorted(diff)}')
                        if not format_confirmed:
                            # The current viewer opens a format dialog whose
                            # default is PDF. Select JPG explicitly; older
                            # viewers download directly and have no dialog.
                            format_confirmed = evaluate_script(page_id, """() => {
                                const jpg = Array.from(document.querySelectorAll('input'))
                                    .find(e => e.value === 'JPG Only');
                                const confirm = document.querySelector('button[data-testid="full-text-confirm-download"]');
                                if (!jpg || !confirm) return false;
                                jpg.click();
                                if (!jpg.checked || confirm.disabled) return false;
                                confirm.click();
                                return true;
                            }""") is True
                    if not new_file:
                        raise RuntimeError("Image download timed out; no new file in the downloads folder")
                    shutil.copy2(new_file, dest_path)
                self._send_json({'status': 'saved', 'path': dest_path, 'size': os.path.getsize(dest_path), 'method': 'button'})

            elif self.path == '/download_file':
                page_id = self._page_for(payload, agent)
                file_url = payload.get('url')
                dest_path = payload.get('destPath')
                js = f"""async () => {{
                    const resp = await fetch({json.dumps(file_url)});
                    const blob = await resp.blob();
                    return new Promise((resolve, reject) => {{
                        const reader = new FileReader();
                        reader.onloadend = () => resolve(reader.result);
                        reader.onerror = reject;
                        reader.readAsDataURL(blob);
                    }});
                }}"""
                data_url = evaluate_script(page_id, js, timeout=30)
                if isinstance(data_url, dict) or not str(data_url).startswith('data:'):
                    raise RuntimeError(f"Expected data URL, got: {str(data_url)[:100]}")
                header, b64_data = str(data_url).split(',', 1)
                raw_bytes = base64.b64decode(b64_data)
                os.makedirs(os.path.dirname(dest_path) or '.', exist_ok=True)
                with open(dest_path, 'wb') as f:
                    f.write(raw_bytes)
                self._send_json({'status': 'saved', 'path': dest_path, 'size': len(raw_bytes)})

            else:
                self._send_json({'error': 'Not found'}, 404)
        except ClaimError as e:
            log(f'409 {self.path} agent={agent}: {e}')
            self._send_json({'error': f'Claim refused: {e}'}, 409)
        except Exception as e:
            log(f'500 {self.path} agent={agent}: {type(e).__name__}: {e}')
            self._send_json({'error': str(e)}, 500)

    def log_message(self, format, *args):
        pass


def caffeinate_thread():
    """Run `caffeinate -dims` while any site is claimed, so the Mac does not
    sleep or lock (which drops the Chrome connection and brings a new prompt)
    in the middle of an agent's work; stop it when no site is held."""
    global CAFFEINATE
    # Stay awake for a while after the last release too: on 11 November 2026
    # a 28-minute gap between two agents let the Mac lock, which dropped the
    # Chrome connection and needed a new approval.
    grace = int(os.environ.get('BRIDGE_AWAKE_GRACE', 3600))
    last_busy = time.time()
    while True:
        try:
            if CLAIMS.snapshot()['sites']:
                last_busy = time.time()
            busy = time.time() - last_busy < grace
            running = CAFFEINATE is not None and CAFFEINATE.poll() is None
            if busy and not running:
                CAFFEINATE = subprocess.Popen(['caffeinate', '-dims'])
                log('keeping the Mac awake while sites are claimed')
            elif not busy and running:
                CAFFEINATE.terminate()
                CAFFEINATE = None
                log(f'no sites claimed for {grace} s; letting the Mac sleep')
        except Exception as e:
            log(f'caffeinate thread: {e}')
        time.sleep(20)


def cleanup():
    if CAFFEINATE is not None:
        try:
            CAFFEINATE.terminate()
        except Exception:
            pass
    if MCP_PROC:
        try:
            os.killpg(os.getpgid(MCP_PROC.pid), signal.SIGKILL)
        except Exception:
            try:
                MCP_PROC.kill()
            except Exception:
                pass


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get('BRIDGE_PORT', cfg.BRIDGE.get('port', 9333))))
    ap.add_argument('--claims-file', default=None,
                    help='default: claims.json next to this file, or claims-<port>.json off 9333')
    opts = ap.parse_args()
    claims_file = opts.claims_file or os.path.join(
        HERE, 'claims.json' if opts.port == 9333 else f'claims-{opts.port}.json')
    CLAIMS = Claims(claims_file, ttl=int(os.environ.get('BRIDGE_CLAIM_TTL', DEFAULT_TTL)))
    # A daemon started in the background ignores SIGINT, so stop on SIGTERM
    # too, through the finally below, which also stops chrome-devtools-mcp.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        print("Starting MCP connection...", flush=True)
        init_mcp()
        if shutil.which('caffeinate'):  # macOS only; elsewhere keep the computer awake by other means
            threading.Thread(target=caffeinate_thread, daemon=True).start()
        print(f"MCP initialized. Chrome connection is checked on browser requests. Starting HTTP daemon on 127.0.0.1:{opts.port}...", flush=True)
        server = ThreadingHTTPServer(('127.0.0.1', opts.port), BridgeHandler)
        server.daemon_threads = True
        server.serve_forever()
    finally:
        cleanup()
