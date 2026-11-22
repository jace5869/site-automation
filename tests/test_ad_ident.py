#!/usr/bin/env python3
"""The shared AD identity rules and the five review rounds, through the portal API."""
import datetime
import json
import os
import sys
import unittest
from unittest.mock import Mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'plugins', 'module_utils'), os.path.join(ROOT, 'plugins', 'filter'),
                os.path.join(ROOT, 'roles', 'ad_report', 'library')]
import site_ad_ident as ident
import site_ad_reader as reader
import site_ad_swis as swis
import ad_filters as filters
import ad_repl_filters as matrix
import ad_swis_filters as swfilters
import test_portal_ad as portal

NC = 'CN=Configuration,DC=corp,DC=test'
NOW = datetime.datetime(2026, 11, 22, 12)
STAMP = '2026-11-22T12:00:00Z'


def dc(name='DC01', domain='east.test', site='SITEA0101'):
    server = 'CN=%s,CN=Servers,CN=%s,CN=Sites,%s' % (name, site, NC)
    return dict(name=name, dc=name, domain=domain, fqdn=name.lower() + '.' + domain, site=site,
                server_dn=server, dsa_dn='CN=NTDS Settings,' + server, in_ad=True, bind={'ok': True})


class Rules(unittest.TestCase):
    def test_guid_before_dn_before_qualified_name(self):
        rec = dict(dc(), objectGUID='AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA')
        self.assertEqual(ident.dc_id(rec), rec['objectGUID'].lower())
        rec.pop('objectGUID')
        self.assertEqual(ident.dc_id(dict(rec, dsa_guid="BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB")), "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        self.assertEqual(ident.dc_id(rec), rec['dsa_dn'].lower())
        self.assertEqual(ident.dc_id({'dc': 'DC01', 'domain': 'EAST.TEST.'}), 'east.test|dc01')
        self.assertEqual(ident.dc_id({'dc': 'DC01'}), '|dc01')

    def test_dn_alias_resolves_to_guid_without_short_name_join(self):
        known = dict(dc(), dsa_guid='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        alias = ident.source({'partner': 'DC01', 'partner_dn': known['dsa_dn']})
        self.assertEqual(ident.dc_id(ident.resolve(alias, [known])), known['dsa_guid'])
        replacement = dict(known, dsa_guid='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
        self.assertIsNone(ident.resolve(replacement, [known]))

    def test_sites_are_exact(self):
        self.assertEqual(ident.site_id({'site': 'SITEA0101'}), ident.site_id({'site': 'sitea0101'}))
        self.assertNotEqual(ident.site_id({'site': 'SITEA0101'}), ident.site_id({'site': 'SITEA01010'}))
        self.assertEqual(ident.site_id({'site_guid': 'ABC', 'site_dn': 'CN=X', 'site': 'Y'}), 'abc')
        self.assertEqual(ident.site_id({'site_dn': 'CN=X', 'site': 'Y'}), 'cn=x')
        self.assertEqual(ident.site_id({"objectGUID": "ABC", "site": "Y"}), "abc")
        self.assertEqual(ident.dc_site({"objectGUID": "DC-GUID", "site": "Y"}), "y")

    def test_domains_are_exact(self):
        self.assertEqual(ident.domain_id('CORP.TEST.'), 'corp.test')
        self.assertNotEqual(ident.domain_id('east.corp.test'), ident.domain_id('corp.test'))

    def test_same_dc_uses_identity(self):
        a, b = dc(), dc(domain='west.test', site='SITEB0201')
        self.assertFalse(ident.same_dc(a, b))
        self.assertTrue(ident.same_dc(a, dict(a, name='RENAMED')))

    def test_links_keep_both_endpoints_and_partition(self):
        a = dict(to='DC02', domain='east.test', partner_dn=dc()['dsa_dn'], nc=NC)
        for b in [dict(a, domain='west.test'), dict(a, partner_dn=dc(site='SITEB0201')['dsa_dn']), dict(a, nc=NC + ',DC=other')]:
            self.assertNotEqual(ident.link_id(a), ident.link_id(b))
        self.assertEqual(ident.link_id(a)[1], dc()['dsa_dn'].lower())

    def test_short_names_require_the_whole_population(self):
        a, b = dc(), dc(domain='west.test', site='SITEB0201')
        self.assertIsNone(ident.resolve({'dc': 'DC01'}, [a, b]))
        self.assertEqual(ident.resolve({'dc': 'DC01'}, [a]), a)
        self.assertIsNone(swis.match([a], [{'node_id': 1, 'name': 'DC01', 'status': 'Up'}], [b])[0][0])

    def test_invocation_id_is_not_a_dc_guid(self):
        inv = '11111111-1111-1111-1111-111111111111'
        self.assertEqual(ident.dc_id({'dc': 'DC01', 'domain': 'east.test', 'invocation_id': inv}), 'east.test|dc01')
        self.assertFalse(ident.source({'partner': 'dc01.east.test', 'partner_id': inv, 'invocation_id': inv})['dsa_guid'])


class ReviewE(unittest.TestCase):
    def make_reader(self):
        r = reader.Reader({})
        r.dsa_name = {}
        self.addCleanup(r.close)
        return r

    def link(self, r, src, fails=3):
        return r._link(NC, dict(partner_dn=src['dsa_dn'], partner_guid=src.get('dsa_guid', ''),
                               last_success='2026-10-01T00:00:00Z' if fails else STAMP,
                               last_attempt=STAMP, failures=fails, result=8524 if fails else 0))

    def api(self, repl, observations):
        ad = dict(report={'status': 'warning'}, dcs=[dict(d, read=True) for d in observations], repl=repl)
        live = swfilters.ad_live_block(ad, swfilters.ad_swis_dcs(ad['dcs']))
        payload = portal.dbf.ad_db_payload(ad, live)
        self.assertEqual(payload['why'], '')
        db = portal.Lite()
        self.addCleanup(db.db.close)
        portal.adb.write(db, NOW, 77, 12, payload)
        model = portal.sp.AdDb(portal.Site(self).cfg(), connect=lambda: db).read(now=NOW)
        app = portal.sp.AdLive(portal.Site(self).cfg(), lambda *_: None, db=Mock(read=lambda *args, **kwargs: model))
        app.read_db()
        answer = json.loads(app.answer()[1])['db']['model']
        return payload, model, answer

    def test_e1_same_short_partner_is_not_self(self):
        r, target, src = self.make_reader(), dc(), dc(domain='west.test', site='SITEB0201')
        target['repl'] = {'links': [self.link(r, src)]}
        repl = filters._repl_reader([target], NOW, 60, 'east.test')
        self.assertEqual(len(repl['links']), 1)
        payload, model, answer = self.api(repl, [target])
        self.assertEqual(len(answer['links']), 1)
        self.assertNotEqual(answer['links'][0]['src_id'], answer['links'][0]['dst_id'])
        self.assertLess(answer['run']['repl_read'], answer['run']['repl_dcs'])

    def test_e2_forest_discovery_precedes_domain_filter(self):
        r = self.make_reader()
        r.domain = 'corp.test'
        servers = [dc(domain=d, site=s) for d, s in [('corp.test', 'SITEA0101'), ('east.corp.test', 'SITEB0201'), ('west.test', 'SITEC0301')]]
        def base(_c, dn, attrs):
            if not dn:
                return {'attributes': {'defaultNamingContext': ['DC=corp,DC=test'], 'configurationNamingContext': [NC]}}
            return {'attributes': {}}
        def search(_c, _dn, query, _attrs):
            if query == '(objectClass=server)':
                return [{'dn': d['server_dn'], 'attributes': {'dNSHostName': [d['fqdn']]}} for d in servers]
            if query == '(objectClass=nTDSDSA)':
                return [{'dn': d['dsa_dn'], 'attributes': {}} for d in servers]
            return []
        r.base, r.search = base, search
        out = {}
        r.read_domain(None, out)
        self.assertEqual(len(out['known_dcs']), 3)
        self.assertEqual([d['domain'] for d in out['dcs_ad']], ['corp.test'])
        target = dict(out['dcs_ad'][0], in_ad=True, bind={'ok': True}, repl={'links': []})
        repl = filters._repl_reader([target], NOW, 60, 'corp.test', out['known_dcs'])
        payload, model, answer = self.api(repl, [dict(target, dc='DC01')])
        self.assertEqual(len(answer['dcs']), 1)
        self.assertEqual((answer['run']['repl_read'], answer['run']['repl_dcs']), (1, 3))
        known = payload['live']['known']
        self.assertIsNone(swis.match(payload['live']['dcs'], [{'node_id': 1, 'name': 'DC01', 'status': 'Up'}], known)[0][0])

    def admin(self, site='', failing=False):
        rows = [dict(source='DC01', source_site=s, nc=NC, last_success=STAMP if i else '2026-10-01T00:00:00Z',
                     failures=3 if failing and i == 0 else 0) for i, s in enumerate(['SITEA0101', 'SITEB0201'])]
        dcs = [dict(_name='DC01', _domain='east.test', _ok=True, data={'site': site, 'repl': []}),
               dict(_name='DC02', _domain='east.test', _ok=True, data={'site': 'SITEA0101', 'repl': rows})]
        return filters._repl_admin(dcs, [], NOW)

    def test_e3_unknown_site_cannot_cover_two_sources(self):
        repl = self.admin()
        self.assertEqual(sum(d['read'] for d in repl['dcs']), 2)
        self.assertGreaterEqual(len(repl['dcs']), 3)
        self.assertEqual(len({ident.link_id(x) for x in repl['links']}), 2)
        _, _, answer = self.api(repl, [dict(dc=n, domain='east.test') for n in ['DC01', 'DC02']])
        self.assertLess(answer['run']['repl_read'], answer['run']['repl_dcs'])

    def test_e4_source_site_survives_report_and_database(self):
        repl = self.admin('SITEA0101', True)
        view = matrix.ad_repl_view(repl)
        self.assertEqual(view['counts']['links'], 2)
        self.assertEqual(view['sections'][0]['rows'][0][0], 'DC01 (SITEA0101)')
        payload, _, answer = self.api(repl, [dict(dc=n, domain='east.test') for n in ['DC01', 'DC02']])
        self.assertEqual(next(d for d in payload['dcs'] if d['dc_id'] == 'east.test|dc01')['site'], 'SITEA0101')
        self.assertEqual(len(answer['links']), 1)

    def test_e5_failed_partition_named_in_both_reports(self):
        r, target, src = self.make_reader(), dc(), dc('DC02')
        def base(_c, nc, _attrs):
            if nc == NC:
                raise LookupError('insufficientAccessRights')
            return {'attributes': {}, 'raw_attributes': {}}
        r.base = base
        repl_read = r.read_repl(None, {'naming_contexts': ['DC=east,DC=test', NC], 'dcs_ad': [target, src]})
        repl_read.update(links=[self.link(r, src, 0)], why='')
        target['repl'] = repl_read
        report = filters.ad_reader_report(dict(ok=True, domain='east.test', read_at=STAMP, dcs=[target], known_dcs=[src]))
        section = next(s for s in report['report']['sections'] if s['title'] == 'Inbound replication')
        self.assertIn('Partition queries failed: Configuration', ' '.join(section['lines']))
        view = matrix.ad_repl_view(report['repl'])
        self.assertNotIn('Every link replicates on time', view['sections'][0]['text'])
        self.assertIn('Configuration', view['sections'][0]['text'])
        _, _, answer = self.api(report['repl'], [target])
        self.assertEqual(answer['run']['repl_read'], 0)

    def test_guid_sites_and_dc_rename_keep_their_identity(self):
        a = dict(dc(), dsa_guid='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', site_guid='11111111-1111-1111-1111-111111111111')
        b = dict(dc('DC02'), dsa_guid='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', site_guid='22222222-2222-2222-2222-222222222222')
        repl = filters._repl_reader([a, b], NOW, 60, 'east.test')
        view = matrix.ad_repl_view(repl)
        self.assertEqual(len(view['web_sections'][0]['matrix']['groups']), 2)
        self.assertEqual(len(view['artifact']['sites']), 2)
        db = portal.Lite()
        self.addCleanup(db.db.close)
        for i, name in enumerate(['DC01', 'DC03']):
            rec = dict(a, dc=name, name=name, status='critical', read=True)
            portal.adb.write(db, NOW + datetime.timedelta(minutes=i), 77 + i, 12, {'dcs': [rec], 'links': []})
        model = portal.sp.AdDb(portal.Site(self).cfg(), connect=lambda: db).read(now=NOW + datetime.timedelta(minutes=1))
        self.assertEqual(len(model['dcs']), 1)
        self.assertEqual(model['dcs'][0]['dc'], 'DC03')
        self.assertEqual(model['dcs'][0]['runs_since'], 2)
        self.assertEqual(model['worst7']['critical'], 2)

    def test_all_reviews_reader_to_api(self):
        # A/B: two domains' DC01; C: two qualified sources; D: two DSA DNs;
        # E: same-named receiver and source. Failure first, then a healthy link.
        cases = [('a', 'DC02', 'east.test', 'west.test'), ('b', 'DC02', 'corp.test', 'east.corp.test'),
                 ('c', 'DC02', 'east.test', 'west.test'), ('d', 'DC02', 'east.test', 'east.test'),
                 ('e', 'DC01', 'east.test', 'west.test')]
        for review, receiver, east, west in cases:
            with self.subTest(review=review):
                r = self.make_reader()
                target = dc(receiver, east, 'SITEC0301')
                sources = [dc(domain=east), dc(domain=west, site='SITEB0201')]
                r.dsa_name = {d['dsa_dn'].lower(): d['fqdn'] for d in sources}
                target['repl'] = {'links': [self.link(r, sources[0]), self.link(r, sources[1], 0)]}
                report = filters.ad_reader_report(dict(ok=True, domain=east, read_at=STAMP, dcs=[target], known_dcs=sources))
                payload, model, answer = self.api(report['repl'], [target])
                self.assertEqual(len(payload['links']), 2)
                self.assertEqual(len({portal.adb.link_key(x) for x in payload['links']}), 2)
                self.assertEqual(len(answer['links']), 1)
                self.assertEqual(answer['links'][0]['now'], 'critical')
                self.assertEqual([d['dc_id'] for d in answer['dcs']], [ident.dc_id(target)])
                self.assertEqual((answer['run']['repl_read'], answer['run']['repl_dcs']), (1, 3))
                self.assertIsNone(swis.match([sources[0]], [{'node_id': 1, 'name': 'DC01', 'status': 'Up'}], sources)[0][0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
