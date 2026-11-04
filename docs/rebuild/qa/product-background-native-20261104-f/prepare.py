"""NOEXEC by import: readmit one closed synthetic quota for a fresh F case."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import stat
import sys
import time
from pathlib import Path

import common as c

ROOT, REPO = c.ROOT, c.REPO
PRIOR = Path('/private/tmp/jarvis-product-background-native-20261104-e')
COMMIT = '2c8259de8112aa5f1487814a5c5c35b3931e5c43'
PRIOR_PREPARED = '2ee2419813d866116202dde4e96a8246aadf1dc83ae5be44e25a4f357f9fb599'
PRIOR_CLOSED = 'a52b472ad082c957ec45132fc3ec344f55e32d6b9f22efa39d5592bbc449d648'
PRIOR_ACTIVE = '054923a82b1129529345011a0ffedb71031132018114965007934fb7e68a2713'


def _known_absent(closed):
    c.require(len(closed['known_processes']) == 12
        and all(not c.exact_alive(row) for row in closed['known_processes']),
        'prior_known_process_identity_still_alive')


def _prior_lease_free(prior, active, volume):
    path = Path(prior['state']) / 'profile.lock'
    value = c.private(path, 'file')
    c.require(value.st_size == 0 and value.st_dev == volume['device']
        and value.st_ino == active['lease_identity'][1], 'prior_lease_inode_changed')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        c.require((opened.st_dev, opened.st_ino) == (value.st_dev, value.st_ino),
                  'prior_lease_descriptor_changed')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise c.GuardIssue('prior_lease_not_free') from None
        fcntl.flock(fd, fcntl.LOCK_UN)
        after = path.lstat()
        c.require((after.st_dev, after.st_ino) == (value.st_dev, value.st_ino),
                  'prior_lease_named_identity_changed')
        return [value.st_dev, value.st_ino]
    finally:
        os.close(fd)


def _rebind_public_manifest(prior, volume, checkpoint):
    """Rebind only the remount device number, preserving every other public pin."""
    manifest = c.read_json(PRIOR / 'copies-manifest.json',
        prior['copies_manifest_sha256'], maximum=4 * c.MIB)
    c.require(manifest['source_commit'] == COMMIT
        and manifest['schema'] == 'jarvis.native-QA-copy-manifest/1',
        'prior_public_manifest_scope_changed')
    previous_device = prior['volume']['device']
    for label in ('source', 'dependencies'):
        tree = manifest[label]
        c.require(tree['root'] == prior[label] and len(tree['files']) <= 10000
            and len(tree['directories']) <= 10000, 'prior_public_tree_scope_changed')
        for row in tree['directories']:
            checkpoint()
            path = Path(tree['root']) / row['path']
            value = c.private(path)
            c.require(row['device'] == previous_device and value.st_dev == volume['device']
                and value.st_ino == row['inode'] and stat.S_IMODE(value.st_mode) == row['mode'],
                'public_directory_changed_across_remount')
            row['device'] = value.st_dev
        for row in tree['files']:
            checkpoint()
            path = Path(tree['root']) / row['path']
            current = c._copy_file_observation(path)
            c.require(row['device'] == previous_device and current['device'] == volume['device']
                and all(current[key] == row[key] for key in current if key != 'device'),
                'public_file_changed_across_remount')
            row['device'] = current['device']
    row = manifest['codex']; current = c._copy_file_observation(Path(row['path']))
    c.require(row['path'] == prior['codex'] and row['device'] == previous_device
        and current['device'] == volume['device']
        and all(current[key] == row[key] for key in current if key != 'device'),
        'copied_Codex_changed_across_remount')
    row['device'] = current['device']
    return c.write_new(ROOT / 'copies-manifest.json', c.canonical(manifest) + b'\n',
                       maximum=4 * c.MIB)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admit-preparation', action='store_true')
    parser.add_argument('--admission-sha256', required=True)
    args = parser.parse_args()
    c.require(args.admit_preparation and sys.dont_write_bytecode,
              'explicit_preparation_B_required')
    os.umask(0o077); c.private(ROOT)
    admission = c.read_json(ROOT / 'admission.json', args.admission_sha256)
    c.require(admission['status'] == 'prepared_not_executed'
        and admission['harness_root'] == str(ROOT) and admission['source_commit'] == COMMIT,
        'exact_F_admission_required')
    for name, digest in admission['scripts_sha256'].items():
        c.require(c.sha(ROOT / name) == digest, 'script_pin_changed')
    c.require(c.sha(ROOT / 'reviewer-checklist.json') == admission['reviewer_checklist_sha256'],
              'review_checklist_changed')
    for name, digest in admission['source_pins'].items():
        c.require(c.sha(REPO / name) == digest, 'source_pin_changed')
    prior = c.read_json(PRIOR / 'prepared.json', PRIOR_PREPARED)
    closed = c.read_json(PRIOR / 'closed.json', PRIOR_CLOSED)
    active = c.read_json(PRIOR / 'active.json', PRIOR_ACTIVE)
    c.require(prior['commit'] == COMMIT and prior['status'] == 'prepared'
        and prior['full_ci_passed'] is True and prior['artifact_id'] == admission['artifact_id']
        and prior['artifact_zip_sha256'] == admission['artifact_zip_sha256']
        and prior['run_id'] == admission['run_id']
        and prior['run_attempt'] == admission['run_attempt']
        and closed['prepared_sha256'] == active['prepared_sha256'] == PRIOR_PREPARED
        and closed['status'] == 'closed_after_GUI_intent'
        and closed['all_known_identities_absent'] is True and closed['same_lease_free'] is True
        and closed['quota_unmounted_without_force'] is True
        and closed['signals_sent'] == [] and closed['issues'] == []
        and closed['resource_failure'] is None, 'prior_closed_custody_not_admitted')
    started, deadline = time.monotonic(), time.monotonic() + 300
    result = {**prior, 'schema': 'jarvis.native-development-QA-preparation/1',
        'admission_sha256': args.admission_sha256, 'app_launch_attempted': False,
        'provider_baseline': 106471424, 'fresh_test_case': 'F_active_background_isolation',
        'prior_prepared_sha256': PRIOR_PREPARED, 'prior_closed_sha256': PRIOR_CLOSED,
        'prior_profile_modified_by_preparation': False,
        'provider_credentials_read_or_copied': False, 'network_or_downloads': False,
        'local_builds_run': False, 'fresh_F_profile': True, 'status': 'preparing'}
    for key in ('resource_anchor', 'resource_accounting', 'last_resource_observation'):
        result.pop(key, None)
    c.write_json(ROOT / 'preparation-intent.json', {
        'schema': 'jarvis.native-background-F-preparation-intent/1',
        'admission_sha256': args.admission_sha256, 'prior_prepared_sha256': PRIOR_PREPARED,
        'prior_closed_sha256': PRIOR_CLOSED, 'one_remount_maximum': 1,
        'prior_profile_preserved': True, 'new_profile_name': 'state-f',
        'existing_synthetic_provider_reused_only_by_official_client': True,
        'automatic_effect_retry': False, 'deletion': False})

    def checkpoint(*, fresh_provider=False):
        c.require(time.monotonic() < deadline, 'preparation_deadline')
        fs = os.statvfs('/System/Volumes/Data')
        c.require(fs.f_bavail * fs.f_frsize >= c.FLOOR, 'Data_free_floor_reached')
        if 'resource_anchor' in result:
            c.resources(result, fresh_provider=fresh_provider)

    try:
        mount, image = Path(prior['root_quota']), Path(prior['volume']['image'])
        c.require(mount == Path('/private/tmp/jq-8k57q6f0')
            and image == PRIOR / 'quota.sparseimage', 'exact_prior_quota_required')
        underlying = c.private(mount); image_info = c.private(image, 'file')
        c.require(not os.path.ismount(mount)
            and [underlying.st_dev, underlying.st_ino] == prior['underlying_mount_identity']
            and [image_info.st_dev, image_info.st_ino] == prior['volume']['image_identity']
            and image_info.st_blocks * 512 <= c.CAPACITY, 'prior_quota_custody_changed')
        _known_absent(closed); checkpoint()
        result['underlying_mode_before_readmission'] = stat.S_IMODE(underlying.st_mode)
        c.write_json(ROOT / 'volume-intent.json', {'schema': 'jarvis.F-quota-readmission/1',
            'image': str(image), 'image_identity': prior['volume']['image_identity'],
            'root': str(mount), 'underlying_identity': prior['underlying_mount_identity'],
            'prior_closed_sha256': PRIOR_CLOSED, 'capacity_mib': 2048,
            'maximum_attach_calls': 1, 'normal_detach_only': True, 'deletion': False,
            'underlying_mode_before': result['underlying_mode_before_readmission'],
            'underlying_mode_for_attach': 0o700})
        mount.chmod(0o700)
        reopened = mount.lstat()
        c.require([reopened.st_dev, reopened.st_ino] == prior['underlying_mount_identity']
            and not os.path.ismount(mount), 'underlying_root_changed_before_readmission')
        with os.scandir(mount) as entries:
            c.require(next(entries, None) is None, 'underlying_mount_root_not_empty')
        _known_absent(closed); checkpoint()
        attached = plistlib.loads(c.capture(['/usr/bin/hdiutil', 'attach', '-nobrowse',
            '-owners', 'on', '-mountpoint', str(mount), '-plist', str(image)],
            deadline=deadline, checkpoint=checkpoint, maximum=65536)[0])
        c.require(any(row.get('mount-point') == str(mount)
            for row in attached['system-entities']), 'F_remount_not_confirmed')
        mounted = c.private(mount)
        c.require(os.path.ismount(mount) and mounted.st_ino == prior['volume']['inode'],
                  'F_mounted_root_inode_changed')
        result['volume'] = {**prior['volume'], 'device': mounted.st_dev, 'inode': mounted.st_ino}
        result['resource_anchor'] = c.resources(result, fresh_provider=True)
        c.write_json(ROOT / 'volume-mounted.json', result['volume'])
        _known_absent(closed)
        result['prior_lease_identity_after_remount'] = _prior_lease_free(prior, active, result['volume'])
        c.require(c.sha(mount / 'artifact.zip') == admission['artifact_zip_sha256'],
                  'preserved_CI_artifact_digest_changed')
        for key, relative in (('main_sha256', 'Contents/MacOS/Jarvis'),
            ('helper_sha256', 'Contents/Helpers/JarvisQALauncher'),
            ('info_plist_sha256', 'Contents/Info.plist'),
            ('code_resources_sha256', 'Contents/_CodeSignature/CodeResources')):
            checkpoint(); c.require(c.sha(Path(prior['app']) / relative) == prior[key],
                                    'prior_CI_app_bytes_changed')
        result['copies_manifest_sha256'] = _rebind_public_manifest(prior, result['volume'], checkpoint)
        result['copy_manifest_path'] = str(ROOT / 'copies-manifest.json')
        c.verify_copies(result, deadline=min(deadline, time.monotonic() + 20))
        state, home, temporary = mount / 'state-f', mount / 'launcher-home-f', mount / 'launcher-tmp-f'
        for path in (state, home, temporary):
            checkpoint(); c.require(not path.exists(), 'F_profile_already_exists_no_replay')
            path.mkdir(mode=0o700)
        arguments = ['--python', prior['python'], '--source', prior['source'],
            '--dependencies', prior['dependencies'], '--state', str(state),
            '--codex', prior['codex'], '--provider-state', prior['provider']]
        c.require(len(arguments) == 12, 'exact_six_development_flags_required')
        request = {'schema': 'jarvis.qa-launch-request.v1', 'app_path': prior['app'],
            'expected_bundle_id': prior['bundle_identifier'],
            'expected_executable_sha256': prior['main_sha256'], 'arguments': arguments,
            'environment': {'HOME': str(home), 'TMPDIR': str(temporary),
                'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
        result.update(status='prepared', state=str(state), home=str(home), temporary=str(temporary),
            request_sha256=c.write_json(ROOT / 'launch-request.json', request),
            preparation_seconds=round(time.monotonic() - started, 3))
        _known_absent(closed); _prior_lease_free(prior, active, result['volume'])
        result['last_resource_observation'] = c.resources(result, fresh_provider=True)
        result['resource_accounting'] = c.resource_observation()
        pin = c.write_json(ROOT / 'prepared.json', result)
        print(json.dumps({'status': 'prepared', 'prepared_sha256': pin, 'root_quota': str(mount)}))
    except BaseException as error:
        # A failed pre-mount admission can restore only this known empty inode.
        # No retry, deletion or unmount follows an uncertain attachment.
        if 'mount' in locals() and 'underlying_mode_before_readmission' in result:
            try:
                current = mount.lstat()
                if not os.path.ismount(mount) and [current.st_dev, current.st_ino] == prior['underlying_mount_identity']:
                    mount.chmod(result['underlying_mode_before_readmission'])
                    result['unmounted_underlying_mode_restored'] = True
            except OSError:
                result['unmounted_underlying_mode_restored'] = False
        result.update(status='failed_preserved_not_launched',
            error=str(error) if isinstance(error, c.GuardIssue) else type(error).__name__,
            preparation_seconds=round(time.monotonic() - started, 3),
            resource_accounting=c.resource_observation(), originals_deleted=False,
            automatic_detach_or_effect_retry=False)
        c.write_json(ROOT / 'preparation-failed.json', result)
        raise


if __name__ == '__main__':
    main()
