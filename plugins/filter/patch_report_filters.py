"""Patch hosts' report (1.74.0): every host of the run - patched, up to date, skipped on purpose, and the ones that could
not be patched or need a look - as the report every other job sends (roles/site_email: the email, the website, the
workbook). playbooks/patch_hosts.yml gathers what each host's steps left (patch_host_facts) and gives them here.

patch_host_state(facts) -> one host's state: {state, reason, step, detail, todo, snapshot, ...}
patch_report(hosts, opts) -> the report (title, status, summary, sections, counts)

A state is one of: patched, would_patch (a dry run), up_to_date, skipped (on purpose), could_not, services_down
(patched, but a service that starts at boot is not running), not_started (the rollout stopped first:
patch_continue_on_failure false). could_not, services_down and not_started need a look: critical for the index,
counted by patch_fail_on_could_not. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

# the hostvars each host's steps leave, the only ones read here
PATCH_FACT_KEYS = ('site_host_unreached', 'patch_not_ready', 'patch_unsupported', 'patch_up_to_date', 'patch_reboot_pending',
                   'patch_reboot_only', 'patch_updates', '_patch_ended_on_purpose', '_patch_done', '_patch_could_not',
                   'patch_could_not', '_patch_snapshot', 'patch_started', 'patch_not_started', 'group_names')

# reason -> (order: worst first, what to do in one line)
REASONS = {
    'LOST mid-run': (0, 'Look at the host (its console in vCenter): it may be half updated. Roll back from the snapshot if needed.'),
    'DID NOT COME BACK after reboot': (1, 'Open its console in vCenter and look why it does not boot. Roll back from the snapshot if needed.'),
    'PATCHED - SERVICES DOWN': (2, 'systemctl status <service>; journalctl -u <service> -b. Roll back from the snapshot if needed.'),
    'FAILED during update': (3, 'Read the error (dnf history info last on the host), fix it, then run again for it.'),
    'FAILED before the update': (3, 'Read the error, fix it on the host, then run again for it. Nothing was installed.'),
    'NO SNAPSHOT': (4, "Read the snapshot step's message (vCenter, the credential), then run again for it."),
    'VM NOT FOUND': (5, 'Set vmware_vm_name (its VM in vCenter), or patch_physical: true for a server that is no VM.'),
    'TWO VMs': (5, 'Set vmware_vm_name (a host variable: the name of its VM in vCenter), then run again for it.'),
    'WHICH VM IS NOT SURE': (5, 'Set vmware_vm_name (a host variable: the name of its VM in vCenter), then run again for it.'),
    'OLD SNAPSHOT IN THE WAY': (6, 'Delete the old snapshot once that patching is proven, or run with patch_snapshot_replace: true.'),
    'REFUSED': (7, 'It is the machine running the job: patch it another way, or leave it out (Limit, no_patch).'),
    'NOT READY': (8, 'Fix that (sudo for the machine credential, or room in /var), then run again for it.'),
    'NO DNF OR YUM': (9, 'Not a RHEL-like Linux: patch it another way, or put it in no_patch.'),
    'NOT REACHED': (10, 'Check it is on and that ssh answers from AAP, then run again for it.'),
    'NOT STARTED': (11, 'The rollout stopped at a host before it (patch_continue_on_failure: false). Run again for it.'),
}
NEEDS_LOOK = ('could_not', 'services_down', 'not_started')


def _s(v):
    return '' if v is None else str(v)


def _short(text, n=160):
    """One line, at most n characters: the last lines of an error, the noise (ssh, Ansible's own words) taken off."""
    lines = [re.sub(r'\s+', ' ', x).strip() for x in _s(text).replace('\\n', '\n').splitlines()]
    lines = [x for x in lines if x and not re.match(r'(?i)^(shared connection to|warning:|>>>|task failed:?$)', x)]
    t = ' / '.join(lines[-2:]) if lines else ''
    t = re.sub(r'^((Task|Module|Action) failed:\s*)+', '', t)
    t = re.sub(r'^LOST at [^:]+:\s*', '', t)                  # the step is in its own column
    return t if len(t) <= n else t[:n - 1].rstrip() + '…'


def _snap(facts, bad):
    """The snapshot as the report says it: what the snapshot step did; for a host that needs a look, kept to roll back."""
    s = _s(facts.get('_patch_snapshot')).strip()
    if not s:
        return 'none'
    m = re.match(r'^(took|reused|would take|would reuse) "([^"]+)"', s)
    if bad and m and not m.group(1).startswith('would'):
        return 'snapshot %s kept - roll back from it if needed' % m.group(2)
    return s


def patch_host_state(facts, dry=False):
    """One host's state from what its steps left (PATCH_FACT_KEYS)."""
    f = facts if isinstance(facts, dict) else {}
    out = {'state': '', 'reason': '', 'step': '', 'detail': '', 'todo': '', 'updates': None, 'rebooted': None,
           'reboot': '', 'warn_services': [], 'why': ''}
    cn = f.get('_patch_could_not') if isinstance(f.get('_patch_could_not'), dict) else None
    vm = f.get('patch_could_not') if isinstance(f.get('patch_could_not'), dict) else None

    def bad(state, reason, step, detail):
        out.update(state=state, reason=reason, step=step, detail=_short(detail), todo=REASONS.get(reason, (99, ''))[1])
        return out
    if _s(f.get('site_host_unreached')):
        return bad('could_not', 'NOT REACHED', 'before patching (is it reached)', f['site_host_unreached'])
    if f.get('patch_not_ready'):
        return bad('could_not', 'NOT READY', 'before patching (root, room in /var)', f['patch_not_ready'])
    if f.get('patch_unsupported'):
        return bad('could_not', 'NO DNF OR YUM', 'before patching (its package manager)', f['patch_unsupported'])
    if vm:
        return bad('could_not', _s(vm.get('reason')) or 'VM NOT FOUND', 'before patching (its VM in vCenter)', vm.get('detail'))
    if cn:
        reason = _s(cn.get('reason')) or 'FAILED during update'
        state = 'services_down' if reason == 'PATCHED - SERVICES DOWN' else 'could_not'
        r = bad(state, reason, _s(cn.get('step')), cn.get('detail'))
        if state == 'services_down':
            r['warn_services'] = list(cn.get('services') or [])
        return r
    if f.get('patch_not_started'):
        return bad('not_started', 'NOT STARTED', 'not started', f['patch_not_started'])
    if f.get('_patch_ended_on_purpose'):
        out.update(state='skipped', why=_s(f['_patch_ended_on_purpose']))
        return out
    if f.get('patch_up_to_date') is not None and f.get('patch_up_to_date') is not False:
        out.update(state='up_to_date', reboot='REBOOT PENDING' if _bool(f.get('patch_reboot_pending')) else '')
        return out
    done = f.get('_patch_done') if isinstance(f.get('_patch_done'), dict) else None
    if done is not None:
        out.update(state='would_patch' if dry else 'patched', updates=done.get('updates'),
                   rebooted=_bool(done.get('rebooted')), reboot=_s(done.get('reboot')),
                   warn_services=list(done.get('warn_services') or []))
        if out['updates'] is None and f.get('patch_updates') is not None:
            out['updates'] = f.get('patch_updates')
        return out
    if f.get('patch_started'):
        # it started and left nothing - lost where nothing could record it (patch_continue_on_failure false)
        return bad('could_not', 'LOST mid-run', 'patching', 'the job lost it, and nothing more is known')
    out.update(state='skipped', why='not in this run')
    return out


def _bool(v):
    return v is True or _s(v).lower() in ('true', 'yes', '1')


def _plural(n, one, many):
    return '%d %s' % (n, one if n == 1 else many)


def patch_report(hosts, opts=None):
    """The report. hosts: {name: facts}. opts: dry (Check mode), serial, continue_on_failure, snapshot, security_only,
    automatic_restarts, stopped (the stop message when the rollout stopped), fail_on_could_not."""
    o = opts or {}
    dry = _bool(o.get('dry'))
    rows = []
    for name in sorted(hosts or {}, key=lambda x: _s(x).lower()):
        st = patch_host_state(hosts[name], dry)
        st['host'] = _s(name)
        st['snapshot'] = _snap(hosts[name] if isinstance(hosts[name], dict) else {}, st['state'] in NEEDS_LOOK and not dry)
        rows.append(st)
    by = dict((k, [r for r in rows if r['state'] == k]) for k in
              ('patched', 'would_patch', 'up_to_date', 'skipped', 'could_not', 'services_down', 'not_started'))
    look = sorted(by['could_not'] + by['services_down'] + by['not_started'],
                  key=lambda r: (REASONS.get(r['reason'], (99,))[0], r['host'].lower()))
    patched = by['would_patch'] if dry else by['patched']
    warn = [r for r in patched if r['warn_services']]
    n_look = len(look)
    status = 'critical' if n_look else ('warning' if warn else 'ok')
    word = 'would be patched' if dry else 'patched'

    # the title: what happened, in a few words; a dry run says so first
    parts = ['%s %s' % (_plural(len(patched), 'host', 'hosts'), word)]
    if by['could_not']:
        parts.append('%d could not be patched' % len(by['could_not']))
    if by['services_down']:
        parts.append('%d with services down' % len(by['services_down']))
    if by['not_started']:
        parts.append('%d not started' % len(by['not_started']))
    title = ('DRY RUN - ' if dry else '') + 'Patch hosts: ' + ', '.join(parts)
    sub = ['%s in this run' % _plural(len(rows), 'host', 'hosts'), 'patch_serial %s' % _s(o.get('serial') or 1),
           'snapshots %s' % ('on' if _bool(o.get('snapshot')) else 'off'),
           'security updates only' if _bool(o.get('security_only')) else 'all updates',
           'reboots %s' % ('on (automatic_restarts)' if _bool(o.get('automatic_restarts')) else 'off')]
    if dry:
        sub.insert(0, 'Check mode: nothing was installed, no snapshot taken, nothing rebooted')

    summary = [
        {'label': 'Would patch' if dry else 'Patched', 'value': len(patched),
         'note': ('%d with services stopped' % len(warn)) if warn else ('updates installed' if not dry else 'updates found'),
         'status': 'warning' if warn else 'ok'},
        {'label': 'Up to date', 'value': len(by['up_to_date']),
         'note': ('%d with a reboot pending' % len([r for r in by['up_to_date'] if r['reboot']])) if any(r['reboot'] for r in by['up_to_date'])
         else 'nothing to install'},
        {'label': 'Could not patch', 'value': len(by['could_not']),
         'note': ('+ %d patched, services down' % len(by['services_down'])) if by['services_down'] else
         ('see below' if by['could_not'] else 'none'),
         'status': 'critical' if (by['could_not'] or by['services_down']) else 'ok'},
        {'label': 'Skipped on purpose', 'value': len(by['skipped']), 'note': 'no_patch, AAP, protected'}]
    if by['not_started']:
        summary.append({'label': 'Not started', 'value': len(by['not_started']), 'note': 'the rollout stopped', 'status': 'critical'})

    sections = []
    if look:
        cards = [{'status': 'warning' if r['state'] == 'not_started' else 'critical', 'chip': r['reason'], 'meta': r['step'],
                  'title': r['host'], 'subject': r['detail'], 'next': r['todo']} for r in look[:3]]
        sections.append({'title': 'Start here', 'cards': cards})
        sections.append({
            'title': 'Could not patch or needs a look', 'status': 'critical',
            'text': ('Worst first. A host here would not be patched.' if dry else
                     'Worst first. A host here was not patched, or was patched and needs a look. Its snapshot, when one was taken, is kept.'),
            'columns': ['Host', 'Reason', 'Step', 'What to do', 'Snapshot'],
            'rows': [[r['host'], r['reason'] + ((': ' + r['detail']) if r['detail'] else ''), r['step'], r['todo'], r['snapshot']] for r in look],
            'row_status': ['warning' if r['state'] == 'not_started' else 'critical' for r in look],
            'cell_status': [['', 'warning' if r['state'] == 'not_started' else 'critical', '', '', ''] for r in look],
            'kinds': ['', 'wrap', 'wrap', 'wrap', 'wrap']})
    if patched:
        sections.append({
            'title': 'Would patch' if dry else 'Patched',
            'text': 'Each host with the updates it ' + ('has' if dry else 'got') + ('.' if dry else ', whether it was rebooted, and the services it stopped.'),
            'columns': ['Host', 'Updates', 'Rebooted', 'Services', 'Snapshot'],
            'rows': [[r['host'], _s(r['updates']) if r['updates'] is not None else '-',
                      ('no (dry run)' if dry else (('yes' if r['rebooted'] else 'no') + ((' - ' + r['reboot']) if r['reboot'] else ''))),
                      ('WARN stopped: ' + ', '.join(r['warn_services'])) if r['warn_services'] else ('-' if dry else 'all running again'),
                      r['snapshot']] for r in patched],
            'row_status': ['warning' if r['warn_services'] else '' for r in patched],
            'cell_status': [['', '', 'warning' if r['reboot'] and not dry else '', 'warning' if r['warn_services'] else '', ''] for r in patched],
            'kinds': ['', '', 'wrap', 'wrap', 'wrap']})
    if by['up_to_date']:
        sections.append({
            'title': 'Up to date', 'text': 'Nothing to install: no snapshot, not patched.',
            'columns': ['Host', 'Reboot'],
            'rows': [[r['host'], r['reboot'] or 'none pending'] for r in by['up_to_date']],
            'row_status': ['' for r in by['up_to_date']],
            'cell_status': [['', 'warning' if r['reboot'] else ''] for r in by['up_to_date']]})
    if by['skipped']:
        sections.append({
            'title': 'Skipped on purpose', 'text': 'Never patched by this job: no_patch, the AAP hosts, a protected VM.',
            'columns': ['Host', 'Why'], 'rows': [[r['host'], r['why']] for r in by['skipped']], 'collapsed': len(by['skipped']) > 10})
    notes = []
    if o.get('stopped'):
        notes.append(_s(o['stopped']))
    if n_look and dry:
        notes.append('A dry run never fails the job, so the approval can follow it: the hosts above would not be patched.')
    elif n_look and _bool(o.get('fail_on_could_not', True)):
        notes.append('The job ends failed because %s (patch_fail_on_could_not).' % (
            _plural(n_look, 'host needs a look', 'hosts need a look')))
    notes.append('patch_continue_on_failure is true: a host that fails or is lost is listed here, and the others go on.'
                 if _bool(o.get('continue_on_failure', True)) else
                 'patch_continue_on_failure is false: the rollout stops at the first host that fails or is lost.')
    sections.append({'title': 'Notes', 'lines': notes})
    return {'title': title, 'subtitle': ' \u00b7 '.join(sub), 'status': status, 'summary': summary, 'sections': sections,
            'counts': {'critical': n_look, 'warning': len(warn), 'low': 0},
            'needs_look': [r['host'] for r in look]}


# the results roles/patch registers on the host itself: one of them unreachable = the host was lost there
PATCH_REMOTE_STEPS = ('_patch_host_mid', '_patch_df', '_patch_before_raw', '_patch_pm', '_patch_dnf', '_patch_yum_list',
                      '_patch_yum_run', '_patch_needs', '_patch_reboot_run', '_patch_after')


def patch_batch_bad(hostvars, batch, at=''):
    """The hosts of a batch that could not be patched or were lost: patch_continue_on_failure false stops the rest of
    the batch on them. Under the linear strategy a failed host's rescue runs only after its peers' last step, and a
    lost one is taken through its skipped steps beside them. So at a checkpoint (at: its name, which every host still
    going set as _patch_at a moment before) a host is bad when it is lost at a step, has its _patch_could_not, or
    started its steps (_patch_at) but did not get to this checkpoint: it failed or was lost on the way. A host left
    out on purpose or NOT STARTED is not."""
    out = []
    for h in batch or []:
        v = (hostvars or {}).get(h) or {}
        lost = any(isinstance(v.get(k), dict) and v.get(k).get('unreachable') for k in PATCH_REMOTE_STEPS if k in v)
        behind = bool(at) and '_patch_at' in v and v.get('_patch_at') != at \
            and not v.get('_patch_ended_on_purpose') and not v.get('patch_not_started')
        if '_patch_could_not' in v or lost or behind:
            out.append(_s(h))
    return out


def patch_host_facts(hostvars, hosts):
    """{host: the hostvars patch_report reads} for these hosts."""
    out = {}
    for h in hosts or []:
        v = (hostvars or {}).get(h) or {}
        out[_s(h)] = dict((k, v[k]) for k in PATCH_FACT_KEYS if k in v)
    return out


class FilterModule(object):
    def filters(self):
        return {'patch_report': patch_report, 'patch_host_state': patch_host_state, 'patch_host_facts': patch_host_facts,
                'patch_batch_bad': patch_batch_bad}
