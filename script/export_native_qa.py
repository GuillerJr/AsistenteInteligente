#!/usr/bin/env python3
"""Export only the already-compiled native QA app in remote CI.

This is a development bundle requiring explicitly selected external Python,
source, dependencies and Codex. It is never a self-contained distribution.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import plistlib
import re
import selectors
import stat
import struct
import subprocess
import sys
import tarfile
import time
from pathlib import Path

MIB = 1024**2
MAX_UNCOMPRESSED = 128 * MIB
MAX_COMPRESSED = 32 * MIB
MAX_METADATA = MIB
COPY_FILES = ('Contents/MacOS/Jarvis', 'Contents/Helpers/JarvisQALauncher',
              'Contents/Info.plist')
SEAL = 'Contents/_CodeSignature/CodeResources'
ARCHIVE_FILES = (*COPY_FILES, SEAL)
ARCHIVE_NAME = 'Jarvis-native-qa-arm64.tar.gz'
PROVENANCE_NAME = 'Jarvis-native-qa-provenance.json'


class ExportError(RuntimeError):
    """Bounded stable errors contain no source file contents or tool output."""


def require(condition, code):
    if not condition:
        raise ExportError(code)


def _check(deadline):
    require(time.monotonic() < deadline, 'native_qa_export_deadline')


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _regular(path, *, maximum, deadline):
    """Read a bounded regular inode, checking its name again before returning."""
    _check(deadline)
    named = path.lstat()
    require(stat.S_ISREG(named.st_mode) and named.st_uid == os.getuid()
            and named.st_nlink == 1 and not named.st_mode & 0o022
            and 0 < named.st_size <= maximum, 'native_qa_input_file_custody_or_size')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        chunks, size = [], 0
        while block := os.read(fd, min(65536, maximum + 1 - size)):
            _check(deadline)
            size += len(block)
            require(size <= maximum, 'native_qa_input_grew')
            chunks.append(block)
        after, current = os.fstat(fd), path.lstat()
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(size == before.st_size and all(getattr(named, key) == getattr(before, key)
                == getattr(after, key) == getattr(current, key) for key in keys),
                'native_qa_input_changed')
        return b''.join(chunks)
    finally:
        os.close(fd)


def _directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o022, 'native_qa_directory_custody')
    return info.st_dev, info.st_ino


def _source_ancestors(app, temporary):
    anchor = temporary if app.is_relative_to(temporary) else Path('/private/tmp')
    require(app.is_relative_to(anchor), 'native_qa_source_anchor_required')
    for parent in reversed(app.parents):
        if parent != anchor and parent.is_relative_to(anchor):
            _directory(parent)


def _file_within(app, relative, *, maximum, deadline):
    require(relative in {*COPY_FILES, SEAL,
        'Contents/Resources/JarvisRuntimeQualification.json'}, 'native_qa_file_not_allowlisted')
    _directory(app)
    path = app
    for part in Path(relative).parts[:-1]:
        path /= part
        _directory(path)
    return _regular(app / relative, maximum=maximum, deadline=deadline)


def text_sections(raw):
    """Read only bounded arm64 MH_EXECUTE section metadata and section hashes."""
    require(32 <= len(raw) <= MAX_UNCOMPRESSED
            and raw[:8] == b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01'
            and struct.unpack_from('<I', raw, 12)[0] == 2, 'native_qa_arm64_executable_required')
    ncmds, sizeofcmds = struct.unpack_from('<II', raw, 16)
    require(ncmds <= 4096 and 32 + sizeofcmds <= len(raw), 'native_qa_MachO_commands_limit')
    offset, sections = 32, []
    for _ in range(ncmds):
        require(offset + 8 <= 32 + sizeofcmds, 'native_qa_MachO_command_bounds')
        kind, size = struct.unpack_from('<II', raw, offset)
        require(size >= 8 and offset + size <= 32 + sizeofcmds,
                'native_qa_MachO_command_bounds')
        if kind == 0x19:
            require(size >= 72, 'native_qa_MachO_segment_bounds')
            count = struct.unpack_from('<I', raw, offset + 64)[0]
            require(72 + count * 80 <= size, 'native_qa_MachO_section_bounds')
            for index in range(count):
                row = struct.unpack_from('<16s16sQQIIIIIIII', raw, offset + 72 + index * 80)
                name, segment = row[0].rstrip(b'\0'), row[1].rstrip(b'\0')
                length, where = row[3], row[4]
                if segment == b'__TEXT':
                    require(where > 0 and where + length <= len(raw), 'native_qa_MachO_text_bounds')
                    sections.append({'name': name.decode('ascii'), 'offset': where,
                        'bytes': length, 'sha256': _digest(raw[where:where + length])})
        offset += size
    require(any(row['name'] == '__text' for row in sections), 'native_qa_MachO_text_missing')
    return sections


def bounded_run(arguments, *, timeout=20, stdout_limit=16384, stderr_limit=65536):
    """Run only a newly-created codesign reader/signer, with bounded streams."""
    require(arguments[0] == '/usr/bin/codesign', 'native_qa_command_not_admitted')
    buffers, counts = {'stdout': bytearray(), 'stderr': bytearray()}, {'stdout': 0, 'stderr': 0}
    deadline = time.monotonic() + timeout
    with subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'},
            start_new_session=True) as process:
        try:
            with selectors.DefaultSelector() as selector:
                for name in buffers:
                    selector.register(getattr(process, name), selectors.EVENT_READ, name)
                while selector.get_map():
                    _check(deadline)
                    for key, _ in selector.select(.1):
                        block = os.read(key.fileobj.fileno(), 8192)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        name = key.data
                        counts[name] += len(block)
                        limit = stdout_limit if name == 'stdout' else stderr_limit
                        require(counts[name] <= limit,
                                'native_qa_command_output_limit')
                        buffers[name].extend(block)
                code = process.wait(timeout=max(.01, deadline - time.monotonic()))
            return subprocess.CompletedProcess(arguments, code, bytes(buffers['stdout']),
                                               bytes(buffers['stderr']))
        finally:
            if process.poll() is None:
                process.terminate()  # Only our just-created codesign process.
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise ExportError('native_qa_codesign_cleanup_unconfirmed') from None


def _signature(app, expected, run, deadline):
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
    result = run(['/usr/bin/codesign', '-d', '--verbose=2', str(app)],
                 stdout_limit=8192, stderr_limit=8192)
    _check(deadline)
    identifiers = [line for raw in (result.stdout, result.stderr) for line in raw.splitlines()
                   if line.startswith(b'Identifier=')]
    require(identifiers == [('Identifier=' + expected).encode()],
            'native_qa_codesign_identifier_mismatch')


def _write_new(path, payload, *, executable=False):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fchmod(stream.fileno(), 0o755 if executable else 0o644)
        os.fsync(stream.fileno())


class _LimitedOutput:
    def __init__(self, stream, deadline):
        self.stream, self.deadline, self.count = stream, deadline, 0

    def write(self, raw):
        _check(self.deadline)
        require(self.count + len(raw) <= MAX_COMPRESSED, 'native_qa_compressed_limit')
        size = self.stream.write(raw)
        require(size == len(raw), 'native_qa_archive_short_write')
        self.count += size
        return size

    def flush(self):
        return self.stream.flush()


def export_native_qa(app: Path, output: Path, *, source_commit: str, run_id: str,
                     environment, command_runner=bounded_run):
    """Finite CI export; all local invocations fail before touching bundle paths."""
    require(environment.get('GITHUB_ACTIONS') == 'true'
            and environment.get('GITHUB_SHA') == source_commit
            and environment.get('GITHUB_RUN_ID') == str(run_id)
            and re.fullmatch('[a-f0-9]{40}', source_commit)
            and re.fullmatch('[0-9]{1,24}', str(run_id)), 'native_qa_exact_CI_required')
    started, deadline = time.monotonic(), time.monotonic() + 120
    app, output = Path(app), Path(output)
    temporary = Path(environment.get('RUNNER_TEMP', '/'))
    require(temporary.is_absolute() and temporary != Path('/') and '..' not in temporary.parts,
            'native_qa_runner_temp_required')
    _directory(temporary)
    require(app.is_absolute() and app.name == 'Jarvis.app' and '..' not in app.parts
            and output.is_absolute() and '..' not in output.parts
            and output != temporary and output.is_relative_to(temporary)
            and (app == Path('/private/tmp/jarvis-ci-desktop/Jarvis.app')
                 or app.is_relative_to(temporary)), 'native_qa_explicit_paths_required')
    require(not output.exists() and not output.is_symlink(), 'native_qa_output_exists')
    for parent in reversed(output.parents):
        if parent == temporary or parent.is_relative_to(temporary):
            _directory(parent)
    _source_ancestors(app, temporary)
    app_identity = _directory(app)
    source = {name: _file_within(app, name,
        maximum=65536 if name.endswith('.plist') else MAX_UNCOMPRESSED,
        deadline=deadline) for name in COPY_FILES}
    original_info = plistlib.loads(source['Contents/Info.plist'])
    pin = original_info.get('JarvisRuntimeManifestSHA256')
    require(original_info.get('JarvisDistributionChannel') == 'self-contained'
            and isinstance(pin, str) and re.fullmatch('[a-f0-9]{64}', pin)
            and original_info.get('CFBundleExecutable') == 'Jarvis'
            and original_info.get('CFBundlePackageType') == 'APPL'
            and isinstance(original_info.get('CFBundleIdentifier'), str),
            'native_qa_qualified_source_bundle_required')
    qualification_bytes = _file_within(app, 'Contents/Resources/JarvisRuntimeQualification.json',
                                      maximum=MAX_METADATA, deadline=deadline)
    qualification = json.loads(qualification_bytes)
    require(qualification.get('source_commit') == source_commit
            and qualification.get('manifest_sha256') == pin
            and qualification.get('parent_eof_clean_shutdown') is True
            and qualification.get('official_codex_empty_account') is True
            and qualification.get('swift_manifest_admitted') is True,
            'native_qa_source_qualification_mismatch')
    original_text = text_sections(source['Contents/MacOS/Jarvis'])
    helper_pin = _digest(source['Contents/Helpers/JarvisQALauncher'])
    require(sum(map(len, source.values())) <= MAX_UNCOMPRESSED, 'native_qa_uncompressed_limit')

    def run(arguments, *, stdout_limit=16384, stderr_limit=65536):
        _check(deadline)
        result = command_runner(arguments, timeout=min(20, max(.01, deadline - time.monotonic())),
                                stdout_limit=stdout_limit, stderr_limit=stderr_limit)
        require(type(result.stdout) is bytes and type(result.stderr) is bytes
                and len(result.stdout) <= stdout_limit and len(result.stderr) <= stderr_limit,
                'native_qa_command_output_limit')
        require(result.returncode == 0, 'native_qa_codesign_failed')
        return result

    _signature(app, original_info['CFBundleIdentifier'], run, deadline)
    output.mkdir(mode=0o700)
    result_app = output / 'Jarvis.app'
    result_app.mkdir(mode=0o755)
    identity = 'local.jarvis.qa.native.' + source_commit[:12]
    info = dict(original_info)
    for key in ('JarvisRuntimeManifestSHA256', 'JarvisQualificationConfigurationSHA256'):
        info.pop(key, None)
    info.update(JarvisDistributionChannel='development', CFBundleIdentifier=identity,
                CFBundleName='Jarvis QA Nativa', CFBundleDisplayName='Jarvis QA Nativa')
    for name, payload in source.items():
        _write_new(result_app / name, plistlib.dumps(info, sort_keys=True)
                   if name == 'Contents/Info.plist' else payload,
                   executable=name != 'Contents/Info.plist')
    run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none',
         '--identifier', identity, str(result_app)])
    _signature(result_app, identity, run, deadline)
    native = {name: _file_within(result_app, name, maximum=MAX_UNCOMPRESSED,
                               deadline=deadline) for name in ARCHIVE_FILES}
    require(text_sections(native['Contents/MacOS/Jarvis']) == original_text,
            'native_qa_TEXT_changed')
    require(_digest(native['Contents/Helpers/JarvisQALauncher']) == helper_pin,
            'native_qa_helper_changed')
    require(sum(map(len, native.values())) <= MAX_UNCOMPRESSED, 'native_qa_uncompressed_limit')
    require(not (result_app / 'Contents/Resources').exists(), 'native_qa_resources_not_allowed')
    observed = set()
    for folder, directories, files in os.walk(result_app, followlinks=False):
        _directory(Path(folder))
        for name in directories:
            _directory(Path(folder) / name)
        for name in files:
            observed.add((Path(folder) / name).relative_to(result_app).as_posix())
    require(observed == set(ARCHIVE_FILES), 'native_qa_unexpected_output_file')
    _source_ancestors(app, temporary)
    require(_directory(app) == app_identity and all(
        _file_within(app, name, maximum=MAX_UNCOMPRESSED, deadline=deadline) == payload
        for name, payload in source.items()) and _file_within(app,
            'Contents/Resources/JarvisRuntimeQualification.json', maximum=MAX_METADATA,
            deadline=deadline) == qualification_bytes, 'native_qa_source_changed')
    archive = output / ARCHIVE_NAME
    fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        limited = _LimitedOutput(stream, deadline)
        with tarfile.open(fileobj=limited, mode='w|gz') as tar:
            for name in sorted(native):
                row = tarfile.TarInfo('Jarvis.app/' + name)
                row.size = len(native[name])
                row.mode = 0o755 if name in COPY_FILES[:2] else 0o644
                tar.addfile(row, io.BytesIO(native[name]))
        stream.flush()
        os.fsync(stream.fileno())
    archive_bytes = _regular(archive, maximum=MAX_COMPRESSED, deadline=deadline)
    provenance = {'format': 'jarvis-native-qa-ci/1', 'source_commit': source_commit,
        'run_id': str(run_id), 'distribution_channel': 'development', 'self_contained': False,
        'external_python_required': True, 'external_source_required': True,
        'external_dependencies_required': True, 'external_codex_required': True,
        'signing': 'ad-hoc-ci', 'notarized': False, 'binary_executed_by_export': False,
        'source_bundle_runtime_qualified': True, 'full_workflow_success_claimed': False,
        'bundle_identifier': identity, 'archive_bytes': len(archive_bytes),
        'archive_sha256': _digest(archive_bytes),
        'uncompressed_bytes': sum(map(len, native.values())),
        'binary_sha256': _digest(native['Contents/MacOS/Jarvis']),
        'qa_launcher_sha256': helper_pin,
        'info_plist_sha256': _digest(native['Contents/Info.plist']),
        'code_resources_sha256': _digest(native[SEAL]), 'code_text_unchanged': True,
        'helper_unchanged': True, 'code_text_sections': original_text,
        'source_binary_sha256': _digest(source['Contents/MacOS/Jarvis']),
        'files': [{'path': name, 'bytes': len(native[name]), 'sha256': _digest(native[name])}
                  for name in sorted(native)],
        'export_script_sha256': _digest(Path(__file__).read_bytes()),
        'compressed_limit_bytes': MAX_COMPRESSED, 'uncompressed_limit_bytes': MAX_UNCOMPRESSED,
        'elapsed_seconds': round(time.monotonic() - started, 3)}
    _write_new(output / PROVENANCE_NAME,
               json.dumps(provenance, sort_keys=True, separators=(',', ':')).encode() + b'\n')
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    try:
        require(sys.platform == 'darwin', 'native_qa_export_macOS_CI_only')
        result = export_native_qa(args.app, args.output, source_commit=args.source_commit,
                                  run_id=args.run_id, environment=os.environ)
    except (ExportError, OSError, ValueError, KeyError, struct.error) as error:
        code = str(error) if isinstance(error, ExportError) else type(error).__name__
        parser.exit(2, f'La exportación nativa de QA falló ({code}); '
                    'no es una distribución.\n')
    print(json.dumps({'status': 'native_qa_development_exported',
                      'archive_bytes': result['archive_bytes'],
                      'archive_sha256': result['archive_sha256'], 'self_contained': False}))


if __name__ == '__main__':
    main()
