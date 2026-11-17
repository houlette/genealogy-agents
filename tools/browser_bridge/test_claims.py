"""Tests for claims.py (no browser needed): python3 tools/browser_bridge/test_claims.py"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claims import SPARE, ClaimError, Claims, normalize_site, site_of  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


FS = 'https://www.familysearch.org/ark:/61903/3:1:ABC'
ANC = 'https://www.ancestry.com/search/'


class SiteOfTest(unittest.TestCase):
    def test_sites(self):
        self.assertEqual(site_of(FS), 'familysearch')
        self.assertEqual(site_of('https://ident.familysearch.org/login'), 'familysearch')
        self.assertEqual(site_of('https://www.fold3.com/x'), 'ancestry')
        self.assertEqual(site_of('https://www.newspapers.com/image/1'), 'ancestry')
        self.assertEqual(site_of('https://www.scotlandspeople.gov.uk/'), 'scotlandspeople.gov.uk')
        self.assertEqual(site_of('https://geneteka.genealodzy.pl/'), 'genealodzy.pl')
        self.assertEqual(site_of('https://data.matricula-online.eu/de/'), 'matricula')
        self.assertEqual(site_of('https://images.icar-us.eu/iiif/x.jpg'), 'matricula')
        self.assertEqual(normalize_site('matricula-online.eu'), 'matricula')
        self.assertIsNone(site_of('about:blank'))
        self.assertIsNone(site_of('chrome://newtab/'))
        self.assertEqual(normalize_site('familysearch'), 'familysearch')
        self.assertEqual(normalize_site('archion.de'), 'archion')
        self.assertEqual(normalize_site('https://www.fold3.com/'), 'ancestry')


class ClaimsTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Claims(None, ttl=60, clock=self.clock)

    def test_one_agent_per_site(self):
        self.c.claim_site('gemini', 'familysearch')
        self.c.claim_site('gemini', 'familysearch')  # renewing is fine
        with self.assertRaisesRegex(ClaimError, 'held by gemini'):
            self.c.claim_site('sol', 'familysearch')
        self.c.claim_site('sol', 'ancestry')  # a different site is free

    def test_tab_ownership(self):
        self.c.own_page('gemini', 5)
        with self.assertRaisesRegex(ClaimError, 'tab 5 belongs to gemini'):
            self.c.check_page('sol', 5, 'about:blank')
        with self.assertRaisesRegex(ClaimError, 'belongs to gemini'):
            self.c.check_page(None, 5, 'about:blank')  # old client, no name

    def test_navigation_into_held_site_refused(self):
        self.c.claim_site('gemini', 'familysearch')
        self.c.own_page('sol', 7)
        with self.assertRaisesRegex(ClaimError, 'held by gemini'):
            self.c.check_page('sol', 7, ANC, target_url=FS)

    def test_followed_link_into_held_site_refused(self):
        # Sol's tab drifted onto FamilySearch through a link click in page JS.
        self.c.claim_site('gemini', 'familysearch')
        self.c.own_page('sol', 7)
        with self.assertRaisesRegex(ClaimError, 'held by gemini'):
            self.c.check_page('sol', 7, FS)

    def test_first_use_claims_and_adopts(self):
        self.c.check_page('sol', 9, ANC)
        self.assertEqual(self.c.holder('ancestry'), 'sol')
        self.assertEqual(self.c.page_owner(9), 'sol')

    def test_nameless_caller_claims_nothing(self):
        self.c.check_page(None, 3, ANC)
        self.assertIsNone(self.c.holder('ancestry'))
        self.assertIsNone(self.c.page_owner(3))
        self.c.claim_site('sol', 'ancestry')
        with self.assertRaisesRegex(ClaimError, 'held by sol'):
            self.c.check_page(None, 3, ANC)

    def test_lapse_and_renewal(self):
        self.c.claim_site('gemini', 'familysearch')
        self.c.own_page('gemini', 5)
        self.clock.t += 50
        self.c.check_page('gemini', 5, FS)  # use renews the claim
        self.clock.t += 50
        with self.assertRaises(ClaimError):
            self.c.claim_site('sol', 'familysearch')
        self.clock.t += 61
        self.c.claim_site('sol', 'familysearch')  # lapsed, so free
        self.assertIsNone(self.c.page_owner(5))

    def test_release(self):
        self.c.claim_site('gemini', 'familysearch')
        self.c.claim_site('gemini', 'archion')
        self.c.own_page('gemini', 5)
        sites, pages = self.c.release('gemini', 'archion')
        self.assertEqual(sites, ['archion'])
        self.assertEqual(self.c.holder('familysearch'), 'gemini')
        sites, pages = self.c.release('gemini')
        self.assertEqual(sites, ['familysearch'])
        self.assertEqual(pages, [5])
        self.assertIsNone(self.c.page_owner(5))
        self.c.claim_site('sol', 'familysearch')

    def test_persistence_keeps_sites_not_tabs(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'claims.json')
            c1 = Claims(path, ttl=60, clock=self.clock)
            c1.claim_site('gemini', 'familysearch')
            c1.own_page('gemini', 5)
            c2 = Claims(path, ttl=60, clock=self.clock)
            self.assertEqual(c2.holder('familysearch'), 'gemini')
            self.assertIsNone(c2.page_owner(5))


class SpareTabTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Claims(clock=self.clock)

    def test_release_into_pool_and_reuse(self):
        self.c.own_page('a', 7)
        self.assertTrue(self.c.pool_put('a', 7, 3))
        self.assertEqual(self.c.page_owner(7), SPARE)
        self.assertEqual(self.c.take_spare('b'), 7)
        self.assertEqual(self.c.page_owner(7), 'b')
        self.assertIsNone(self.c.take_spare('c'))

    def test_spare_tab_is_off_limits(self):
        self.c.own_page('a', 7)
        self.c.pool_put('a', 7, 3)
        with self.assertRaises(ClaimError):
            self.c.check_page('b', 7, FS)
        with self.assertRaises(ClaimError):
            self.c.check_page(None, 7, 'https://example.com/#bridge-spare-1')

    def test_only_owner_can_pool_and_pool_is_capped(self):
        self.c.own_page('a', 1)
        self.assertFalse(self.c.pool_put('b', 1, 3))
        self.assertEqual(self.c.page_owner(1), 'a')
        for pid in (2, 3, 4):
            self.c.own_page('a', pid)
            self.c.pool_put('a', pid, 2)
        self.assertEqual(self.c.snapshot()['spare_tabs'], [2, 3])
        self.assertEqual(self.c.page_owner(4), 'a')

    def test_oldest_first_and_no_double_give(self):
        for pid in (5, 6):
            self.c.own_page('a', pid)
            self.c.pool_put('a', pid, 3)
            self.clock.t += 10
        got = {self.c.take_spare('x'), self.c.take_spare('y')}
        self.assertEqual(got, {5, 6})
        self.assertEqual(self.c.page_owner(5), 'x')

    def test_unowned_extra_tabs_after_restart(self):
        self.c.own_page('a', 9)
        self.assertEqual(self.c.take_spare('b', extra=[9, 10]), 10)
        self.assertIsNone(self.c.take_spare('', extra=[11]))

    def test_spares_do_not_expire_and_forget_drops(self):
        self.c.own_page('a', 7)
        self.c.pool_put('a', 7, 3)
        self.clock.t += 10 * 3600
        self.assertEqual(self.c.page_owner(7), SPARE)
        self.c.forget_page(7)
        self.assertIsNone(self.c.page_owner(7))

if __name__ == '__main__':
    unittest.main()
