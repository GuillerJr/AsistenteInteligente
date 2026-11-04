"""NOEXEC by import: one admitted CI-native development QA preparation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import stat
import tempfile
import time
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import common as c

ROOT, REPO = c.ROOT, c.REPO
COMMIT = '2c8259de8112aa5f1487814a5c5c35b3931e5c43'
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


def _copy_dependencies(source, destination, *, checkpoint):
    c.require(source == Path(REPO / '.venv.nosync/lib/python3.11/site-packages'),
              'exact_selected_dependencies_required')
    c.require(source.is_dir() and not source.is_symlink(), 'selected_dependencies_unavailable')
    destination.mkdir(mode=0o700)
    pending, files, total, seen = [source], [], 0, set()
    excluded = {'cache': 0, 'site_hook': 0, 'private_name': 0}
    while pending:
        checkpoint(); directory = pending.pop()
        info = directory.lstat()
        c.require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.getuid()}
                  and not info.st_mode & 0o022, 'selected_dependency_directory_custody')
        relative = directory.relative_to(source)
        (destination / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.scandir(directory) as entries:
            for entry in entries:
                checkpoint(); name = entry.name; lower = name.casefold()
                if name == '__pycache__' or lower.endswith(('.pyc', '.pyo')):
                    excluded['cache'] += 1; continue
                if lower.endswith('.pth'):
                    excluded['site_hook'] += 1; continue
                if lower in {'auth.json', 'direct_url.json'} or lower.startswith('.env') or lower.endswith(
                        ('.key', '.p12', '.pfx')) or ' 2.' in name:
                    excluded['private_name'] += 1; continue
                value = entry.stat(follow_symlinks=False)
                c.require(not stat.S_ISLNK(value.st_mode), 'dependency_symlink_not_admitted')
                path = Path(entry.path)
                if stat.S_ISDIR(value.st_mode):
                    pending.append(path); continue
                c.require(stat.S_ISREG(value.st_mode), 'dependency_special_file_not_admitted')
                relative_name = path.relative_to(source).as_posix()
                c.require(relative_name.casefold() not in seen and len(files) < MAX_COPY_FILES,
                          'dependency_copy_collision_or_limit')
                seen.add(relative_name.casefold()); total += value.st_size
                c.require(total <= 256 * c.MIB, 'dependency_copy_bytes_limit')
                row = _copy_public(path, destination / relative_name, checkpoint=checkpoint,
                                   maximum=256 * c.MIB - total + value.st_size)
                files.append({'path': relative_name, **row})
    return {'root': str(destination), 'files': files, 'directories': _directory_rows(destination),
            'bytes': total, 'exclusions': excluded}


def _verify_dependency_versions(tree, expected, *, checkpoint):
    found = {}
    for row in tree['files']:
        name = row['path']
        if name.count('/') != 1 or not name.endswith('.dist-info/METADATA'):
            continue
        checkpoint()
        path = Path(tree['root']) / name
        c.require(path.stat().st_size <= c.MIB, 'package_metadata_limit')
        fields = {}
        for line in path.read_bytes().splitlines():
            if not line: break
            if line.startswith((b'Name:', b'Version:')):
                key, value = line.split(b':', 1)
                fields[key.decode()] = value.strip().decode('utf-8')
        if 'Name' in fields and 'Version' in fields:
            found[fields['Name'].casefold().replace('_', '-')] = fields['Version']
    c.require(all(found.get(name) == version for name, version in expected.items())
        and 'soundfile' not in found, 'selected_local_dependency_versions_changed')
    return {name: found[name] for name in expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admit-preparation', action='store_true')
    parser.add_argument('--admission-sha256', required=True)
    args = parser.parse_args()
    c.require(args.admit_preparation and sys_no_bytecode(), 'explicit_preparation_B_required')
    os.umask(0o077); c.private(ROOT)
    admission = c.read_json(ROOT / 'admission.json', args.admission_sha256)
    c.require(admission['status'] == 'prepared_not_executed'
        and admission['source_commit'] == COMMIT and admission['harness_root'] == str(ROOT)
        and admission['run_id'] == 41842491175 and admission['run_attempt'] == 1,
        'exact_preparation_admission_required')
    for name, digest in admission['scripts_sha256'].items():
        c.require(c.sha(ROOT / name) == digest, 'script_pin_changed')
    c.require(c.sha(ROOT / 'reviewer-checklist.json') == admission['reviewer_checklist_sha256'],
              'review_checklist_changed')
    for name, digest in admission['source_pins'].items():
        c.require(c.sha(REPO / name) == digest, 'source_pin_changed')
    started, deadline = time.monotonic(), time.monotonic() + 300
    result = {'schema': 'jarvis.native-development-QA-preparation/1', 'commit': COMMIT,
        'run_id': admission['run_id'], 'run_attempt': admission['run_attempt'],
        'artifact_id': admission['artifact_id'], 'admission_sha256': args.admission_sha256,
        'provider_baseline': 0, 'app_launch_attempted': False, 'local_builds_run': False,
        'owner_installed_app_modified': False, 'credentials_read': False,
        'distribution_channel': 'development', 'self_contained': False,
        'external_Python_required': True, 'local_dependencies_complete': False,
        'missing_declared_dependency': 'soundfile', 'audio_media_requalification_claimed': False}
    c.write_json(ROOT / 'preparation-intent.json', result)

    def checkpoint(*, fresh_provider=False):
        c.require(time.monotonic() < deadline, 'preparation_deadline')
        fs = os.statvfs('/System/Volumes/Data')
        c.require(fs.f_bavail * fs.f_frsize >= c.FLOOR, 'Data_free_floor_reached')
        if 'volume' in result:
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
        jobs = github(f"actions/runs/{admission['run_id']}/attempts/1/jobs?per_page=100")
        c.require(jobs['total_count'] == 2 and {row['id'] for row in jobs['jobs']}
            == set(admission['job_ids'].values()) and all(row['status'] == 'completed'
            and row['conclusion'] == 'success' and row['head_sha'] == COMMIT for row in jobs['jobs']),
            'exact_CI_jobs_not_successful')
        meta = github(f"actions/artifacts/{admission['artifact_id']}")
        c.require(not meta['expired'] and meta['name'] == 'jarvis-native-qa-' + COMMIT
            and meta['workflow_run']['id'] == admission['run_id']
            and meta['workflow_run']['head_sha'] == COMMIT
            and meta['digest'] == 'sha256:' + admission['artifact_zip_sha256']
            and 0 < meta['size_in_bytes'] <= 33 * c.MIB, 'exact_native_artifact_not_admitted')
        c.require(git('rev-parse', 'HEAD').decode().strip() == COMMIT, 'local_HEAD_changed')
        git('diff', '--exit-code', COMMIT, '--', 'src', 'native')
        local = admission['selected_local_programs']
        for label in ('python', 'python_process', 'codex'):
            path = Path(local[label]['path'])
            c.require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK)
                and c.sha(path) == local[label]['sha256'], 'selected_local_program_changed')
        result['full_ci_passed'] = True
        mount = Path(tempfile.mkdtemp(prefix='jq-', dir='/private/tmp')); mount.chmod(0o700)
        underlying = mount.lstat(); result['root_quota'] = str(mount)
        result['underlying_mount_identity'] = [underlying.st_dev, underlying.st_ino]
        image = ROOT / 'quota.sparseimage'
        c.write_json(ROOT / 'volume-intent.json', {'image': str(image), 'root': str(mount),
            'underlying_identity': result['underlying_mount_identity'], 'capacity_mib': 2048,
            'automatic_retry': False, 'deletion': False})
        command(['/usr/bin/hdiutil', 'create', '-size', '2048m', '-type', 'SPARSE', '-fs',
                 'APFS', '-volname', 'JarvisNativeBackgroundQA', str(image)], maximum=65536)
        attached = plistlib.loads(command(['/usr/bin/hdiutil', 'attach', '-nobrowse',
            '-owners', 'on', '-mountpoint', str(mount), '-plist', str(image)], maximum=65536)[0])
        c.require(any(row.get('mount-point') == str(mount) for row in attached['system-entities']),
                  'mount_not_confirmed')
        mount.chmod(0o700); info, image_info = mount.lstat(), image.lstat()
        result['volume'] = {'root': str(mount), 'image': str(image), 'device': info.st_dev,
            'inode': info.st_ino, 'image_identity': [image_info.st_dev, image_info.st_ino]}
        c.write_json(ROOT / 'volume-mounted.json', result['volume'])
        provider = mount / 'provider'; provider.mkdir(mode=0o700); result['provider'] = str(provider)
        result['resource_anchor'] = c.resources(result); checkpoint()
        archive = mount / 'artifact.zip'
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
                        target = mount / member.name
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
        app = mount / 'Jarvis.app'; main_binary = app / 'Contents/MacOS/Jarvis'
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
        source = mount / 'source'; source.mkdir(mode=0o700)
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
        dependencies = mount / 'dependencies'
        deps = _copy_dependencies(Path(admission['selected_dependencies']['path']), dependencies,
                                  checkpoint=checkpoint)
        versions = _verify_dependency_versions(deps,
            admission['selected_dependencies']['verified_versions'], checkpoint=checkpoint)
        codex = mount / 'bin/codex'
        codex_row = _copy_public(Path(local['codex']['path']), codex, checkpoint=checkpoint,
                                expected_sha256=local['codex']['sha256'])
        manifest = {'schema': 'jarvis.native-QA-copy-manifest/1', 'source_commit': COMMIT,
            'source': {'root': str(source), 'files': source_rows,
                       'directories': _directory_rows(source), 'bytes': source_total},
            'dependencies': deps, 'codex': {'path': str(codex), **codex_row},
            'python': local['python'], 'python_process': local['python_process'],
            'declared_dependency_versions': versions,
            'missing_declared_dependencies': ['soundfile'], 'private_files_or_site_hooks_read': False}
        manifest_path = ROOT / 'copies-manifest.json'
        manifest_pin = c.write_new(manifest_path, c.canonical(manifest) + b'\n', maximum=4 * c.MIB)
        state, home, temporary = mount / 'state', mount / 'launcher-home', mount / 'launcher-tmp'
        for path in (state, home, temporary): path.mkdir(mode=0o700)
        arguments = ['--python', local['python']['path'], '--source', str(source),
            '--dependencies', str(dependencies), '--state', str(state), '--codex', str(codex),
            '--provider-state', str(provider)]
        c.require(len(arguments) == 12, 'development_helper_exact_six_flags_required')
        request = {'schema': 'jarvis.qa-launch-request.v1', 'app_path': str(app),
            'expected_bundle_id': plist['CFBundleIdentifier'], 'expected_executable_sha256': c.sha(main_binary),
            'arguments': arguments, 'environment': {'HOME': str(home), 'TMPDIR': str(temporary),
                'PATH': '/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
        request_pin = c.write_json(ROOT / 'launch-request.json', request)
        result.update(status='prepared', app=str(app), state=str(state), bundle_id=plist['CFBundleIdentifier'],
            bundle_identifier=plist['CFBundleIdentifier'], source=str(source), dependencies=str(dependencies),
            codex=str(codex), python=local['python']['path'],
            python_process_executable=local['python_process']['path'],
            python_sha256=local['python']['sha256'],
            home=str(home), temporary=str(temporary), helper=str(helper), helper_sha256=c.sha(helper),
            main_sha256=c.sha(main_binary), info_plist_sha256=c.sha(info_path),
            code_resources_sha256=c.sha(app / 'Contents/_CodeSignature/CodeResources'),
            request_sha256=request_pin, copies_manifest_sha256=manifest_pin,
            copy_manifest_path=str(manifest_path), artifact_zip_sha256=admission['artifact_zip_sha256'],
            CI_native_app_byte_identical=True, local_app_resigned=False,
            copied_source_files=len(source_rows), copied_dependencies_files=len(deps['files']),
            copied_source_bytes=source_total, copied_dependencies_bytes=deps['bytes'],
            preparation_seconds=round(time.monotonic() - started, 3))
        c.verify_copies(result, deadline=min(deadline, time.monotonic() + 20))
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


def sys_no_bytecode():
    import sys
    return sys.dont_write_bytecode


if __name__ == '__main__':
    main()
