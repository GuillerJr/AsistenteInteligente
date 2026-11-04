"""Support for one opt-in synthetic native QA. Importing starts no process."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import selectors
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).parent
REPO = Path('/Users/guillerjr/Documents/Proyectos/AsistenteInteligente')
MIB, GIB = 1024**2, 1024**3
CAPACITY, FLOOR = 2048 * MIB, 25 * GIB
_OBSERVATION = {'samples': 0, 'peak_used_bytes': 0, 'peak_provider_growth_bytes': 0,
                'peak_unaccounted_Data_drop_bytes': 0}
_LIB = None
_PROVIDER_CACHE = {}
_OWNED_CHILDREN = {}
_PROCESS_METADATA = {'read_retry_maximum_samples': 10, 'read_retry_maximum_seconds': 2,
    'unconfirmed_samples': 0, 'recovered_lookups': 0, 'absent_after_pending': 0,
    'owned_child_reaped_observations': 0, 'audit_event_limit': 32,
    'dropped_audit_events': 0, 'events': []}


class GuardIssue(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise GuardIssue(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(512 * 1024):
            digest.update(block)
    return digest.hexdigest()


def private(path, kind='directory'):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path)
            and str(path).startswith('/private/tmp/'), 'private_path_required')
    current = Path('/private/tmp')
    for part in path.parts[3:]:
        current /= part
        value = current.lstat()
        require(not stat.S_ISLNK(value.st_mode) and value.st_uid == os.getuid()
                and not value.st_mode & 0o022, 'private_path_custody')
    require(stat.S_ISDIR(value.st_mode) if kind == 'directory' else
            stat.S_ISREG(value.st_mode) and value.st_nlink == 1, 'private_path_kind')
    return value


def read_json(path, pin=None, maximum=65536):
    path = Path(path)
    private(path, 'file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(0 < before.st_size <= maximum, 'JSON_limit')
        raw = os.read(fd, maximum + 1)
        after, named = os.fstat(fd), path.lstat()
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k)
                == getattr(named, k) for k in fields), 'JSON_changed')
        require(pin is None or hashlib.sha256(raw).hexdigest() == pin, 'JSON_pin_changed')
        return json.loads(raw)
    finally:
        os.close(fd)


def write_new(path, raw, *, maximum=65536):
    path = Path(path)
    private(path.parent)
    require(type(maximum) is int and 0 < maximum <= 4 * MIB
            and type(raw) is bytes and len(raw) <= maximum, 'receipt_or_manifest_limit')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    return write_new(path, canonical(value) + b'\n')


def allocation(path):
    """Metadata only in explicitly chosen synthetic provider; never file bytes."""
    path = Path(path)
    private(path)
    total, count, pending = 0, 0, [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                require(count <= 10000, 'provider_metadata_entry_limit')
                info = entry.stat(follow_symlinks=False)
                require(info.st_uid == os.getuid(), 'provider_metadata_owner_changed')
                total += info.st_blocks * 512
                if stat.S_ISDIR(info.st_mode):
                    require(not info.st_mode & 0o022, 'provider_directory_not_private')
                    pending.append(Path(entry.path))
    return total


def provider_observation(prepared, *, fresh=False):
    """At most one-second metadata cache, only in the admitted synthetic quota-owned provider.

    The physical APFS quota bounds the provider even between samples. This is a
    sampled 128MiB sub-budget, not a separate physical provider sandbox.
    """
    root, path = Path(prepared['root_quota']), Path(prepared['provider'])
    require(path == root / 'provider' and prepared['provider_baseline'] == 106471424,
            'admitted_synthetic_provider_required')
    key = (os.getpid(), str(path))
    now = time.monotonic()
    cached = _PROVIDER_CACHE.get(key)
    if fresh or cached is None or now - cached['sample_started_monotonic'] >= 1:
        started = time.monotonic()
        value = allocation(path)
        cached = {'allocated_bytes': value, 'sample_started_monotonic': started,
                  'sample_completed_monotonic': time.monotonic(), 'unix': time.time()}
        _PROVIDER_CACHE[key] = cached
    age = max(0, time.monotonic() - cached['sample_started_monotonic'])
    require(cached['allocated_bytes'] <= 128 * MIB, 'quota_owned_provider_sampled_limit')
    return {**cached, 'sample_age_seconds': age,
            'sampling_cache_maximum_seconds': 1, 'fresh_requested': fresh,
            'physically_in_aggregate_quota': True}


def resources(prepared, *, fresh_provider=False):
    root = Path(prepared['root_quota'])
    value = root.lstat()
    volume = prepared['volume']
    require(os.path.ismount(root) and not stat.S_ISLNK(value.st_mode)
            and (value.st_dev, value.st_ino) == (volume['device'], volume['inode']),
            'quota_identity_changed')
    fs = os.statvfs(root)
    capacity, used = fs.f_blocks * fs.f_frsize, (fs.f_blocks - fs.f_bfree) * fs.f_frsize
    require(capacity <= CAPACITY and 0 <= used <= CAPACITY, 'quota_capacity_exceeded')
    image = Path(volume['image'])
    image_info = private(image, 'file')
    require((image_info.st_dev, image_info.st_ino) == tuple(volume['image_identity'])
            and image_info.st_blocks * 512 <= CAPACITY, 'quota_image_identity_or_capacity')
    data_free = os.statvfs('/System/Volumes/Data')
    free = data_free.f_bavail * data_free.f_frsize
    require(free >= FLOOR, 'Data_free_floor_reached')
    provider_sample = provider_observation(prepared, fresh=fresh_provider)
    provider_used = provider_sample['allocated_bytes']
    growth = max(0, provider_used - prepared['provider_baseline'])
    anchor = prepared.get('resource_anchor')
    # Provider writes already belong to the same APFS used-byte delta; never subtract twice.
    residual = 0 if anchor is None else max(0, anchor['data_available_bytes'] - free
        - max(0, used - anchor['quota_used_bytes']))
    require(residual <= GIB, 'unaccounted_Data_drop_limit')
    current = {'data_available_bytes': free, 'quota_used_bytes': used,
               'quota_capacity_bytes': capacity, 'provider_allocated_bytes': provider_used,
               'provider_growth_bytes': growth, 'unaccounted_Data_drop_bytes': residual,
               'physical_image_bytes': image_info.st_blocks * 512, 'unix': time.time(),
               'provider_sampling': provider_sample, 'provider_scope': 'previous_synthetic_provider_inside_same_APFS_quota'}
    _OBSERVATION['samples'] += 1
    for key, measured in [('peak_used_bytes', used), ('peak_provider_growth_bytes', growth),
                          ('peak_unaccounted_Data_drop_bytes', residual)]:
        _OBSERVATION[key] = max(_OBSERVATION[key], measured)
    _OBSERVATION['last'] = current
    return current


def resource_observation():
    return {**_OBSERVATION, 'process_metadata': dict(_PROCESS_METADATA)}


def capture(argv, *, deadline, checkpoint=lambda: None, output=None, maximum=1048576,
            stderr_max=65536, environment=None, include_stderr=False, on_spawn=None):
    """Bound every new child; reserve exclusive output before spawning it."""
    checkpoint()
    require(time.monotonic() < deadline, 'command_deadline')
    process, destination, failure = None, None, None
    buffers, counts = {'stdout': bytearray(), 'stderr': bytearray()}, {'stdout': 0, 'stderr': 0}
    try:
        if output is not None:
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                destination = os.fdopen(fd, 'wb')
            except BaseException:
                os.close(fd)
                raise
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=REPO, env=environment, start_new_session=True)
        _OWNED_CHILDREN[process.pid] = process
        process.poll()  # Reap only this exact Popen child before any observer checkpoint.
        if on_spawn is not None:
            on_spawn(process.pid)
        with selectors.DefaultSelector() as selector:
            for label in buffers:
                selector.register(getattr(process, label), selectors.EVENT_READ, label)
            while selector.get_map():
                process.poll()  # A finished helper must not remain waitable during a scan.
                checkpoint()
                require(time.monotonic() < deadline, 'command_deadline')
                for key, _ in selector.select(.1):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj); continue
                    label = key.data; counts[label] += len(block)
                    require(counts[label] <= (maximum if label == 'stdout' else stderr_max),
                            'command_output_limit')
                    if label == 'stdout' and destination is not None:
                        destination.write(block)
                    else:
                        buffers[label].extend(block)
        require(process.wait(timeout=max(.01, min(2, deadline - time.monotonic()))) == 0,
                'command_failed')
        if destination is not None:
            destination.flush(); os.fsync(destination.fileno())
        return (bytes(buffers['stdout']) + b'\n' + bytes(buffers['stderr'])
                if include_stderr else bytes(buffers['stdout'])), counts['stdout']
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup_failure = None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()  # Only our exact just-created helper/metadata child.
                    process.wait(timeout=2)
            except BaseException:
                cleanup_failure = GuardIssue('owned_command_cleanup_unconfirmed')
            _OWNED_CHILDREN.pop(process.pid, None)
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except BaseException:
                    if cleanup_failure is None:
                        cleanup_failure = GuardIssue('owned_command_stream_close_failed')
        if destination is not None:
            try:
                destination.close()
            except BaseException:
                if cleanup_failure is None:
                    cleanup_failure = GuardIssue('owned_output_close_failed_child_reaped')
        if cleanup_failure is not None:
            raise cleanup_failure from failure


class BSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid',
        'svuid', 'svgid', 'rfu')] + [('comm', ctypes.c_char * 16),
        ('name', ctypes.c_char * 32)] + [(name, ctypes.c_uint32) for name in
        ('nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')] + [('nice', ctypes.c_int32),
        ('start_seconds', ctypes.c_uint64), ('start_microseconds', ctypes.c_uint64)]


def library():
    global _LIB
    if _LIB is None:
        require(ctypes.sizeof(BSDInfo) == 136, 'BSDInfo_layout_unconfirmed')
        _LIB = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        _LIB.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_int]
        _LIB.proc_pidinfo.restype = ctypes.c_int
        _LIB.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        _LIB.proc_pidpath.restype = ctypes.c_int
        _LIB.proc_listchildpids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        _LIB.proc_listchildpids.restype = ctypes.c_int
    return _LIB


def _metadata_audit(value):
    if len(_PROCESS_METADATA['events']) < _PROCESS_METADATA['audit_event_limit']:
        _PROCESS_METADATA['events'].append(value)
    else:
        _PROCESS_METADATA['dropped_audit_events'] += 1


class _MetadataPending(GuardIssue):
    def __init__(self, code, evidence):
        super().__init__(code)
        self.evidence = evidence


def _own_child_reaped(pid, stage):
    owned = _OWNED_CHILDREN.get(pid)
    if owned is None:
        return False
    code = owned.poll()  # waitpid authority belongs only to this exact new Popen object.
    if code is None:
        return False
    _PROCESS_METADATA['owned_child_reaped_observations'] += 1
    _metadata_audit({'pid': pid, 'stage': stage, 'own_child_returncode': code,
                     'unix': time.time()})
    return True


def _snapshot_once(pid):
    if _own_child_reaped(pid, 'owned_Popen_reaped_before_metadata'):
        return None
    lib, info = library(), BSDInfo()
    ctypes.set_errno(0)
    returned = lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    saved_errno = ctypes.get_errno()
    if returned != ctypes.sizeof(info):
        evidence = {'pid': pid, 'stage': 'BSDInfo', 'returned_bytes': returned,
                    'errno': saved_errno, 'unix': time.time()}
        if _own_child_reaped(pid, 'owned_Popen_reaped_after_BSD_unavailable'):
            return None
        try:
            os.kill(pid, 0)  # Existence observation only, never an effect signal.
        except ProcessLookupError:
            return None
        raise _MetadataPending('selected_process_metadata_unconfirmed', evidence)
    if info.status == 5:  # SZOMB cannot retain target-owned resources.
        return None
    require(info.pid == pid and info.uid == os.getuid(), 'selected_process_owner_changed')
    buffer = ctypes.create_string_buffer(4096)
    ctypes.set_errno(0)
    returned = lib.proc_pidpath(pid, buffer, len(buffer))
    saved_errno = ctypes.get_errno()
    if returned <= 0:
        evidence = {'pid': pid, 'stage': 'executable_path', 'returned_bytes': returned,
                    'errno': saved_errno, 'unix': time.time()}
        if _own_child_reaped(pid, 'owned_Popen_reaped_after_path_unavailable'):
            return None
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        raise _MetadataPending('selected_executable_unconfirmed', evidence)
    return {'pid': pid, 'parent_pid': info.ppid, 'uid': info.uid,
        'start_seconds': info.start_seconds, 'start_microseconds': info.start_microseconds,
        'executable_path': os.fsdecode(buffer.value), 'status': info.status}


def snapshot_pid(pid):
    """A finite read-only reconciliation, never a launch or effect retry.

    An observed identity mismatch is still handled by the unchanged caller pins.
    Only unavailable BSD/path samples are retried; own-child poll can resolve a
    waitable helper exit while capture is draining its streams.
    """
    require(type(pid) is int and pid > 1, 'invalid_selected_PID')
    deadline, pending = time.monotonic() + 2, False
    for attempt in range(1, 11):
        try:
            result = _snapshot_once(pid)
            if pending:
                key = 'recovered_lookups' if result is not None else 'absent_after_pending'
                _PROCESS_METADATA[key] += 1
                _metadata_audit({'pid': pid, 'stage': key, 'attempt': attempt,
                                 'unix': time.time()})
            return result
        except _MetadataPending as error:
            pending = True
            _PROCESS_METADATA['unconfirmed_samples'] += 1
            _metadata_audit({**error.evidence, 'attempt': attempt})
            remaining = deadline - time.monotonic()
            if attempt >= 10 or remaining <= 0:
                raise GuardIssue('selected_process_metadata_unconfirmed_after_finite_reads') from None
            time.sleep(min(.05, remaining))
    raise GuardIssue('selected_process_metadata_unconfirmed_after_finite_reads')


def identity(row):
    return (row['pid'], row['start_seconds'], row['start_microseconds'],
            row['executable_path'], row['uid'])


def children(parent):
    """Direct children of one already-owned parent, never global PID inventory."""
    current = snapshot_pid(parent['pid'])
    if current is None or identity(current) != identity(parent):
        return []
    data = (ctypes.c_int32 * 129)()
    # proc_listchildpids returns a PID count, unlike proc_listpids byte count.
    # Apple xnu/libsyscall/wrappers/libproc/libproc.c proc_listchildpids divides by sizeof(int).
    ctypes.set_errno(0)
    count = library().proc_listchildpids(parent['pid'], data, ctypes.sizeof(data))
    error = ctypes.get_errno()
    if count == 0 and error == errno.ESRCH and not exact_alive(parent):
        return []
    require(0 <= count < len(data) and not (count == 0 and error),
            'owned_children_limit_or_metadata_error')
    rows = []
    for pid in data[:count]:
        row = snapshot_pid(int(pid))
        if row is not None:
            require(row['parent_pid'] == parent['pid'], 'owned_child_parent_changed')
            rows.append(row)
    return rows


def exact_alive(row):
    current = snapshot_pid(row['pid'])
    return current is not None and identity(current) == identity(row)


def text_sections(binary):
    """Hash section bytes, not load-command/code-signature ranges; no execution."""
    import struct
    raw = Path(binary).read_bytes()
    require(32 <= len(raw) <= 128 * MIB and raw[:8] == b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01',
            'arm64_thin_native_required')
    ncmds, sizeofcmds = struct.unpack_from('<II', raw, 16)
    require(ncmds <= 4096 and 32 + sizeofcmds <= len(raw), 'MachO_commands_limit')
    offset, sections = 32, []
    for _ in range(ncmds):
        cmd, length = struct.unpack_from('<II', raw, offset)
        require(length >= 8 and offset + length <= 32 + sizeofcmds, 'MachO_command_bounds')
        if cmd == 0x19:
            require(length >= 72, 'MachO_segment_bounds')
            count = struct.unpack_from('<I', raw, offset + 64)[0]
            require(72 + count * 80 <= length, 'MachO_sections_bounds')
            for index in range(count):
                row = struct.unpack_from('<16s16sQQIIIIIIII', raw, offset + 72 + index * 80)
                name, segment = row[0].rstrip(b'\0'), row[1].rstrip(b'\0')
                size, where = row[3], row[4]
                if segment == b'__TEXT':
                    require(where > 0 and where + size <= len(raw), 'MachO_text_bounds')
                    sections.append({'section': name.decode('ascii'), 'offset': where,
                        'size': size, 'sha256': hashlib.sha256(raw[where:where + size]).hexdigest()})
        offset += length
    require(any(row['section'] == '__text' for row in sections), 'MachO_text_missing')
    return sections


def _copy_file_observation(path):
    value = private(path, 'file')
    return {'device': value.st_dev, 'inode': value.st_ino, 'size': value.st_size,
            'mtime_ns': value.st_mtime_ns, 'ctime_ns': value.st_ctime_ns,
            'mode': stat.S_IMODE(value.st_mode)}


def verify_copies(prepared, deadline=None, *, metadata_only=False):
    """Validate only the admitted quota's immutable source/dependency/program copies.

    Full entry checks hash all copied public bytes. The immediate effect check
    can use the same physical inode/size/mtime/ctime pins without hashing 400MiB
    a second time. No malicious same-UID race-proof sandbox is claimed.
    """
    deadline = time.monotonic() + 20 if deadline is None else deadline

    def check():
        require(time.monotonic() < deadline, 'copy_verification_deadline')

    root = Path(prepared['root_quota'])
    require(prepared['source'] == str(root / 'source')
        and prepared['dependencies'] == str(root / 'dependencies')
        and prepared['codex'] == str(root / 'bin/codex')
        and prepared['copy_manifest_path'] == str(ROOT / 'copies-manifest.json'),
        'explicit_copy_roots_required')
    manifest = read_json(ROOT / 'copies-manifest.json', prepared['copies_manifest_sha256'],
                         maximum=4 * MIB)
    require(manifest['schema'] == 'jarvis.native-QA-copy-manifest/1'
        and manifest['source_commit'] == prepared['commit'], 'copy_manifest_scope_changed')
    checked_files, checked_bytes = 0, 0
    for label in ('source', 'dependencies'):
        tree = manifest[label]
        base = Path(tree['root'])
        require(str(base) == prepared[label] and len(tree['files']) <= 10000
                and len(tree['directories']) <= 10000, 'copy_tree_limit')
        expected_files = {row['path']: row for row in tree['files']}
        expected_dirs = {row['path']: row for row in tree['directories']}
        require(len(expected_files) == len(tree['files'])
            and len(expected_dirs) == len(tree['directories']), 'copy_tree_duplicates')
        seen_files, seen_dirs, pending = set(), set(), [base]
        while pending:
            check(); directory = pending.pop()
            relative = directory.relative_to(base).as_posix()
            relative = '' if relative == '.' else relative
            value = private(directory)
            row = expected_dirs.get(relative)
            require(row is not None and (value.st_dev, value.st_ino) ==
                (row['device'], row['inode']) and stat.S_IMODE(value.st_mode) == row['mode'],
                'copy_directory_identity_changed')
            seen_dirs.add(relative)
            with os.scandir(directory) as entries:
                for entry in entries:
                    check()
                    item = Path(entry.path); name = item.relative_to(base).as_posix()
                    value = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(value.st_mode):
                        require(name in expected_dirs, 'unexpected_copy_directory')
                        pending.append(item)
                    else:
                        require(name in expected_files, 'unexpected_copy_file')
                        expected = expected_files[name]
                        current = _copy_file_observation(item)
                        require(all(current[key] == expected[key] for key in current),
                                'copy_file_identity_changed')
                        if not metadata_only:
                            require(sha(item) == expected['sha256'], 'copy_file_digest_changed')
                        seen_files.add(name); checked_files += 1; checked_bytes += current['size']
        require(seen_files == set(expected_files) and seen_dirs == set(expected_dirs),
                'copy_tree_incomplete')
    codex = manifest['codex']
    require(codex['path'] == prepared['codex'], 'copied_Codex_path_changed')
    current = _copy_file_observation(Path(codex['path']))
    require(all(current[key] == codex[key] for key in current), 'copied_Codex_identity_changed')
    if not metadata_only:
        check(); require(sha(codex['path']) == codex['sha256'], 'copied_Codex_digest_changed')
    for label, path_key in (('python', 'python'), ('python_process', 'python_process_executable')):
        check(); row = manifest[label]
        path = Path(row['path'])
        require(row['path'] == prepared[path_key] and path.is_file()
            and os.access(path, os.X_OK) and sha(path) == row['sha256'],
            'selected_external_Python_changed')
    check()
    return {'copied_public_files_verified': checked_files + 1,
            'copied_public_bytes_verified': checked_bytes + codex['size'],
            'metadata_only': metadata_only, 'external_Python_pin_verified': True,
            'same_UID_sandbox_claimed': False, 'owner_credentials_read': False}
