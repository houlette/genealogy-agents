"""Site claims for the browser bridge: one agent per site at a time.


A claim gives one agent a site (a registrable domain, or a group of domains
that share an account, such as Ancestry, Fold3 and Newspapers.com) and the
tabs it opened there. The daemon checks every page request against the claims,
so two agents cannot drive the same site or the same tab at once. A claim
lapses after a period without use (default 30 minutes), so a crashed session
does not block a site for long.

This module has no browser code, so `test_claims.py` can test it alone.
"""

import json
import os
import threading
import time
from urllib.parse import urlparse

# Domains that share one account and, as far as we know, one rate limit, so
# they are claimed together. TODO: check whether Ancestry, Fold3 and
# Newspapers.com throttle separately; if so, split the group.
SITE_GROUPS = {
    'ancestry': ['ancestry.com', 'ancestry.co.uk', 'ancestry.de', 'ancestrycdn.com',
                 'fold3.com', 'newspapers.com'],
    'familysearch': ['familysearch.org'],
    'archion': ['archion.de'],
    'geneanet': ['geneanet.org'],
    # Matricula serves its register images from icar-us.eu (ICARUS), so the
    # viewer claimed that domain separately and the claim outlived the worker
    # (Claude, 14 November 2026).
    'matricula': ['matricula-online.eu', 'icar-us.eu'],
}

# Second-level suffixes under which the registrable domain has three labels.
THREE_LABEL_SUFFIXES = {
    'co.uk', 'gov.uk', 'org.uk', 'ac.uk', 'com.au', 'org.au', 'gov.au',
    'co.nz', 'com.pl', 'org.pl', 'gov.pl',
}

DEFAULT_TTL = 30 * 60


def site_of(url):
    """Return the site key for a URL, or None for pages that no one claims
    (about:blank, chrome:// pages, data: URLs)."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return None
    host = (parsed.hostname or '').lower().rstrip('.')
    if not host:
        return None
    for key, domains in SITE_GROUPS.items():
        for d in domains:
            if host == d or host.endswith('.' + d):
                return key
    labels = host.split('.')
    if len(labels) >= 3 and '.'.join(labels[-2:]) in THREE_LABEL_SUFFIXES:
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def normalize_site(site_or_url):
    """Accept 'familysearch', 'familysearch.org', 'www.ancestry.com' or a URL."""
    s = site_or_url.strip().lower()
    if s in SITE_GROUPS:
        return s
    if '://' not in s:
        s = 'https://' + s
    return site_of(s)


class ClaimError(Exception):
    """A request that would break the one-agent-per-site rule."""


# page_owner() of a spare tab; not a possible agent name.
SPARE = '(bridge spare tab)'


class Claims:
    def __init__(self, path=None, ttl=DEFAULT_TTL, clock=time.time):
        self.path = path
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.RLock()
        self.sites = {}   # site -> {'agent', 'since', 'last_used'}
        self.pages = {}   # pageId -> {'agent', 'last_used'}
        # Spare tabs: tabs that
        # agents released, reset to the marker page and kept for the next
        # claim, so that it need not open a tab (which brings Chrome to the
        # front). A spare tab belongs to no agent and no agent may act on it.
        # Not saved: page IDs change with a daemon restart.
        self.pool = {}    # pageId -> time pooled
        self._load()

    # Persistence. Page IDs belong to one chrome-devtools-mcp process, so only
    # site claims survive a daemon restart; agents claim their tabs again.
    def _load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            self.sites = data.get('sites', {})
        except (OSError, ValueError):
            self.sites = {}

    def _save(self):
        if not self.path:
            return
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'sites': self.sites}, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def _expire(self):
        now = self.clock()
        changed = False
        for site in [s for s, c in self.sites.items() if now - c['last_used'] > self.ttl]:
            del self.sites[site]
            changed = True
        for pid in [p for p, c in self.pages.items() if now - c['last_used'] > self.ttl]:
            del self.pages[pid]
        if changed:
            self._save()

    def _describe(self, site):
        c = self.sites[site]
        since = time.strftime('%H:%M', time.localtime(c['since']))
        return f"{site} is held by {c['agent']} since {since}"

    # Sites
    def claim_site(self, agent, site):
        """Give `site` to `agent`, or raise ClaimError if another agent holds it."""
        if not agent:
            raise ClaimError('a claim needs an agent name')
        if site is None:
            return None
        with self.lock:
            self._expire()
            now = self.clock()
            held = self.sites.get(site)
            if held and held['agent'] != agent:
                raise ClaimError(self._describe(site))
            if held:
                held['last_used'] = now
            else:
                self.sites[site] = {'agent': agent, 'since': now, 'last_used': now}
            self._save()
            return site

    def holder(self, site):
        with self.lock:
            self._expire()
            c = self.sites.get(site)
            return c['agent'] if c else None

    def release(self, agent, site=None):
        """Release one site, or all of the agent's sites and tabs. Returns
        (released sites, page IDs the agent owned on them)."""
        with self.lock:
            self._expire()
            sites = [s for s, c in self.sites.items()
                     if c['agent'] == agent and (site is None or s == site)]
            for s in sites:
                del self.sites[s]
            pages = [p for p, c in self.pages.items() if c['agent'] == agent]
            if site is None:
                for p in pages:
                    del self.pages[p]
            self._save()
            return sites, pages

    # Pages
    def own_page(self, agent, page_id):
        with self.lock:
            self.pages[page_id] = {'agent': agent, 'last_used': self.clock()}

    def page_owner(self, page_id):
        with self.lock:
            self._expire()
            if page_id in self.pool:
                return SPARE
            c = self.pages.get(page_id)
            return c['agent'] if c else None

    def forget_page(self, page_id):
        with self.lock:
            self.pages.pop(page_id, None)
            self.pool.pop(page_id, None)

    # Spare tabs
    def pool_put(self, agent, page_id, max_size):
        """Move `agent`'s tab into the pool. Returns False, and leaves the
        tab with the agent, if the pool is full or the tab is not the agent's."""
        with self.lock:
            if self.pages.get(page_id, {}).get('agent') != agent or len(self.pool) >= max_size:
                return False
            del self.pages[page_id]
            self.pool[page_id] = self.clock()
            return True

    def take_spare(self, agent, extra=()):
        """Give `agent` a spare tab, oldest first, or one of `extra` (unowned
        tabs the caller found, such as spares left by an earlier daemon), and
        return its pageId, or None. Atomic, so no two agents get one tab."""
        if not agent:
            return None
        with self.lock:
            self._expire()
            candidates = sorted(self.pool, key=self.pool.get) + [p for p in extra if p not in self.pool]
            for pid in candidates:
                if pid in self.pages:
                    continue
                self.pool.pop(pid, None)
                self.pages[pid] = {'agent': agent, 'last_used': self.clock()}
                return pid
            return None

    def check_page(self, agent, page_id, current_url, target_url=None):
        """Decide whether `agent` (None for an old client that sends no name)
        may act on page `page_id`, which is now at `current_url`, and, for a
        navigation, go to `target_url`. Raises ClaimError, or renews the
        agent's claims and returns the sites involved.

        Rules:
        - A tab owned by an agent is for that agent only; an agent that uses
          an unowned tab takes ownership of it.
        - A site held by an agent is for that agent only.
        - An agent touching a free site claims it, so the first agent to use a
          site holds it until it releases it or the claim lapses.
        - A caller with no name may use only unowned tabs on unclaimed sites,
          and claims nothing; this keeps old scripts working without letting
          them reach into another agent's work.
        """
        with self.lock:
            self._expire()
            if page_id in self.pool:
                raise ClaimError(f'tab {page_id} is a spare tab kept by the bridge; claim a site to get a tab')
            owner = self.pages.get(page_id, {}).get('agent')
            if owner and owner != agent:
                raise ClaimError(f'tab {page_id} belongs to {owner}')
            sites = []
            for url in (current_url, target_url):
                site = site_of(url)
                if site is None:
                    continue
                held = self.sites.get(site)
                if held and held['agent'] != agent:
                    raise ClaimError(self._describe(site))
                sites.append(site)
            if agent:
                for site in sites:
                    self.claim_site(agent, site)
                # An agent that acts on an unowned tab (one the owner opened, or one
                # from before a restart) adopts it, so no one else can use it.
                self.pages[page_id] = {'agent': agent, 'last_used': self.clock()}
            return sites

    def snapshot(self):
        with self.lock:
            self._expire()
            return {'ttl_seconds': self.ttl,
                    'sites': dict(self.sites),
                    'pages': {str(p): c for p, c in self.pages.items()},
                    'spare_tabs': sorted(self.pool)}
