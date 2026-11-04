"""One admitted native helper launch, own process custody and finite resources."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import sys
import time
from pathlib import Path

import common as c

ROOT, REPO = c.ROOT, c.REPO
COMMIT = 'c41378acc0c1f24f6534011f1aab09691a9a6948'


def lease_identity(prepared):
    path = Path(prepared['state']) / 'profile.lock'
    value = c.private(path, 'file')
    c.require(value.st_size == 0, 'lease_format_changed')
    return [value.st_dev, value.st_ino]


def lease_free(prepared, pin):
    path = Path(prepared['state']) / 'profile.lock'
    c.require(lease_identity(prepared) == pin, 'lease_inode_changed')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        value = os.fstat(fd)
        c.require([value.st_dev, value.st_ino] == pin, 'lease_descriptor_changed')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        c.require(lease_identity(prepared) == pin, 'lease_inode_changed')
        return True
    finally:
        os.close(fd)


def checked_prepared(pin):
    prepared = c.read_json(ROOT / 'prepared.json', pin)
    admission = c.read_json(ROOT / 'admission.json', prepared['admission_sha256'])
    c.require(prepared['status'] == 'prepared' and prepared['commit'] == COMMIT
        and prepared['full_ci_passed'] is True and admission['harness_root'] == str(ROOT)
        and prepared['run_id'] == admission['run_id']
        and prepared['run_attempt'] == admission['run_attempt']
        and prepared['artifact_id'] == admission['artifact_id'], 'prepared_CI_identity_changed')
    c.require(c.sha(ROOT / 'reviewer-checklist.json') == admission['reviewer_checklist_sha256'],
              'review_checklist_changed')
    for name, digest in admission['scripts_sha256'].items():
        c.require(c.sha(ROOT / name) == digest, 'script_changed')
    for name, digest in admission['source_pins'].items():
        c.require(c.sha(REPO / name) == digest, 'source_changed')
    c.require(c.sha(prepared['helper']) == prepared['helper_sha256']
        and c.sha(Path(prepared['app']) / 'Contents/MacOS/Jarvis') == prepared['main_sha256']
        and c.sha(Path(prepared['app']) / 'Contents/Info.plist')
            == prepared['info_plist_sha256']
        and c.sha(ROOT / 'launch-request.json') == prepared['request_sha256'],
        'prepared_app_changed')
    if 'code_resources_sha256' in prepared:
        c.require(c.sha(Path(prepared['app']) / 'Contents/_CodeSignature/CodeResources')
            == prepared['code_resources_sha256'], 'prepared_code_seal_changed')
    c.verify_copies(prepared, deadline=time.monotonic() + 20)
    c.resources(prepared)
    return prepared


def gui_intent(args):
    prepared = checked_prepared(args.prepared_sha256)
    active = c.read_json(ROOT / 'active.json')
    c.require(active['prepared_sha256'] == args.prepared_sha256
        and time.time() < active['launch_requested_unix'] + 810
        and c.exact_alive(active['app']), 'current_app_required')
    pin = c.write_json(ROOT / 'quit-intent.json', {'schema': 'jarvis.background-GUI-quit-intent/1',
        'prepared_sha256': args.prepared_sha256, 'active_sha256': c.sha(ROOT / 'active.json'),
        'app': active['app'], 'observed_at_unix': time.time(), 'GUI_quit_still_required': True,
        'profile': prepared['state']})
    print(json.dumps({'status': 'intent_recorded_GUI_quit_required', 'sha256': pin}))
    return 0


def run(args):
    prepared = checked_prepared(args.prepared_sha256)
    for name in ('native-intent.json', 'launch-receipt.json', 'active.json', 'closed.json'):
        c.require(not (ROOT / name).exists(), 'prior_native_attempt_preserved_no_replay')
    c.library()  # Check actual metadata ABI support before any launch effect.
    born, deadline = time.monotonic(), time.monotonic() + 900
    requested = time.time()
    c.write_json(ROOT / 'native-intent.json', {'schema': 'jarvis.background-native-intent/1',
        'prepared_sha256': args.prepared_sha256, 'launch_requested_unix': requested,
        'active_seconds': 810, 'cleanup_reserve_seconds': 90,
        'maximum_launch_calls': 1, 'automatic_retry': False})
    known, signals, issues = {}, [], []
    app, lease, active_written, normal_intent = None, None, False, False
    resource_failure, stopped = None, False

    def admit(row, role):
        if row is None:
            return
        c.require(row['uid'] == os.getuid() and (len(known) < 128 or c.identity(row) in known),
                  'owned_process_limit_or_UID')
        key = c.identity(row)
        if key not in known:
            known[key] = {**row, 'role': role}

    def scan():
        pending = list(known.values())
        for parent in pending:
            if not c.exact_alive(parent):
                continue
            for child in c.children(parent):
                old = len(known); admit(child, 'observed_owned_descendant')
                if len(known) != old:
                    pending.append(child)

    def checkpoint():
        c.require(time.monotonic() < born + 810, 'native_active_deadline')
        c.resources(prepared); scan()

    def terminate_app(reason):
        nonlocal stopped
        if stopped:
            return
        stopped = True
        if app is not None and c.exact_alive(app):
            # The exact app returned by our one admitted launch, never a pattern kill.
            os.kill(app['pid'], signal.SIGTERM)
            signals.append({'pid': app['pid'], 'identity': list(c.identity(app)),
                            'signal': 'SIGTERM', 'reason': reason})

    try:
        c.verify_copies(prepared, deadline=min(deadline, time.monotonic() + 5), metadata_only=True)
        c.resources(prepared, fresh_provider=True)
        c.capture([prepared['helper'], '--request', str(ROOT / 'launch-request.json'),
            '--receipt', str(ROOT / 'launch-receipt.json')], deadline=min(deadline, born + 45),
            checkpoint=checkpoint, maximum=65536, stderr_max=65536,
            environment={'HOME': prepared['home'], 'TMPDIR': prepared['temporary'],
                'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C'},
            on_spawn=lambda pid: admit(c.snapshot_pid(pid), 'own_CI_NSWorkspace_helper'))
        receipt = c.read_json(ROOT / 'launch-receipt.json')
        c.require(receipt['status'] == 'launched' and receipt['launch_effect_status'] == 'known'
            and receipt['request_sha256'] == prepared['request_sha256']
            and receipt['bundle_id'] == prepared['bundle_identifier']
            and receipt['bundle_path'] == prepared['app'], 'native_launch_not_confirmed')
        returned = receipt['process']; app = c.snapshot_pid(returned['pid'])
        c.require(app is not None and all(app[key] == returned[key] for key in
            ('pid', 'start_seconds', 'start_microseconds', 'executable_path'))
            and app['executable_path'] == str(Path(prepared['app']) / 'Contents/MacOS/Jarvis'),
            'returned_app_identity_changed')
        admit(app, 'native_app'); scan()
        while time.monotonic() < deadline:
            scan()
            try:
                c.resources(prepared)
            except c.GuardIssue as error:
                resource_failure = str(error); terminate_app(resource_failure)
            alive = [row for row in known.values() if c.exact_alive(row)]
            if lease is None and (Path(prepared['state']) / 'profile.lock').exists():
                lease = lease_identity(prepared)
            if not active_written and lease is not None and any(
                row['parent_pid'] == app['pid']
                    and row['executable_path'] == prepared['python_process_executable']
                for row in alive):
                c.write_json(ROOT / 'active.json', {'schema': 'jarvis.background-native-active/1',
                    'prepared_sha256': args.prepared_sha256, 'commit': COMMIT,
                    'app': app, 'launch_requested_unix': requested,
                    'lease_identity': lease, 'profile': prepared['state'],
                    'request_sha256': prepared['request_sha256']})
                active_written = True
                print(json.dumps({'status': 'active', 'app_pid': app['pid'],
                    'active_deadline_unix': requested + 810}), flush=True)
            intent_path = ROOT / 'quit-intent.json'
            if intent_path.exists() and not normal_intent:
                intent = c.read_json(intent_path)
                c.require(intent['prepared_sha256'] == args.prepared_sha256
                    and c.identity(intent['app']) == c.identity(app)
                    and intent['active_sha256'] == c.sha(ROOT / 'active.json'),
                    'GUI_quit_intent_changed')
                normal_intent = True
            if not alive:
                break
            if time.monotonic() >= born + 810:
                terminate_app('native_active_deadline')
            time.sleep(.2)
        else:
            issues.append('native_cleanup_deadline')
    except BaseException as error:
        issues.append(str(error) if isinstance(error, c.GuardIssue) else type(error).__name__)
        # An unconfirmed returned candidate in the helper receipt is retained data,
        # never signal authority. Do not reconstruct or retry an unknown launch.
        if app is None:
            issues.append('returned_app_not_admitted_no_signal_authority')
        try:
            terminate_app('native_observer_or_launch_failure')
            while time.monotonic() < deadline and any(c.exact_alive(row) for row in known.values()):
                scan(); time.sleep(.2)
        except BaseException:
            issues.append('cleanup_observation_unconfirmed')
    result = {'schema': 'jarvis.background-native-close/1', 'commit': COMMIT,
        'prepared_sha256': args.prepared_sha256, 'known_processes': list(known.values()),
        'signals_sent': signals, 'issues': issues, 'normal_GUI_quit_intent': normal_intent,
        'resource_failure': resource_failure, 'same_lease_free': False,
        'all_known_identities_absent': False, 'normal_detach_attempts': [],
        'quota_unmounted_without_force': False, 'originals_or_profiles_deleted': False,
        'images_preserved': True, 'full_increment_qualification_claimed': False,
        'native_exit_status_claimed': False, 'process_scope': 'Known child metadata only; no global absence claim',
        'elapsed_seconds': round(time.monotonic() - born, 3)}
    try:
        result['all_known_identities_absent'] = bool(app is not None and known) and all(
            not c.exact_alive(row) for row in known.values())
        if result['all_known_identities_absent'] and lease is not None:
            result['same_lease_free'] = lease_free(prepared, lease)
        if result['all_known_identities_absent'] and result['same_lease_free']:
            for attempt in (1, 2):
                c.require(time.monotonic() + 22 < deadline, 'normal_detach_reserve_exhausted')
                c.require(all(not c.exact_alive(row) for row in known.values())
                    and lease_free(prepared, lease), 'custody_changed_before_detach')
                c.resources(prepared, fresh_provider=True)
                try:
                    c.capture(['/usr/bin/hdiutil', 'detach', prepared['root_quota']],
                        deadline=min(deadline - 1, time.monotonic() + 20), maximum=65536,
                        stderr_max=65536)
                    effect = 'command_returned'
                except BaseException as error:
                    effect = str(error) if isinstance(error, c.GuardIssue) else type(error).__name__
                root = Path(prepared['root_quota']); after = root.lstat()
                unmounted = (not os.path.ismount(root) and [after.st_dev, after.st_ino]
                             == prepared['underlying_mount_identity'])
                result['normal_detach_attempts'].append({'attempt': attempt,
                    'effect_observation': effect, 'underlying_identity_confirmed': unmounted})
                if unmounted:
                    root.chmod(0); result['quota_unmounted_without_force'] = True; break
        result['status'] = ('closed_after_GUI_intent' if normal_intent and not signals and not issues
            and resource_failure is None and result['quota_unmounted_without_force']
            else 'closed_with_original_limits' if result['quota_unmounted_without_force']
            else 'unknown_custody_preserved')
    except BaseException as error:
        result['issues'].append(str(error) if isinstance(error, c.GuardIssue) else type(error).__name__)
        result['status'] = 'unknown_custody_preserved'
    result['resource_accounting'] = c.resource_observation()
    pin = c.write_json(ROOT / 'closed.json', result)
    print(json.dumps({'status': result['status'], 'closed_sha256': pin}), flush=True)
    return 0 if result['status'] == 'closed_after_GUI_intent' else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--admit-launch', action='store_true')
    parser.add_argument('--request-GUI-quit', action='store_true')
    args = parser.parse_args()
    c.require(sys.dont_write_bytecode and args.admit_launch != args.request_GUI_quit,
              'explicit_launch_or_GUI_intent_G_required')
    os.umask(0o077)
    return gui_intent(args) if args.request_GUI_quit else run(args)


if __name__ == '__main__':
    raise SystemExit(main())
