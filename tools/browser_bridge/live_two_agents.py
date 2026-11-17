"""Live test of the one-agent-per-site rule against a running daemon.


Run a second daemon so the live one is not disturbed, then the test:
    python3 tools/browser_bridge/daemon.py --port 9334 &
    python3 tools/browser_bridge/live_two_agents.py --port 9334

It uses example.com, example.org, wikipedia.org and loc.gov only, never a
signed-in research site, and closes every tab it opens. Run it again after
raising MCP_VERSION in daemon.py.
"""

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument('--port', type=int, default=9334)
URL = f'http://127.0.0.1:{ap.parse_args().port}'
failures = []


def post(agent, path, payload, timeout=60):
    body = dict(payload, agent=agent) if agent else payload
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        return {'status_code': e.code, **json.loads(e.read().decode())}


def get(path):
    return json.load(urllib.request.urlopen(URL + path))


def expect_refused(label, res):
    if res.get('status_code') == 409:
        print(f'ok    {label}: {res["error"]}')
    else:
        print(f'FAIL  {label}: not refused: {res}')
        failures.append(label)


def ok(label, res, key):
    if key not in res:
        print(f'FAIL  {label}: {res}')
        failures.append(label)
        return None
    print(f'ok    {label}: {res[key]}')
    return res[key]


n_tabs = len(get('/pages')['pages'])
a = ok('test-a claims example.com', post('test-a', '/claim', {'site': 'example.com', 'url': 'https://example.com/'}), 'pageId')
b = ok('test-b claims example.org', post('test-b', '/claim', {'site': 'example.org', 'url': 'https://example.org/'}), 'pageId')
expect_refused('test-b claims example.com', post('test-b', '/claim', {'site': 'example.com'}))
expect_refused('test-b uses test-a tab', post('test-b', '/evaluate', {'pageId': a, 'function': '() => 1'}))
expect_refused('test-b navigates to example.com', post('test-b', '/navigate', {'pageId': b, 'url': 'https://example.com/x'}))
expect_refused('nameless caller uses test-a tab', post(None, '/evaluate', {'pageId': a, 'function': '() => 1'}))
post('test-b', '/evaluate', {'pageId': b, 'function': "() => { location.href = 'https://example.com/drift'; return 1; }"})
time.sleep(2)
expect_refused('test-b after its tab drifts to example.com', post('test-b', '/evaluate', {'pageId': b, 'function': '() => 1'}))
post('test-a', '/release', {})
post('test-b', '/release', {})


def session(agent, site, urls, out):
    t = time.time()
    page = post(agent, '/claim', {'site': site, 'url': urls[0]})['pageId']
    for u in urls:
        post(agent, '/navigate', {'pageId': page, 'url': u})
        time.sleep(2)
        post(agent, '/evaluate', {'pageId': page, 'function': '() => document.body.innerText.slice(0, 80)'})
    post(agent, '/release', {})
    out[agent] = round(time.time() - t, 1)


A = ('test-a', 'wikipedia.org', [f'https://en.wikipedia.org/wiki/{p}' for p in
                                 ['Genealogy', 'Parish_register', 'Census', 'Baptism', 'Gravestone']])
B = ('test-b', 'loc.gov', [f'https://www.loc.gov/{p}/' for p in
                           ['about', 'collections', 'maps', 'manuscripts', 'photos']])
out = {}
t0 = time.time()
session(*A, out)
session(*B, out)
serial = time.time() - t0
t0 = time.time()
threads = [threading.Thread(target=session, args=(*X, out)) for X in (A, B)]
[t.start() for t in threads]
[t.join() for t in threads]
parallel = time.time() - t0
print(f'{"ok  " if parallel < 0.8 * serial else "FAIL"}  timing: serial {serial:.1f} s, parallel {parallel:.1f} s')
if parallel >= 0.8 * serial:
    failures.append('timing')

left = get('/claims')['sites']
tabs = len(get('/pages')['pages'])
print(f'{"ok  " if not left and tabs == n_tabs else "WARN"}  after: claims {left}, tabs {n_tabs} -> {tabs} '
      '(a tab the owner opens meanwhile changes the count)')
sys.exit(1 if failures else 0)
