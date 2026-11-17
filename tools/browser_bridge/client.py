"""Python client for the browser bridge daemon.

Set the agent name once, with `client.set_agent('gemini')` or the BRIDGE_AGENT
environment variable, and every request carries it. Claim a site before using
it: `page = client.claim('familysearch')` returns the pageId of a new tab that
only this agent can drive. Call `client.release()` at the end of the session.
See README.md, section "Several agents at once".
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import project_config as cfg  # noqa: E402

URL = f"http://127.0.0.1:{os.environ.get('BRIDGE_PORT', cfg.BRIDGE.get('port', 9333))}"
AGENT = os.environ.get('BRIDGE_AGENT') or None


class ClaimRefused(RuntimeError):
    """Another agent holds the site or tab (HTTP 409 from the daemon)."""


def set_agent(name):
    global AGENT
    AGENT = name


def _read_json(req, timeout=None):
    """Preserve the daemon's diagnostic message when it returns an error."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode('utf-8', errors='replace')
        try:
            message = json.loads(body).get('error', body)
        except (ValueError, AttributeError):
            message = body
        if error.code == 409:
            raise ClaimRefused(message) from error
        raise RuntimeError(f'Browser bridge HTTP {error.code}: {message}') from error
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result


def _post(path, payload, timeout=None):
    if AGENT and 'agent' not in payload:
        payload = dict(payload, agent=AGENT)
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f'{URL}{path}', data=data,
                                 headers={'Content-Type': 'application/json'})
    return _read_json(req, timeout)


def _get(path):
    return _read_json(urllib.request.Request(f'{URL}{path}'))


def is_running():
    try:
        with urllib.request.urlopen(f'{URL}/ping', timeout=2) as resp:
            return json.loads(resp.read().decode('utf-8')).get('status') == 'ok'
    except Exception:
        return False


# Claims
def claim(site, url=None, background=True):
    """Claim a site ('familysearch', 'ancestry', 'archion.de', or a URL's
    site) and open a tab on it. Returns the new tab's pageId. Raises
    ClaimRefused if another agent holds the site."""
    payload = {'site': site, 'background': background}
    if url:
        payload['url'] = url
    return _post('/claim', payload, timeout=120)['pageId']


def open_tab(url, background=True):
    """Open another tab for this agent. The URL's site must be free or held by
    this agent."""
    return _post('/open', {'url': url, 'background': background}, timeout=120)['pageId']


def release(site=None, close_tabs=True):
    """Release one site, or, with no site, all of this agent's sites and
    (by default) close its tabs."""
    payload = {'closeTabs': close_tabs}
    if site:
        payload['site'] = site
    return _post('/release', payload)


def claims():
    """Who holds which site and tab."""
    return _get('/claims')


# Tabs
def get_pages(mine=False):
    """All open tabs with their owner and site holder; with mine=True, only
    this agent's tabs."""
    path = '/pages'
    if mine and AGENT:
        path += '?agent=' + urllib.request.quote(AGENT)
    return _get(path)['pages']


def find_page(query):
    """Fuzzy lookup among this agent's own tabs (or, with no agent name, among
    unowned tabs)."""
    q = query.lower()
    for p in get_pages():
        if p.get('owner') != AGENT:
            continue
        if q in p['url'].lower() or q in p['title'].lower():
            return p['id']
    raise ValueError(f"No tab matching '{query}'" + (f' owned by {AGENT}' if AGENT else ''))


def _page_id(page):
    return page if isinstance(page, int) else find_page(page)


def evaluate(page, js_func, timeout=20):
    return _post('/evaluate', {'pageId': _page_id(page), 'function': js_func,
                               'timeout': timeout}, timeout=timeout + 75)['result']


def navigate(page, target_url, wait_sec=3):
    res = _post('/navigate', {'pageId': _page_id(page), 'url': target_url})
    if wait_sec > 0:
        time.sleep(wait_sec)
    return res


def screenshot(page, file_path, full_page=False):
    return _post('/screenshot', {'pageId': _page_id(page), 'filePath': file_path,
                                 'fullPage': full_page})


def download_fs(img_num, dest_filename, page='familysearch'):
    """Download a FamilySearch viewer image into sources/<dest_filename>.
    Refuses to overwrite an existing file."""
    return _post('/download_fs', {'pageId': _page_id(page), 'imageNumber': str(img_num),
                                  'destFilename': dest_filename})


def download_file(page, file_url, dest_path):
    return _post('/download_file', {'pageId': _page_id(page), 'url': file_url,
                                    'destPath': dest_path})
