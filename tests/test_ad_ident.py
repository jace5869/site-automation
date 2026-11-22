#!/usr/bin/env python3
"""One-domain identity examples, from AD discovery through the report and portal DB."""
import datetime
import json
import re
import os
import sys
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

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

DOMAIN = 'ad.example.mil'
NC = 'CN=Configuration,DC=ad,DC=example,DC=mil'
NOW = datetime.datetime(2026, 11, 22, 12)
STAMP = '2026-11-22T12:00:00Z'
GUID1 = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
GUID2 = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


def dc(name='DC01', site='SITEA0101', guid=GUID1):
    server = 'CN=%s,CN=Servers,CN=%s,CN=Sites,%s' % (name, site, NC)
    return dict(name=name, dc=name, domain=DOMAIN, fqdn=name.lower() + '.example.mil', site=site,
                server_dn=server, dsa_dn='CN=NTDS Settings,' + server, dsa_guid=guid,
                in_ad=True, bind={'ok': True}, repl={'links': [], 'source': 'repsFrom'})


class OneDomain(unittest.TestCase):
    def db(self):
        db = portal.Lite()
        self.addCleanup(db.db.close)
        return db

    def api(self, report, sw=None):
        live = swfilters.ad_live_block(report, swfilters.ad_swis_dcs(report.get('dcs')))
        payload = portal.dbf.ad_db_payload(report, live, {'solarwinds': sw or {}})
        db = self.db()
        portal.adb.write(db, NOW, 77, 12, payload)
        model = portal.sp.AdDb(None, connect=lambda: db).read(now=NOW)
        app = portal.sp.AdLive(portal.Site(self).cfg(), lambda *_: None, db=Mock(read=lambda **kw: model))
        app.read_db()
        return payload, model, json.loads(app.answer()[1])['db']['model']

    def report(self, dcs, known=None, **kwargs):
        return filters.ad_reader_report(dict(ok=True, domain=DOMAIN, read_at=STAMP, dcs=dcs,
                                             known_dcs=known if known is not None else dcs, discovered=True, **kwargs))

    def test_host_label_not_guid_dn_domain_or_site(self):
        a = dc()
        for rec in [a, dict(a, dsa_guid=GUID2, site='SITEB0201'), {'dc': 'DC01', 'domain': DOMAIN},
                    {'fqdn': 'dc01.example.mil', 'name': 'old'}]:
            self.assertEqual(ident.dc_id(rec), 'dc01')
        self.assertEqual(ident.domain({'fqdn': a['fqdn']}), '')
        self.assertEqual(ident.dc_site({'site': 'SITEA0101', 'site_guid': GUID1}), 'sitea0101')
        self.assertEqual(ident.dc_site({'site_dn': 'CN=SITEA0101'}), '')
        self.assertNotEqual(ident.dc_site({'site': 'SITEA0101'}), ident.dc_site({'site': 'SITEA01010'}))

    def test_disjoint_namespace_matched_stored_displayed(self):
        a = dc()
        node = {'node_id': 101, 'name': a['fqdn'], 'dns': '', 'status': 'Up'}
        matched = swis.match([a], [node])
        self.assertEqual(matched[0][0], node)
        sw = {'dcs': swis._dc_rows([a], matched, {}, {}, {}, False)}
        payload, model, answer = self.api(self.report([a]), sw)
        self.assertFalse(payload['why'])
        self.assertEqual([(d['dc'], d['dns_host'], d['node_id']) for d in answer['dcs']], [('dc01', a['fqdn'], 101)])
        self.assertEqual(answer['run']['repl_read'], 1)
        self.assertEqual(model['live']['dcs'][0]['node_id'], 101)

    def test_reader_domain_from_ntds_not_dns_suffix(self):
        r = reader.Reader({})
        self.addCleanup(r.close)
        r.domain = DOMAIN
        a, b = dc(), dc('DC02', 'SITEB0201', GUID2)
        def base(_c, dn, attrs):
            return {'attributes': {'defaultNamingContext': ['DC=ad,DC=example,DC=mil'],
                                    'configurationNamingContext': [NC]}} if not dn else {'attributes': {}}
        def search(_c, _dn, query, attrs):
            if query == '(objectClass=server)':
                return [{'dn': d['server_dn'], 'attributes': {'dNSHostName': [d['fqdn']]}} for d in [a, b]]
            if query == '(objectClass=nTDSDSA)':
                return [{'dn': d['dsa_dn'], 'attributes': {'objectGUID': [d['dsa_guid']],
                         'msDS-HasDomainNCs': ['DC=ad,DC=example,DC=mil']}} for d in [a, b]]
            return []
        r.base, r.search = base, search
        out = {}
        r.read_domain(None, out)
        self.assertEqual([d['domain'] for d in out['dcs_ad']], [DOMAIN, DOMAIN])
        self.assertEqual([d['dc_id'] for d in out['dcs_ad']], ['dc01', 'dc02'])
        self.assertFalse(ident.unsupported(out))

    def test_second_domain_and_forest_refused_everywhere(self):
        for extra in [dict(dc('DC02'), domain='other.example.mil'), dict(dc('DC02'), forest='other.example.mil')]:
            with self.subTest(extra=extra):
                report = self.report([dict(dc(), forest=DOMAIN)], [dict(dc(), forest=DOMAIN), extra])
                payload, model, answer = self.api(report)
                self.assertEqual(report['report']['sections'][0]['text'], ident.REFUSAL)
                self.assertEqual((payload['status'], payload['why']), ('unknown', ident.REFUSAL))
                self.assertEqual(answer['run']['why'], ident.REFUSAL)
                self.assertEqual((answer['dcs'], answer['links']), ([], []))
                self.assertTrue(model['live']['unsupported'])

    def test_partner_domain_nc_is_evidence_but_node_name_is_not(self):
        a = dc()
        a['repl']['links'] = [{'partner': 'DC02', 'nc': 'DC=other,DC=example,DC=mil'}]
        self.assertEqual(self.report([a])['why'], ident.REFUSAL)
        nodes = [{'node_id': 101, 'name': 'dc01.other.example.mil'}]
        self.assertEqual(swis.match([dc()], nodes)[0][0], nodes[0])
        self.assertFalse(ident.unsupported({'domain': DOMAIN, 'live': {'dcs': nodes}}))

    def test_writer_refuses_mixed_domain_rows(self):
        db = self.db()
        portal.adb.write(db, NOW, 77, 12, {'status': 'ok', 'dcs': [dc(), dict(dc('DC02'), domain='other.example.mil')]})
        self.assertEqual(db.db.execute('SELECT status, why FROM ad_run').fetchone(), ('unknown', ident.REFUSAL))
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM ad_dc').fetchone()[0], 0)

    def test_page_refuses_mixed_domain_database_rows(self):
        db = self.db()
        portal.adb.write(db, NOW, 77, 12, {'status': 'ok', 'dcs': [dict(dc(), status='ok'), dict(dc('DC02'), status='ok')]})
        db.db.execute("UPDATE ad_dc SET domain='other.example.mil' WHERE dc='dc02'")
        model = portal.sp.AdDb(None, connect=lambda: db).read(now=NOW)
        self.assertEqual((model['run']['status'], model['run']['why']), ('unknown', ident.REFUSAL))
        self.assertEqual(model['dcs'], [])

    def test_stale_dsa_is_one_dc_and_failure_survives(self):
        a = dc()
        old = dict(dc(guid=GUID2), site='SITEB0201')
        a['repl']['links'] = [{'partner': 'dc01.example.mil', 'partner_guid': GUID2, 'nc': NC,
                              'last_success': '2026-10-01T00:00:00Z', 'failures': 3, 'result': 8524},
                             {'partner': 'dc01.example.mil', 'partner_guid': GUID1, 'nc': NC,
                              'last_success': STAMP, 'failures': 0}]
        report = self.report([a], [old, a])
        self.assertEqual(len(report['repl']['dcs']), 1)
        self.assertTrue(report['repl']['dcs'][0]['stale_dsa'])
        self.assertIn('Two NTDS objects for DC01 - stale DSA, metadata cleanup', json.dumps(report))
        payload, model, answer = self.api(report)
        self.assertEqual(len(payload['links']), 1)
        self.assertEqual((answer['links'][0]['frm'], answer['links'][0]['to'], answer['links'][0]['now']), ('dc01', 'dc01', 'critical'))
        self.assertTrue(answer['dcs'][0]['stale_dsa'])

    def test_moved_dc_has_continuous_history(self):
        db = self.db()
        for i, site in enumerate(['SITEA0101', 'SITEB0201']):
            portal.adb.write(db, NOW + datetime.timedelta(minutes=i), 77 + i, 12,
                             {'dcs': [dict(dc(site=site), status='critical', read=True)]})
        model = portal.sp.AdDb(None, connect=lambda: db).read(now=NOW + datetime.timedelta(minutes=1))
        self.assertEqual(len(model['dcs']), 1)
        self.assertEqual((model['dcs'][0]['dc'], model['dcs'][0]['site'], model['dcs'][0]['runs_since']), ('dc01', 'SITEB0201', 2))
        self.assertEqual(model['worst7']['critical'], 2)

    def test_site_lag_does_not_add_a_dc_or_hide_the_worst_link(self):
        dcs = [{'_name': name, '_domain': DOMAIN, '_ok': True, 'data': {'site': 'SITEA0101', 'repl': []}}
               for name in ['DC01', 'DC02']]
        dcs[1]['data']['repl'] = [{'source': 'DC01', 'source_site': site, 'nc': NC,
                                  'last_success': STAMP if i else '2026-10-01T00:00:00Z', 'failures': 0 if i else 3}
                                 for i, site in enumerate(['SITEA0101', 'SITEB0201'])]
        repl = filters._repl_admin(dcs, [{'data': {'dns': DOMAIN, 'dcs': [dc(), dc('DC02')]}}], NOW)
        self.assertEqual(len(repl['dcs']), 2)
        self.assertEqual(len({ident.link_id(x) for x in repl['links']}), 1)
        view = matrix.ad_repl_view(repl)
        self.assertEqual(view['counts']['links'], 1)
        self.assertIn('SITEA0101', view['sections'][0]['rows'][0][0])

    def test_unknown_dsa_not_in_coverage(self):
        r = reader.Reader({})
        self.addCleanup(r.close)
        r.dsa_name = {}
        link = r._link(NC, {'partner_guid': GUID2, 'last_success': '', 'last_attempt': STAMP, 'failures': 3, 'result': 8524})
        a = dc()
        a['repl']['links'] = [link]
        report = self.report([a])
        self.assertEqual(len(report['repl']['dcs']), 1)
        self.assertEqual(report['repl']['links'][0]['src_id'], 'dsa:' + GUID2)
        self.assertIn('unknown DSA', json.dumps(report))
        payload, model, answer = self.api(report)
        self.assertEqual((payload['repl_read'], payload['repl_dcs']), (1, 1))
        self.assertEqual(answer['links'][0]['frm'], 'unknown DSA')
        admin = filters._repl_admin([{'_name': 'DC01', '_domain': DOMAIN, '_ok': True, 'data': {'repl': [dict(link, source='')]}}], [], NOW)
        self.assertEqual(admin['links'][0]['frm'], 'unknown DSA')
        self.assertEqual(len(admin['dcs']), 1)

    def test_unpolled_dc_is_visible_and_in_denominator(self):
        report = self.report([dc()], [dc(), dc('DC02', 'SITEB0201', GUID2)])
        payload, model, answer = self.api(report)
        self.assertEqual((payload['repl_read'], payload['repl_dcs']), (1, 2))
        row = next(d for d in answer['dcs'] if d['dc'] == 'dc02')
        self.assertEqual((row['now'], row['worst']), ('unknown', 'not polled'))
        self.assertIn('DC02 (not polled)', answer['run']['coverage'])
        self.assertIn(['DC02', 'SITEB0201', 'not polled'], report['report']['sections'][-1]['rows'])
        self.assertEqual(report['report']['sections'][-1]['row_status'], ['unknown'])
        self.assertFalse(any(p.get('object') == 'DC02' for p in report['problems']))

    def test_coverage_words_include_each_unread_reason(self):
        a, b, c = dc(), dc('DC03'), dc('DC05')
        c['repl']['not_granted'] = [NC]
        report = self.report([a, c], [a, b, c])
        expected = 'Replication read in full on 1 of 3 DCs; not read: DC03 (not polled), DC05 (Configuration: no right)'
        self.assertEqual(report['report']['sections'][-1]['text'], expected)
        self.assertEqual(self.api(report)[2]['run']['coverage'], expected)

    def test_unpolled_stale_dsa_keeps_both_facts(self):
        report = self.report([dc()], [dc(), dc('DC02'), dc('DC02', guid=GUID2)])
        row = next(d for d in self.api(report)[2]['dcs'] if d['dc'] == 'dc02')
        self.assertEqual((row['now'], row['worst'], row['stale_dsa']), ('unknown', 'not polled', True))

    def test_solarwinds_age_is_the_samples_not_the_portal_reads(self):
        app = portal.live(portal.fill(['ok']), portal.Site(self))
        app.read_db()
        sample = portal.sw_answer()
        sample['read_at'] = portal.utc(30)
        app.source = lambda _: sample
        app.sw = app.read_sw(app.sw_source())
        sw = json.loads(app.answer()[1])['sw']
        self.assertTrue(1800 <= sw['age'] < 1860, sw)
        self.assertTrue(sw['stale'])

    def test_discovery_failure_is_unknown_coverage(self):
        report = filters.ad_reader_report({'ok': False, 'domain': DOMAIN, 'why': 'LDAP refused'})
        payload, model, answer = self.api(report)
        self.assertIn(ident.COVERAGE_UNKNOWN, json.dumps(report))
        self.assertEqual(answer['run']['coverage'], ident.COVERAGE_UNKNOWN)
        self.assertNotIn('0 of 0', json.dumps(answer))
        self.assertNotEqual(answer['hist'][0]['status'], 'ok')

    def test_partial_partition_names_in_report_matrix_and_page(self):
        a = dc()
        a['repl'].update(failed=[NC], links=[{'partner': 'DC02', 'nc': 'DC=ad,DC=example,DC=mil', 'last_success': STAMP}])
        report = self.report([a], [a, dc('DC02', guid=GUID2)])
        self.assertIn('Partition queries failed: Configuration', json.dumps(report))
        self.assertNotIn('Every link replicates on time', json.dumps(matrix.ad_repl_view(report['repl'])))
        payload, model, answer = self.api(report)
        self.assertEqual(answer['run']['repl_read'], 0)
        self.assertIn('Configuration', answer['run']['coverage'])

    def test_row_conversion_failure_is_unknown_in_history(self):
        report = self.report([dc()])
        report['repl']['links'] = [{'frm': 'DC02', 'to': 'DC01', 'failures': 'unknown'}]
        payload, model, answer = self.api(report)
        self.assertEqual(payload['status'], 'unknown')
        self.assertEqual(answer['hist'][0]['status'], 'unknown')
        self.assertIn('could not make', answer['hist'][0]['why'])

    def test_match_priority_ambiguity_and_two_claimants(self):
        a, b = dict(dc(), ip='192.0.2.10'), dict(dc('DC02'), ip='192.0.2.10')
        nodes = [{'node_id': 1, 'name': a['fqdn']}, {'node_id': 2, 'name': 'dc01.other.example.mil'}]
        self.assertEqual(swis.match([a], nodes)[0][0]['node_id'], 1)
        nodes = [{'node_id': 1, 'name': 'dc01'}, {'node_id': 2, 'name': 'dc01'}]
        self.assertIsNone(swis.match([a], nodes)[0][0])
        self.assertIn('2', swis.match([a], nodes)[0][2])
        nodes = [{'node_id': 3, 'name': 'dc01', 'ip': '192.0.2.10'}]
        both = swis.match([a, b], nodes)
        self.assertEqual([both[i][0] for i in range(2)], [None, None])
        self.assertTrue(all('claimed by dc01, dc02' in both[i][2] for i in range(2)))

    def test_portal_reads_saved_node_id_after_node_rename(self):
        client = Mock()
        client.paged.return_value = ([{'NodeID': 101, 'Caption': 'renamed', 'Status': 2},
                                     {'NodeID': 102, 'Caption': 'dc01.example.mil', 'Status': 1}], False)
        with patch.object(swis, '_has', return_value=set()), patch.object(swis, '_live_ifaces', return_value={}):
            result = swis.from_live([dict(dc(), node_id=101)], {'by_node_id': True}, client)
        self.assertEqual(result['dcs'][0]['node_id'], 101)
        self.assertEqual(result['dcs'][0]['status'], 'Down')
        self.assertIn('WHERE N.NodeID IN (101)', client.paged.call_args[0][0])
        client.paged.return_value = ([], False)
        missing = swis.from_live([dict(dc(), node_id=101)], {'by_node_id': True}, client)
        self.assertFalse(missing['dcs'][0]['matched'])
        self.assertEqual(missing['dcs'][0]['node_id'], 101)
        self.assertEqual(missing['dcs'][0]['unmatched_why'], 'not in SolarWinds')

    def test_writer_date_conversion_failure_keeps_unknown_run(self):
        db = self.db()
        portal.adb.write(db, NOW, 77, 12, {'status': 'ok', 'dcs': [dc()],
                         'links': [{'frm': 'dc02', 'to': 'dc01', 'last_success': '2026-99-01T00:00:00Z'}]})
        model = portal.sp.AdDb(None, connect=lambda: db).read(now=NOW)
        self.assertEqual(model['run']['status'], 'unknown')
        self.assertIn('rows not kept', model['hist'][0]['why'])
        self.assertEqual((model['dcs'], model['links']), ([], []))

    def test_admin_disjoint_namespace_and_forest_domain_list(self):
        a = dc()
        report = filters.ad_report([{'host': a['fqdn'], 'data': dict(a, repl=[]), 'reachable': True}],
                                   [{'data': {'dns': DOMAIN, 'dcs': [a], 'forest': {'name': DOMAIN, 'domains': [DOMAIN]}}}])
        self.assertNotEqual(report.get('why'), ident.REFUSAL)
        self.assertEqual(report['dcs'][0]['dc_id'], 'dc01')
        refused = filters.ad_report([], [{'data': {'dns': DOMAIN, 'forest': {'name': DOMAIN, 'domains': [DOMAIN, 'other.example.mil']}}}])
        self.assertEqual(refused['why'], ident.REFUSAL)

    def test_missing_partition_list_is_not_a_full_read(self):
        r = reader.Reader({})
        self.addCleanup(r.close)
        a = dc()
        a['repl'] = r.read_repl(None, {'dcs_ad': [a]})
        repl = filters._repl_reader([a], NOW, 60, DOMAIN, [a])
        self.assertFalse(repl['dcs'][0]['read'])
        self.assertIn('partition list not read', ident.coverage(repl))

    def test_each_dc_supplies_its_own_partition_list_and_domain(self):
        r = reader.Reader({})
        self.addCleanup(r.close)
        r.endpoint = Mock(return_value={'address': '192.0.2.10', 'nat': False})
        r.connect = Mock(return_value=(Mock(), 1, ''))
        r.a = Mock(return_value=(['192.0.2.10'], ''))
        r.cert, r.kerberos = Mock(return_value={}), Mock(return_value={})
        extra = 'DC=DomainDnsZones,DC=ad,DC=example,DC=mil'
        r.base = Mock(return_value={'attributes': {'namingContexts': [NC, extra],
                                                  'defaultNamingContext': ['DC=ad,DC=example,DC=mil']}})
        r.read_repl = Mock(return_value={'links': []})
        got = r.read_dc('dc01.example.mil', dc(), None, {'naming_contexts': [NC]})
        self.assertEqual(r.read_repl.call_args[0][1]['naming_contexts'], [NC, extra])
        self.assertEqual(got['domain'], DOMAIN)
        r.base.return_value = {'attributes': {}}
        r.read_dc('dc01.example.mil', dc(), None, {'naming_contexts': [NC]})
        self.assertEqual(r.read_repl.call_args[0][1]['naming_contexts'], [])

    def test_healthy_extra_domain_db_link_still_refuses(self):
        db = self.db()
        portal.adb.write(db, NOW, 77, 12, {'status': 'ok', 'dcs': [dc()],
                         'links': [{'frm': 'dc02', 'to': 'dc01', 'nc_dn': NC, 'domain': DOMAIN, 'status': 'ok'}]})
        db.db.execute("UPDATE ad_link SET domain='other.example.mil'")
        model = portal.sp.AdDb(None, connect=lambda: db).read(now=NOW)
        self.assertEqual(model['run']['why'], ident.REFUSAL)
        self.assertEqual(model['dcs'], [])

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_page_words_and_node_id_join_without_browser(self):
        # A DOM tree checks the page's text; Chromium tests cover layout and polling.
        js = r"""
const fs = require('fs'), vm = require('vm');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.classList = {contains: () => false}; }
  append(...xs) { this.children.push(...xs); }
  prepend(...xs) { this.children.unshift(...xs); }
  replaceChildren(...xs) { this.children = xs; }
  setAttribute(k, v) { this[k] = v; }
  getAttribute() { return ''; }
  addEventListener() {}
  querySelectorAll() { return []; }
  get textContent() { return this.children.map(x => typeof x === 'object' ? x.textContent : x).join(' '); }
}
const root = new Element('main'), toasts = new Element('aside');
const document = {getElementById: id => id === 'ad' ? root : toasts, documentElement: new Element('html'),
  createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), querySelector: () => null};
const fetch = () => new Promise(() => {}), window = {fetch, addEventListener() {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document, window, fetch, setTimeout() {}, clearTimeout() {}, Date});
window.adPage.draw(JSON.parse(fs.readFileSync(0, 'utf8')));
console.log(root.textContent);
"""
        def page(model, sw=None):
            answer = {'db': {'on': True, 'model': model}, 'sw': sw or {'on': False}, 'old_after': 90}
            return subprocess.check_output(['node', '-e', js, os.path.join(ROOT, 'roles/site_portal_stack/files/static/ad-page.js')],
                                           input=json.dumps(answer), text=True)
        _, _, failed = self.api(filters.ad_reader_report({'ok': False, 'why': 'LDAP refused'}))
        text = page(failed)
        self.assertIn(ident.COVERAGE_UNKNOWN, text)
        self.assertNotIn('0 of 0', text)
        self.assertNotIn('every DC answered', text)
        _, _, refused = self.api(self.report([dc()], [dc(), dict(dc('DC02'), domain='other.example.mil')]))
        text = page(refused)
        self.assertIn(ident.REFUSAL, text)
        self.assertIn('DC domain attribute (dc02): domain=other.example.mil', text)
        a = dc(site='')
        a['stale_dsa'] = True
        a['repl']['links'] = [{'partner_guid': 'cccccccc-cccc-cccc-cccc-cccccccccccc', 'nc': NC, 'failures': 3}]
        _, _, model = self.api(self.report([a], [a, dc('DC02', guid=GUID2)]),
                              {'dcs': [{'dc': 'dc01', 'node_id': 101, 'matched': 'name'}]})
        # A caption cannot redirect the saved NodeID to the Up row.
        sw = {'on': True, 'age': 900, 'stale': 'older than its interval',
              'section': {'columns': ['DC', 'SolarWinds'], 'rows': [['dc01', 'Up'], ['renamed', 'Down']],
                          'keys': ['102', '101'], 'cell_status': [['', 'ok'], ['', 'critical']]}}
        text = page(model, sw)
        self.assertIn('unknown DSA', text)
        self.assertIn('stale DSA, metadata cleanup', text)
        self.assertIn('not polled', text)
        self.assertIn('Site unknown', text)
        self.assertIn('dc01 Down', text)
        self.assertNotIn('every DC up', text)
        self.assertIn('last known', text)
        self.assertIn('15 min ago', text)
        self.assertRegex(text, r'DCs with a problem 1 dc01 critical')
        self.assertRegex(text, r'unknown DSA\s+→\s+dc01')
        a, b, c = dc(), dc('DC03'), dc('DC05')
        c['repl']['not_granted'] = [NC]
        _, _, partial = self.api(self.report([a, c], [a, b, c]))
        self.assertIn('Replication read in full on 1 of 3 DCs; not read: DC03 (not polled), DC05 (Configuration: no right)', page(partial))
        for why in ('not in SolarWinds', 'not matched', 'ambiguous (2 nodes named dc01)'):
            _, _, unmatched = self.api(self.report([dc()]), {'dcs': [{'dc': 'dc01', 'unmatched_why': why}]})
            self.assertIn(why, page(unmatched))
        bad = self.report([dc()])
        bad['repl']['links'] = [{'frm': 'dc02', 'to': 'dc01', 'failures': 'invalid'}]
        _, _, model = self.api(bad)
        text = page(model)
        self.assertIn('Rows not kept:', text)
        self.assertIn('Unknown', text)


@unittest.skipUnless(portal._browser(), 'no google-chrome or chromium')
class FableBrowser(unittest.TestCase):
    db = OneDomain.db
    api = OneDomain.api
    report = OneDomain.report
    page = portal.Browser1012.page
    READ = portal.Browser1012.READ + "r.text=document.getElementById('ad').textContent;"

    def answer(self, report, sw=None):
        return {'db': {'on': True, 'model': self.api(report, sw)[2]}, 'sw': {'on': False}, 'old_after': 90}

    def test_1_unpolled_grey_in_page_and_full_report_not_a_problem(self):
        report = self.report([dc()], [dc(), dc('DC02', 'SITEB0201', GUID2)])
        a = self.answer(report)
        import test_report_fit as fit
        for look in ('atlas', 'brief', 'ops'):
            # the full report, as rendered: DC02's row grey (r-unknown) and "not polled" (an iframe write of the whole
            # report does not render in the headless check, so the rendered HTML itself is read)
            full = fit.render(look, report['report'])
            rows = re.findall(r'<tr class="r-unknown"[^>]*>(.*?)</tr>', full, re.S)
            self.assertTrue(any('DC02' in x and 'not polled' in x for x in rows), look)
            r = self.page([a], self.READ, look=look)
            row = next(d for d in r['dcs'] if d[0] == 'dc02')
            self.assertEqual(row[1:3], ['r-unknown', 'not polled'])
            self.assertEqual(r['tv'][r['tiles'].index('DCs with a problem')], '1')  # DC01's missing health probes

    def test_2_coverage_reasons_and_failed_discovery(self):
        a, b, c = dc(), dc('DC03'), dc('DC05')
        c['repl']['not_granted'] = [NC]
        answer = self.answer(self.report([a, c], [a, b, c]))
        r = self.page([answer], self.READ)
        self.assertIn('Replication read in full on 1 of 3 DCs; not read: DC03 (not polled), DC05 (Configuration: no right)', r['text'])
        r = self.page([self.answer(filters.ad_reader_report({'ok': False, 'why': 'LDAP refused'}))], self.READ)
        self.assertIn(ident.COVERAGE_UNKNOWN, r['text'])
        self.assertNotIn('0 of 0', r['text'])
        self.assertNotIn('every DC answered', r['text'])

    def test_3_second_domain_evidence_reaches_browser(self):
        for evidence in ('partner NC', 'DC domain attribute'):
            a = dc()
            known = [a]
            if evidence == 'partner NC':
                a['repl']['links'] = [{'partner': 'DC02', 'nc': 'DC=other,DC=example,DC=mil'}]
            else:
                known.append(dict(dc('DC02'), domain='other.example.mil'))
            r = self.page([self.answer(self.report([a], known))], self.READ)
            self.assertTrue(any(ident.REFUSAL in n and evidence in n for n in r['notes']), r['notes'])
            self.assertEqual((r['dcs'], r['links']), ([], []))
        a = self.answer(self.report([dc()]))
        a['sw'] = {'on': True, 'age': 0, 'section': {'columns': ['DC', 'SolarWinds'],
                   'rows': [['dc01.other.example.mil', 'Up']], 'cell_status': [['', 'ok']], 'keys': ['101']}}
        self.assertNotIn(ident.REFUSAL, self.page([a], self.READ)['text'])

    def test_4_stale_dsa_text(self):
        a = dc()
        a['stale_dsa'] = True
        r = self.page([self.answer(self.report([a]))], self.READ)
        self.assertIn('Two NTDS objects for DC01 - stale DSA, metadata cleanup', r['text'])

    def test_5_unknown_source_direction_and_denominator(self):
        a = dc()
        a['repl']['links'] = [{'partner_guid': GUID2, 'nc': NC, 'failures': 3}]
        answer = self.answer(self.report([a]))
        self.assertEqual(answer['db']['model']['run']['repl_dcs'], 1)
        r = self.page([answer], self.READ)
        self.assertTrue(any('unknown DSA → dc01' in row for row in r['links']), r['links'])

    def test_6_empty_site_cell(self):
        r = self.page([self.answer(self.report([dc(site='')]))], self.READ + "r.site=document.querySelector('.adp-dcs tbody tr').cells[1].textContent;")
        self.assertEqual(r['site'], 'Site unknown')

    def test_7_solarwinds_age_last_known_and_match_reasons(self):
        dcs = [dc('DC%02d' % i) for i in range(1, 5)]
        nodes = [{'node_id': 101, 'name': 'dc01'}, {'node_id': 102, 'name': 'dc01'}, {'node_id': 104, 'name': 'dc04'}]
        sw = {'dcs': swis._dc_rows(dcs, swis.match(dcs, nodes), {}, {}, {}, False)}
        sw['dcs'][2]['unmatched_why'] = 'not matched: the same SolarWinds node claimed by dc03, dc05'
        a = self.answer(self.report(dcs), sw)
        a['sw'] = {'on': True, 'age': 900, 'every': 600, 'stale': 'older than its interval',
                   'section': {'columns': ['DC', 'SolarWinds'], 'rows': [['dc04.example.mil', 'Up']],
                               'cell_status': [['', 'ok']], 'keys': ['104']}}
        r = self.page([a], self.READ)
        rows = {d[0]: d[6] for d in r['dcs']}
        self.assertEqual(rows['dc01'], 'ambiguous (2 nodes named dc01)')
        self.assertEqual(rows['dc02'], 'not in SolarWinds')
        self.assertTrue(rows['dc03'].startswith('not matched:'))
        self.assertIn('last known · 15 min ago', rows['dc04'])
        self.assertIn('Not live', r['live'])

    def test_8_history_uses_stored_status_and_rows_not_kept(self):
        report = self.report([dc()])
        report['repl']['links'] = [{'frm': 'DC02', 'to': 'DC01', 'failures': 'unknown'}]
        a = self.answer(report)
        r = self.page([a], self.READ + "r.history=q('#ad section:nth-of-type(4) tbody tr');")
        self.assertIn('Rows not kept:', r['text'])
        self.assertTrue(any('Unknown' in row and 'Rows not kept:' in row for row in r['history']))
        a['db']['model']['hist'][0].update(status='warning', c=0, w=0, why='')
        r = self.page([a], self.READ + "r.badges=q('#ad section:nth-of-type(4) tbody tr td:nth-child(2)');")
        self.assertEqual(r['badges'], ['Warning'])

    def test_9_overdue_sample_on_304_is_not_live(self):
        a = self.answer(self.report([dc()]), {'dcs': [{'dc': 'dc01', 'node_id': 101, 'matched': 'name'}]})
        a.update(poll=15)
        a['sw'] = {'on': True, 'age': 715, 'every': 600, 'stale': '',
                   'section': {'columns': ['DC', 'SolarWinds'], 'rows': [['dc01.example.mil', 'Up']],
                               'cell_status': [['', 'ok']], 'keys': ['101']}}
        r = self.page([a, '304'], self.READ, wait=20000)
        self.assertIn('Not live', r['live'])
        self.assertIn('last known', r['dcs'][0][6])


if __name__ == '__main__':
    unittest.main(verbosity=2)
