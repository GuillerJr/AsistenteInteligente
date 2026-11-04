"""Explicit fresh CI-artifact preparation only. No app launch or local build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import stat
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import common as c

ROOT, REPO = c.ROOT, c.REPO
COMMIT = '1b0a7ead49f2659ef536d47947a9ed7364d0e632'
GH = '/opt/homebrew/bin/gh'
SCRIPTS = ('common.py', 'prepare.py', 'supervise.py', 'probe.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admit-preparation', action='store_true')
    parser.add_argument('--admission-sha256', required=True)
    args = parser.parse_args()
    c.require(args.admit_preparation and sys.dont_write_bytecode, 'explicit_preparation_B_required')
    os.umask(0o077); c.private(ROOT)
    admission = c.read_json(ROOT / 'admission.json', args.admission_sha256)
    c.require(admission['status'] == 'prepared_not_executed'
        and admission['source_commit'] == COMMIT and admission['harness_root'] == str(ROOT)
        and admission['run_id'] == 41123446861 and admission['run_attempt'] == 2,
        'exact_preparation_admission_required')
    c.require(c.sha(ROOT / 'reviewer-checklist.json') == admission['reviewer_checklist_sha256'],
              'review_checklist_changed')
    for name in SCRIPTS:
        c.require(c.sha(ROOT / name) == admission['scripts_sha256'][name], 'script_pin_changed')
    for name, pin in admission['source_pins'].items():
        c.require(c.sha(REPO / name) == pin, 'source_pin_changed')
    started, deadline = time.monotonic(), time.monotonic() + 300
    result = {'schema': 'jarvis.background-preparation/1', 'commit': COMMIT,
        'run_id': admission['run_id'], 'run_attempt': admission['run_attempt'],
        'artifact_id': admission['artifact_id'], 'admission_sha256': args.admission_sha256,
        'provider_baseline': c.allocation(c.PROVIDER), 'provider': str(c.PROVIDER),
        'app_launch_attempted': False, 'local_builds_run': False,
        'owner_installed_app_modified': False, 'credentials_read': False}
    c.write_json(ROOT / 'preparation-intent.json', result)

    def checkpoint():
        c.require(time.monotonic() < deadline, 'preparation_deadline')
        fs = os.statvfs('/System/Volumes/Data')
        c.require(fs.f_bavail * fs.f_frsize >= c.FLOOR, 'Data_free_floor_reached')
        if 'volume' in result:
            c.resources(result)

    def command(argv, **kwargs):
        return c.capture(argv, deadline=deadline, checkpoint=checkpoint, **kwargs)

    def github(path):
        return json.loads(command([GH, 'api', '--hostname', 'github.com', 'repos/GuillerJr/AsistenteInteligente/' + path])[0])

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
        c.require(not meta['expired'] and meta['name'] == 'jarvis-desktop-' + COMMIT
            and meta['workflow_run']['id'] == admission['run_id']
            and meta['workflow_run']['head_sha'] == COMMIT
            and meta['digest'] == 'sha256:' + admission['artifact_zip_sha256']
            and 0 < meta['size_in_bytes'] <= 448 * c.MIB, 'exact_artifact_not_admitted')
        command(['/usr/bin/git', 'diff', '--exit-code', COMMIT, '--', 'src', 'native',
            'script/test_resource_guard.py', 'script/package_desktop_runtime.py',
            'script/qualify_bundled_runtime.py'],
            environment={'PATH': '/usr/bin:/bin', 'HOME': str(ROOT),
                'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'})
        result['full_ci_passed'] = True
        mount = Path(tempfile.mkdtemp(prefix='jq-', dir='/private/tmp')); mount.chmod(0o700)
        underlying = mount.lstat()
        result['root_quota'] = str(mount)
        result['underlying_mount_identity'] = [underlying.st_dev, underlying.st_ino]
        image = ROOT / 'quota.sparseimage'
        c.write_json(ROOT / 'volume-intent.json', {'image': str(image), 'root': str(mount),
            'underlying_identity': result['underlying_mount_identity'], 'capacity_mib': 2048,
            'automatic_retry': False, 'deletion': False})
        command(['/usr/bin/hdiutil', 'create', '-size', '2048m', '-type', 'SPARSE', '-fs',
                 'APFS', '-volname', 'JarvisBackgroundQA', str(image)], maximum=65536)
        attached = plistlib.loads(command(['/usr/bin/hdiutil', 'attach', '-nobrowse',
            '-owners', 'on', '-mountpoint', str(mount), '-plist', str(image)], maximum=65536)[0])
        c.require(any(row.get('mount-point') == str(mount) for row in attached['system-entities']),
                  'mount_not_confirmed')
        mount.chmod(0o700); info, image_info = mount.lstat(), image.lstat()
        result['volume'] = {'root': str(mount), 'image': str(image), 'device': info.st_dev,
            'inode': info.st_ino, 'image_identity': [image_info.st_dev, image_info.st_ino]}
        c.write_json(ROOT / 'volume-mounted.json', result['volume'])
        result['resource_anchor'] = c.resources(result)
        checkpoint()
        archive = mount / 'artifact.zip'
        _, size = command([GH, 'api', '--hostname', 'github.com', f"repos/GuillerJr/AsistenteInteligente/actions/artifacts/{admission['artifact_id']}/zip"],
                          output=archive, maximum=meta['size_in_bytes'])
        c.require(size == meta['size_in_bytes'] and c.sha(archive) == admission['artifact_zip_sha256'],
                  'artifact_download_digest')
        total, entries, seen = 0, 0, set()
        with zipfile.ZipFile(archive) as zipped:
            c.require(set(zipped.namelist()) == {'Jarvis-macos-arm64.tar.gz', 'Jarvis-provenance.json'}
                and len(zipped.infolist()) == 2 and all(not item.flag_bits & 1
                and not stat.S_ISLNK(item.external_attr >> 16) for item in zipped.infolist()),
                'ZIP_members_or_type')
            c.require(zipped.getinfo('Jarvis-provenance.json').file_size <= 65536,
                      'provenance_limit')
            provenance = json.loads(zipped.read('Jarvis-provenance.json'))
            c.require(provenance['format'] == 'jarvis-desktop-ci/2' and provenance['commit'] == COMMIT
                and str(provenance['run_id']) == str(admission['run_id'])
                and provenance['external_python_required'] is False
                and provenance['external_codex_required'] is False
                and provenance['qa_launcher_bundled'] is True, 'provenance_changed')
            with zipped.open('Jarvis-macos-arm64.tar.gz') as compressed:
                digest, count = hashlib.sha256(), 0
                while block := compressed.read(512 * 1024):
                    checkpoint(); digest.update(block); count += len(block)
                    c.require(count <= 448 * c.MIB, 'tar_compressed_limit')
                c.require(count == provenance['archive_bytes']
                    and digest.hexdigest() == provenance['archive_sha256'], 'tar_digest')
            with zipped.open('Jarvis-macos-arm64.tar.gz') as compressed:
                with tarfile.open(fileobj=compressed, mode='r|gz') as tar:
                    for member in tar:
                        checkpoint(); entries += 1
                        name = member.name.rstrip('/')
                        path = PurePosixPath(name)
                        c.require(not path.is_absolute() and path.parts and path.parts[0] == 'Jarvis.app'
                            and all(part not in {'', '.', '..'} for part in path.parts)
                            and str(path) == name and '\\' not in name
                            and name.casefold() not in seen and entries <= 65536,
                            'tar_member_path_or_collision')
                        seen.add(name.casefold()); target = mount / name
                        c.require(member.isdir() or member.isreg(), 'tar_nonphysical_member')
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                        for parent in (target.parent, *target.parent.parents):
                            if parent == mount.parent: break
                            pin = parent.lstat()
                            c.require(stat.S_ISDIR(pin.st_mode) and pin.st_uid == os.getuid()
                                and not pin.st_mode & 0o022, 'tar_parent_custody')
                        if member.isdir():
                            target.mkdir(mode=0o755, exist_ok=True); continue
                        total += member.size
                        c.require(0 <= member.size <= 512 * c.MIB and total <= 1536 * c.MIB,
                                  'tar_expansion_limit')
                        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                        with os.fdopen(fd, 'wb') as out, tar.extractfile(member) as stream:
                            written = 0
                            while block := stream.read(512 * 1024):
                                checkpoint(); written += len(block)
                                c.require(written <= member.size, 'tar_entry_grew'); out.write(block)
                            c.require(written == member.size, 'tar_entry_truncated')
                            os.fchmod(out.fileno(), 0o755 if member.mode & 0o111 else 0o644)
        app = mount / 'Jarvis.app'; resources = app / 'Contents/Resources'
        main_binary = app / 'Contents/MacOS/Jarvis'; helper = app / 'Contents/Helpers/JarvisQALauncher'
        c.require(c.sha(main_binary) == provenance['binary_sha256']
            and c.sha(helper) == provenance['qa_launcher_sha256'], 'original_binary_digest')
        command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], maximum=65536)
        original_info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        metadata = command(['/usr/bin/codesign', '-d', '--verbose=2', str(app)],
            maximum=8192, stderr_max=8192, include_stderr=True)[0]
        c.require([line for line in metadata.splitlines() if line.startswith(b'Identifier=')]
            == [('Identifier=' + original_info['CFBundleIdentifier']).encode()],
            'original_signing_identifier_mismatch')
        original_text, helper_pin = c.text_sections(main_binary), c.sha(helper)
        info_path = app / 'Contents/Info.plist'; info = plistlib.loads(info_path.read_bytes())
        manifest_pin = info['JarvisRuntimeManifestSHA256']
        c.require(manifest_pin == provenance['runtime_manifest_sha256'], 'manifest_provenance_pin')
        sys.path.insert(0, str(REPO / 'src'))
        from jarvis_core.bundled_runtime import verify_runtime_tree
        runtime = verify_runtime_tree(resources / 'JarvisRuntime',
            expected_manifest_sha256=manifest_pin, expected_source_commit=COMMIT)
        state = mount / 'state'; home = mount / 'launcher-home'; temporary = mount / 'launcher-tmp'
        for path in (state, home, temporary): path.mkdir(mode=0o700)
        config = {'format': 'jarvis.qualification-profile.v1', 'state': str(state),
                  'provider_state': str(c.PROVIDER)}
        config_pin = c.write_new(resources / 'JarvisQualification.json', c.canonical(config))
        bundle_id = admission['bundle_identifier']
        info.update(CFBundleIdentifier=bundle_id, CFBundleName='Jarvis Fondo Prueba',
            CFBundleDisplayName='Jarvis Fondo Prueba', JarvisDistributionChannel='self-contained-qualification',
            JarvisQualificationConfigurationSHA256=config_pin)
        info_path.write_bytes(plistlib.dumps(info, sort_keys=True))
        command(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', bundle_id, str(app)],
                maximum=65536)
        command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], maximum=65536)
        metadata = command(['/usr/bin/codesign', '-d', '--verbose=2', str(app)],
            maximum=8192, stderr_max=8192, include_stderr=True)[0]
        c.require([line for line in metadata.splitlines() if line.startswith(b'Identifier=')]
            == [('Identifier=' + bundle_id).encode()], 'QA_signing_identifier_mismatch')
        c.require(c.text_sections(main_binary) == original_text and c.sha(helper) == helper_pin,
                  'native_text_or_helper_changed')
        verify_runtime_tree(resources / 'JarvisRuntime', expected_manifest_sha256=manifest_pin,
                            expected_source_commit=COMMIT)
        request = {'schema': 'jarvis.qa-launch-request.v1', 'app_path': str(app),
            'expected_bundle_id': bundle_id, 'expected_executable_sha256': c.sha(main_binary),
            'arguments': [], 'environment': {'HOME': str(home), 'TMPDIR': str(temporary),
                'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
        request_pin = c.write_json(ROOT / 'launch-request.json', request)
        result.update(status='prepared', app=str(app), state=str(state), bundle_identifier=bundle_id, bundle_id=bundle_id,
            home=str(home), temporary=str(temporary), helper=str(helper), helper_sha256=helper_pin,
            main_sha256=c.sha(main_binary), manifest_sha256=manifest_pin,
            configuration_sha256=config_pin, request_sha256=request_pin,
            artifact_zip_sha256=admission['artifact_zip_sha256'], runtime_bytes=runtime.bytes,
            runtime_files=runtime.files, native_text_sections_unchanged=True, helper_unchanged=True,
            outer_app_only_resigned=True, extraction_bytes=total, extraction_entries=entries,
            preparation_seconds=round(time.monotonic() - started, 3))
        result['last_resource_observation'] = c.resources(result)
        result['resource_accounting'] = c.resource_observation()
        pin = c.write_json(ROOT / 'prepared.json', result)
        print(json.dumps({'status': 'prepared', 'prepared_sha256': pin, 'root_quota': str(mount)}))
    except BaseException as error:
        result.update(status='failed_preserved_not_launched', error=str(error)
            if isinstance(error, c.GuardIssue) else type(error).__name__,
            preparation_seconds=round(time.monotonic() - started, 3),
            resource_accounting=c.resource_observation(), originals_deleted=False)
        c.write_json(ROOT / 'preparation-failed.json', result)
        raise


if __name__ == '__main__':
    main()
