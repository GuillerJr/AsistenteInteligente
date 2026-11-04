"""NOEXEC by import: new case G; closed physical quota and exact new CI-native artifact."""
from __future__ import annotations

import argparse
import fcntl
import sys
import hashlib
import json
import os
import plistlib
import stat
import time
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import common as c

ROOT, REPO = c.ROOT, c.REPO
COMMIT = 'c41378acc0c1f24f6534011f1aab09691a9a6948'
OLD_COMMIT = '2c8259de8112aa5f1487814a5c5c35b3931e5c43'
PRIOR = Path('/private/tmp/jarvis-product-background-native-20261104-f')
IMAGE_ROOT = Path('/private/tmp/jarvis-product-background-native-20261104-e')
PRIOR_PREPARED = '01bab74674e6a2f47b9d058c39965193e0c2e70807dd534789d577d0b979bdcc'
PRIOR_CLOSED = 'f90e0ae56ddcde81b0023e7d31ad43ef8f16ab1d5d0e838adc36774a9e598a46'
PRIOR_ACTIVE = '816694cd02a1d125bf7dc4ee02ab76e1d6815d7e39a68634adbf2a46aeadbb71'
PRIOR_CLEANUP = '73c040c23d678c9a56f42fe562b01cfaf8ce06548d5f794b0849737df9b6a811'
PRIOR_METADATA = '1aeefa22d0bbc1131f4f460e583cb5b15be2f2525b42136b25ca430adb24f962'
GH = '/opt/homebrew/bin/gh'
MAX_COPY_FILES = 10000
APP_FILES = {'Contents/MacOS/Jarvis', 'Contents/Helpers/JarvisQALauncher',
             'Contents/Info.plist', 'Contents/_CodeSignature/CodeResources'}


def _public_file(path, *, maximum):
    value = path.lstat()
    c.require(stat.S_ISREG(value.st_mode) and value.st_uid in {0, os.getuid()}
        and not value.st_mode & 0o022 and 0 <= value.st_size <= maximum,
        'selected_public_input_custody')
    return value


def _directory_rows(root):
    rows = []
    for folder, dirs, _files in os.walk(root, followlinks=False):
        path = Path(folder); value = c.private(path)
        rows.append({'path': '' if path == root else path.relative_to(root).as_posix(),
            'device': value.st_dev, 'inode': value.st_ino, 'mode': stat.S_IMODE(value.st_mode)})
        for name in dirs:
            c.private(path / name)
    return rows


def _copy_public(source, destination, *, checkpoint, expected_sha256=None, expected_blob=None,
                 maximum=512 * c.MIB):
    before = _public_file(source, maximum=maximum)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    c.private(destination.parent)
    incoming = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    outgoing = None
    try:
        outgoing = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest = hashlib.sha256()
        blob = hashlib.sha1(b'blob ' + str(before.st_size).encode() + b'\0')
        count = 0
        opened = os.fstat(incoming)
        c.require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                  'selected_public_input_changed')
        while block := os.read(incoming, 65536):
            checkpoint(); count += len(block)
            c.require(count <= before.st_size, 'selected_public_input_grew')
            digest.update(block); blob.update(block)
            view = memoryview(block)
            while view:
                written = os.write(outgoing, view)
                c.require(written > 0, 'copy_write_failed'); view = view[written:]
        named, after = source.lstat(), os.fstat(incoming)
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        c.require(count == before.st_size and all(getattr(before, key) == getattr(after, key)
            == getattr(named, key) for key in keys), 'selected_public_input_changed')
        result_hash = digest.hexdigest()
        c.require(expected_sha256 is None or result_hash == expected_sha256,
                  'selected_public_input_digest_mismatch')
        c.require(expected_blob is None or blob.hexdigest() == expected_blob,
                  'selected_Git_source_blob_mismatch')
        os.fchmod(outgoing, 0o755 if before.st_mode & 0o111 else 0o644)
        os.fsync(outgoing)
    finally:
        try:
            os.close(incoming)
        finally:
            if outgoing is not None:
                os.close(outgoing)
    return {**c._copy_file_observation(destination), 'sha256': result_hash}


def _known_absent(closed):
    c.require(len(closed['known_processes']) == 4
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
    c.require(manifest['source_commit'] == OLD_COMMIT
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
            c.require(c.sha(path) == row['sha256'], 'public_file_digest_changed')
            row['device'] = current['device']
    row = manifest['codex']; current = c._copy_file_observation(Path(row['path']))
    c.require(row['path'] == prior['codex'] and row['device'] == previous_device
        and current['device'] == volume['device']
        and all(current[key] == row[key] for key in current if key != 'device'),
        'copied_Codex_changed_across_remount')
    c.require(c.sha(row['path']) == row['sha256'], 'copied_Codex_digest_changed')
    row['device'] = current['device']
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admit-preparation', action='store_true')
    parser.add_argument('--admission-sha256', required=True)
    args = parser.parse_args()
    c.require(args.admit_preparation and sys.dont_write_bytecode,
              'explicit_preparation_G_required')
    os.umask(0o077); c.private(ROOT)
    admission = c.read_json(ROOT / 'admission.json', args.admission_sha256)
    c.require(admission['status'] == 'prepared_not_executed'
        and admission['review_status'] == 'frozen_NOEXEC_root_reviewed'
        and admission['harness_root'] == str(ROOT)
        and admission['source_commit'] == COMMIT and len(COMMIT) == 40,
        'exact_G_admission_required_template_not_executable')
    review = c.read_json(ROOT / 'root-review.json')
    c.require(review['admission_sha256'] == args.admission_sha256
        and review['source_commit'] == COMMIT and review['execution_review_passed'] is True,
        'root_exact_review_required')
    for name, digest in admission['scripts_sha256'].items():
        c.require(c.sha(ROOT / name) == digest, 'script_pin_changed')
    c.require(set(admission['scripts_sha256']) == {'common.py', 'prepare.py', 'supervise.py', 'probe.py'}
        and c.sha(ROOT / 'reviewer-checklist.json') == admission['reviewer_checklist_sha256'],
        'full_harness_review_checklist_required')
    for name, digest in admission['source_pins'].items():
        c.require(c.sha(REPO / name) == digest, 'source_pin_changed')
    for name in ('preparation-intent.json', 'volume-intent.json', 'prepared.json', 'preparation-failed.json'):
        c.require(not (ROOT / name).exists(), 'prior_G_preparation_preserved_no_replay')
    prior = c.read_json(PRIOR / 'prepared.json', PRIOR_PREPARED)
    closed = c.read_json(PRIOR / 'closed.json', PRIOR_CLOSED)
    active = c.read_json(PRIOR / 'active.json', PRIOR_ACTIVE)
    cleanup = c.read_json(PRIOR / 'root-cleanup-after-resource-stop.json', PRIOR_CLEANUP)
    metadata = c.read_json(PRIOR / 'provider-metadata-after-stop.json', PRIOR_METADATA)
    c.require(prior['commit'] == OLD_COMMIT and prior['status'] == 'prepared'
        and closed['prepared_sha256'] == active['prepared_sha256'] == PRIOR_PREPARED
        and closed['all_known_identities_absent'] is True and closed['same_lease_free'] is True
        and closed['resource_failure'] == 'quota_owned_provider_sampled_limit'
        and len(closed['signals_sent']) == 1
        and cleanup['original_closed_sha256'] == PRIOR_CLOSED
        and cleanup['status'] == 'resource_stop_closed_quota_preserved'
        and cleanup['all_known_identities_absent'] is True and cleanup['same_lease_free'] is True
        and cleanup['quota_unmounted_without_force'] is True
        and cleanup['provider_budget_extended'] is False and cleanup['resource_breach_preserved'] is True
        and cleanup['signals_sent_by_root'] == [] and cleanup['test_case_passed'] is False
        and metadata['allocated_bytes'] == 170102784 and metadata['entry_count'] == 8442
        and metadata['contents_read'] is False and metadata['auth_read'] is False,
        'F_failure_and_normal_root_cleanup_required')
    started, deadline = time.monotonic(), time.monotonic() + 300
    result = {**prior, 'schema': 'jarvis.native-development-QA-preparation/1',
        'commit': COMMIT, 'run_id': admission['run_id'], 'run_attempt': admission['run_attempt'],
        'artifact_id': admission['artifact_id'], 'admission_sha256': args.admission_sha256,
        'app_launch_attempted': False, 'full_ci_passed': False,
        'CI_native_app_byte_identical': False, 'provider_baseline': 170102784,
        'provider_absolute_limit': 170102784 + 64 * c.MIB,
        'fresh_test_case': 'G_active_background_isolation_after_plugin_fetch_suppression',
        'prior_prepared_sha256': PRIOR_PREPARED, 'prior_closed_sha256': PRIOR_CLOSED,
        'prior_root_cleanup_sha256': PRIOR_CLEANUP, 'prior_F_failure_preserved': True,
        'prior_profile_modified_by_preparation': False, 'provider_credentials_read_or_copied': False,
        'network_or_downloads': 'exact_new_native_CI_artifact_only',
        'local_builds_run': False, 'fresh_G_profile': True, 'status': 'preparing'}
    for key in ('resource_anchor', 'resource_accounting', 'last_resource_observation', 'fresh_F_profile'):
        result.pop(key, None)
    c.write_json(ROOT / 'preparation-intent.json', {
        'schema': 'jarvis.native-background-G-preparation-intent/1',
        'admission_sha256': args.admission_sha256, 'prior_prepared_sha256': PRIOR_PREPARED,
        'prior_closed_sha256': PRIOR_CLOSED, 'prior_cleanup_sha256': PRIOR_CLEANUP,
        'one_remount_maximum': 1, 'prior_profiles_and_app_preserved': True,
        'new_profile_name': 'state-g', 'new_source_name': 'source-g', 'new_app_name': 'Jarvis-G.app',
        'existing_synthetic_provider_reused_only_by_official_client': True,
        'automatic_effect_retry': False, 'deletion': False})

    def checkpoint(*, fresh_provider=False):
        c.require(time.monotonic() < deadline, 'preparation_deadline')
        fs = os.statvfs('/System/Volumes/Data')
        c.require(fs.f_bavail * fs.f_frsize >= c.FLOOR, 'Data_free_floor_reached')
        if 'resource_anchor' in result:
            c.resources(result, fresh_provider=fresh_provider)

    def command(argv, **kwargs):
        checkpoint(fresh_provider=True)
        return c.capture(argv, deadline=deadline, checkpoint=checkpoint, **kwargs)

    def git(*arguments):
        return command(['/usr/bin/git', '-c', 'core.fsmonitor=false', *arguments],
            environment={'PATH': '/usr/bin:/bin', 'HOME': str(ROOT),
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})[0]

    def github(path):
        return json.loads(command([GH, 'api', '--hostname', 'github.com',
            'repos/GuillerJr/AsistenteInteligente/' + path])[0])

    try:
        run = github(f"actions/runs/{admission['run_id']}")
        c.require(run['head_sha'] == COMMIT and run['status'] == 'completed'
            and run['conclusion'] == 'success' and run['run_attempt'] == admission['run_attempt']
            and run['name'] == 'Base propia de Jarvis', 'exact_CI_not_successful')
        jobs = github(f"actions/runs/{admission['run_id']}/attempts/{admission['run_attempt']}/jobs?per_page=100")
        c.require(jobs['total_count'] == 2 and {row['id'] for row in jobs['jobs']}
            == set(admission['job_ids'].values()) and all(row['status'] == 'completed'
            and row['conclusion'] == 'success' and row['head_sha'] == COMMIT for row in jobs['jobs']),
            'exact_CI_jobs_not_successful')
        meta = github(f"actions/artifacts/{admission['artifact_id']}")
        c.require(not meta['expired'] and meta['name'] == 'jarvis-native-qa-' + COMMIT
            and meta['workflow_run']['id'] == admission['run_id']
            and meta['workflow_run']['head_sha'] == COMMIT
            and meta['digest'] == 'sha256:' + admission['artifact_zip_sha256']
            and meta['size_in_bytes'] == admission['artifact_bytes_observed']
            and 0 < meta['size_in_bytes'] <= 33 * c.MIB, 'exact_native_artifact_not_admitted')
        c.require(git('rev-parse', 'HEAD').decode().strip() == COMMIT, 'local_HEAD_changed')
        git('diff', '--exit-code', COMMIT, '--', 'src', 'native')
        local = admission['selected_local_programs']
        for label in ('python', 'python_process'):
            path = Path(local[label]['path'])
            c.require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK)
                and c.sha(path) == local[label]['sha256'], 'selected_local_program_changed')
        result['full_ci_passed'] = True
        mount, image = Path(prior['root_quota']), Path(prior['volume']['image'])
        c.require(mount == Path('/private/tmp/jq-8k57q6f0')
            and image == IMAGE_ROOT / 'quota.sparseimage', 'exact_prior_quota_required')
        underlying = c.private(mount); image_info = c.private(image, 'file')
        c.require(not os.path.ismount(mount)
            and [underlying.st_dev, underlying.st_ino] == prior['underlying_mount_identity']
            and [image_info.st_dev, image_info.st_ino] == prior['volume']['image_identity']
            and image_info.st_blocks * 512 <= c.CAPACITY, 'prior_quota_custody_changed')
        _known_absent(closed); checkpoint()
        result['underlying_mode_before_readmission'] = stat.S_IMODE(underlying.st_mode)
        c.write_json(ROOT / 'volume-intent.json', {'schema': 'jarvis.G-quota-readmission/1',
            'image': str(image), 'image_identity': prior['volume']['image_identity'],
            'root': str(mount), 'underlying_identity': prior['underlying_mount_identity'],
            'prior_closed_sha256': PRIOR_CLOSED, 'prior_cleanup_sha256': PRIOR_CLEANUP,
            'capacity_mib': 2048, 'maximum_attach_calls': 1, 'normal_detach_only': True,
            'deletion': False, 'underlying_mode_before': result['underlying_mode_before_readmission']})
        mount.chmod(0o700)
        reopened = mount.lstat()
        c.require([reopened.st_dev, reopened.st_ino] == prior['underlying_mount_identity']
            and not os.path.ismount(mount), 'underlying_root_changed_before_readmission')
        with os.scandir(mount) as entries:
            c.require(next(entries, None) is None, 'underlying_mount_root_not_empty')
        _known_absent(closed); checkpoint()
        attached = plistlib.loads(command(['/usr/bin/hdiutil', 'attach', '-nobrowse',
            '-owners', 'on', '-mountpoint', str(mount), '-plist', str(image)], maximum=65536)[0])
        c.require(any(row.get('mount-point') == str(mount)
            for row in attached['system-entities']), 'G_remount_not_confirmed')
        mounted = c.private(mount)
        c.require(os.path.ismount(mount) and mounted.st_ino == prior['volume']['inode'],
                  'G_mounted_root_inode_changed')
        result['volume'] = {**prior['volume'], 'device': mounted.st_dev, 'inode': mounted.st_ino}
        result['resource_anchor'] = c.resources(result, fresh_provider=True)
        c.write_json(ROOT / 'volume-mounted.json', result['volume'])
        _known_absent(closed)
        result['prior_lease_identity_after_remount'] = _prior_lease_free(prior, active, result['volume'])
        c.require(active['lease_identity'][1] == 11385, 'exact_F_lease_required')
        manifest = _rebind_public_manifest(prior, result['volume'], checkpoint)
        for path in (mount / 'artifact-g.zip', mount / 'Jarvis-G.app', mount / 'source-g',
                     mount / 'state-g', mount / 'launcher-home-g', mount / 'launcher-tmp-g'):
            c.require(not path.exists(), 'G_destination_already_exists_no_replay')
        archive = mount / 'artifact-g.zip'
        _, count = command([GH, 'api', '--hostname', 'github.com',
            f"repos/GuillerJr/AsistenteInteligente/actions/artifacts/{admission['artifact_id']}/zip"],
            output=archive, maximum=meta['size_in_bytes'])
        c.require(count == meta['size_in_bytes'] and c.sha(archive) == admission['artifact_zip_sha256'],
                  'native_artifact_download_digest')
        with zipfile.ZipFile(archive) as zipped:
            c.require(set(zipped.namelist()) == {'Jarvis-native-qa-arm64.tar.gz',
                'Jarvis-native-qa-provenance.json'} and len(zipped.infolist()) == 2
                and all(not row.flag_bits & 1 and not stat.S_ISLNK(row.external_attr >> 16)
                        for row in zipped.infolist()), 'native_ZIP_members_or_types')
            c.require(zipped.getinfo('Jarvis-native-qa-provenance.json').file_size <= 65536,
                      'native_provenance_size')
            provenance = json.loads(zipped.read('Jarvis-native-qa-provenance.json'))
            c.require(provenance['format'] == 'jarvis-native-qa-ci/1'
                and provenance['source_commit'] == COMMIT
                and str(provenance['run_id']) == str(admission['run_id'])
                and provenance['distribution_channel'] == 'development'
                and provenance['self_contained'] is False
                and all(provenance['external_' + name + '_required'] is True
                        for name in ('python', 'codex', 'source', 'dependencies'))
                and provenance['code_text_unchanged'] is True and provenance['helper_unchanged'] is True
                and provenance['full_workflow_success_claimed'] is False
                and provenance['export_script_sha256'] == admission['source_pins']['script/export_native_qa.py'],
                'native_provenance_changed')
            with zipped.open('Jarvis-native-qa-arm64.tar.gz') as compressed:
                digest, size = hashlib.sha256(), 0
                while block := compressed.read(512 * 1024):
                    checkpoint(); digest.update(block); size += len(block)
                    c.require(size <= 32 * c.MIB, 'native_compressed_limit')
                c.require(size == provenance['archive_bytes']
                    and digest.hexdigest() == provenance['archive_sha256'], 'native_tar_digest')
            seen, total = set(), 0
            with zipped.open('Jarvis-native-qa-arm64.tar.gz') as compressed:
                with tarfile.open(fileobj=compressed, mode='r|gz') as tar:
                    for member in tar:
                        checkpoint(); path = PurePosixPath(member.name)
                        c.require(member.isreg() and path.parts[0] == 'Jarvis.app'
                            and '/'.join(path.parts[1:]) in APP_FILES
                            and str(path) == member.name and member.name not in seen,
                            'native_tar_fixed_members_required')
                        seen.add(member.name); total += member.size
                        c.require(0 < member.size <= 128 * c.MIB and total <= 128 * c.MIB,
                                  'native_uncompressed_limit')
                        target = mount / 'Jarvis-G.app' / Path(*path.parts[1:])
                        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                        with os.fdopen(fd, 'wb') as out, tar.extractfile(member) as data:
                            written = 0
                            while block := data.read(65536):
                                checkpoint(); written += len(block)
                                c.require(written <= member.size, 'native_tar_entry_grew'); out.write(block)
                            c.require(written == member.size, 'native_tar_entry_truncated')
                            os.fchmod(out.fileno(), 0o755 if member.mode & 0o111 else 0o644)
            c.require(seen == {'Jarvis.app/' + name for name in APP_FILES}, 'native_tar_incomplete')
        app = mount / 'Jarvis-G.app'; main_binary = app / 'Contents/MacOS/Jarvis'
        helper = app / 'Contents/Helpers/JarvisQALauncher'; info_path = app / 'Contents/Info.plist'
        plist = plistlib.loads(info_path.read_bytes())
        c.require(plist['JarvisDistributionChannel'] == 'development'
            and not any(key in plist for key in ('JarvisRuntimeManifestSHA256',
                                                 'JarvisQualificationConfigurationSHA256'))
            and not (app / 'Contents/Resources').exists()
            and plist['CFBundleIdentifier'] == provenance['bundle_identifier']
            == 'local.jarvis.qa.native.' + COMMIT[:12], 'native_development_bundle_required')
        native_pins = {row['path']: row for row in provenance['files']}
        c.require(set(native_pins) == APP_FILES and len(provenance['files']) == 4,
                  'native_provenance_member_set')
        for name, row in native_pins.items():
            c.require((app / name).stat().st_size == row['bytes']
                and c.sha(app / name) == row['sha256'], 'native_member_digest_changed')
        c.require(c.sha(main_binary) == provenance['binary_sha256']
            and c.sha(helper) == provenance['qa_launcher_sha256']
            and c.sha(info_path) == provenance['info_plist_sha256']
            and c.sha(app / 'Contents/_CodeSignature/CodeResources') == provenance['code_resources_sha256'],
            'native_binary_metadata_pin_changed')
        sections = [{'name': row['section'], 'offset': row['offset'], 'bytes': row['size'],
                     'sha256': row['sha256']} for row in c.text_sections(main_binary)]
        c.require(sections == provenance['code_text_sections'], 'native_TEXT_sections_changed')
        command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], maximum=65536)
        metadata = command(['/usr/bin/codesign', '-d', '--verbose=2', str(app)],
            maximum=8192, stderr_max=8192, include_stderr=True)[0]
        c.require([line for line in metadata.splitlines() if line.startswith(b'Identifier=')]
            == [('Identifier=' + plist['CFBundleIdentifier']).encode()], 'native_signing_ID_mismatch')
        source = mount / 'source-g'; source.mkdir(mode=0o700)
        source_rows, source_total = [], 0
        tree = git('ls-tree', '-r', '-z', COMMIT, '--', 'src')
        for item in tree.split(b'\0'):
            if not item: continue
            header, encoded = item.split(b'\t', 1); mode, kind, blob = header.split()
            path = Path(os.fsdecode(encoded)); relative = path.relative_to('src')
            c.require(kind == b'blob' and mode in {b'100644', b'100755'}
                and relative.parts and all(part not in {'', '.', '..'} for part in relative.parts)
                and not any(part.casefold() == 'auth.json' or part.endswith('.key') for part in relative.parts)
                and len(source_rows) < 1000, 'Git_source_path_not_admitted')
            row = _copy_public(REPO / path, source / relative, checkpoint=checkpoint,
                               expected_blob=blob.decode(), maximum=32 * c.MIB - source_total)
            source_total += row['size']; c.require(source_total <= 32 * c.MIB, 'source_copy_limit')
            source_rows.append({'path': relative.as_posix(), **row})
        c.require(source_rows, 'Git_source_copy_empty')
        manifest.update(source_commit=COMMIT, dependency_origin_commit=OLD_COMMIT,
            source={'root': str(source), 'files': source_rows,
                    'directories': _directory_rows(source), 'bytes': source_total})
        manifest_path = ROOT / 'copies-manifest.json'
        manifest_pin = c.write_new(manifest_path, c.canonical(manifest) + b'\n', maximum=4 * c.MIB)
        dependencies, codex = mount / 'dependencies', mount / 'bin/codex'
        state, home, temporary = mount / 'state-g', mount / 'launcher-home-g', mount / 'launcher-tmp-g'
        for path in (state, home, temporary): path.mkdir(mode=0o700)
        arguments = ['--python', prior['python'], '--source', str(source),
            '--dependencies', str(dependencies), '--state', str(state), '--codex', str(codex),
            '--provider-state', prior['provider']]
        c.require(len(arguments) == 12, 'development_helper_exact_six_flags_required')
        request = {'schema': 'jarvis.qa-launch-request.v1', 'app_path': str(app),
            'expected_bundle_id': plist['CFBundleIdentifier'], 'expected_executable_sha256': c.sha(main_binary),
            'arguments': arguments, 'environment': {'HOME': str(home), 'TMPDIR': str(temporary),
                'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
        request_pin = c.write_json(ROOT / 'launch-request.json', request)
        result.update(status='prepared', app=str(app), state=str(state), bundle_id=plist['CFBundleIdentifier'],
            bundle_identifier=plist['CFBundleIdentifier'], source=str(source), dependencies=str(dependencies),
            codex=str(codex), python=prior['python'], python_process_executable=prior['python_process_executable'],
            python_sha256=prior['python_sha256'], home=str(home), temporary=str(temporary), helper=str(helper),
            helper_sha256=c.sha(helper), main_sha256=c.sha(main_binary), info_plist_sha256=c.sha(info_path),
            code_resources_sha256=c.sha(app / 'Contents/_CodeSignature/CodeResources'), request_sha256=request_pin,
            copies_manifest_sha256=manifest_pin, copy_manifest_path=str(manifest_path),
            artifact_zip_sha256=admission['artifact_zip_sha256'], CI_native_app_byte_identical=True,
            local_app_resigned=False, copied_source_files=len(source_rows), copied_source_bytes=source_total,
            preparation_seconds=round(time.monotonic() - started, 3))
        _known_absent(closed); _prior_lease_free(prior, active, result['volume'])
        c.verify_copies(result, deadline=min(deadline, time.monotonic() + 20))
        result['last_resource_observation'] = c.resources(result, fresh_provider=True)
        result['resource_accounting'] = c.resource_observation()
        pin = c.write_json(ROOT / 'prepared.json', result)
        print(json.dumps({'status': 'prepared', 'prepared_sha256': pin, 'root_quota': str(mount)}))
    except BaseException as error:
        # Never retry attachment/download/launch, delete evidence or detach uncertain custody.
        if 'mount' in locals() and 'underlying_mode_before_readmission' in result:
            try:
                current = mount.lstat()
                if not os.path.ismount(mount) and [current.st_dev, current.st_ino] == prior['underlying_mount_identity']:
                    mount.chmod(result['underlying_mode_before_readmission'])
                    result['unmounted_underlying_mode_restored'] = True
            except OSError:
                result['unmounted_underlying_mode_restored'] = False
        result.update(status='failed_preserved_not_launched', error=str(error)
            if isinstance(error, c.GuardIssue) else type(error).__name__,
            preparation_seconds=round(time.monotonic() - started, 3),
            resource_accounting=c.resource_observation(), originals_deleted=False,
            automatic_detach_or_effect_retry=False)
        c.write_json(ROOT / 'preparation-failed.json', result)
        raise


if __name__ == '__main__':
    main()
