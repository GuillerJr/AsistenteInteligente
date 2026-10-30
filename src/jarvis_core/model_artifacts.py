"""Pinned public MiniLM assets. Acquisition is explicit; inference never downloads."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
REPOSITORY = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_RESOLVE = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/"
MAX_DOWNLOAD_BYTES = 256_000_000
MODEL_SPEC = {
    "format": "jarvis-local-embedding-model/1", "repository": REPOSITORY,
    "revision": REVISION, "license": "Apache-2.0", "dimension": 384,
    "max_tokens": 128, "overlap_tokens": 16,
    "pooling": "attention-masked-mean-including-special-tokens",
    "normalization": "float32-l2", "query_prefix": "", "document_prefix": "",
    "runtime": {"onnxruntime": "1.31.1", "numpy": "2.3.5", "tokenizers": "0.22.1",
                "provider": "CPUExecutionProvider", "threads": 1, "execution": "sequential"},
    "files": [
        {"path": "onnx/model_qint8_arm64.onnx", "bytes": 118_412_398,
         "sha256": "783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672",
         "url": _RESOLVE + "onnx/model_qint8_arm64.onnx"},
        {"path": "tokenizer.json", "bytes": 9_081_518,
         "sha256": "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8",
         "url": _RESOLVE + "tokenizer.json"},
        {"path": "README.md", "bytes": 3888,
         "sha256": "1e98ea05b0de579fcaad3d625b62ea55647142ed674d5f5ebf1440e4bbbb6f23",
         "url": _RESOLVE + "README.md"},
        {"path": "LICENSE.Apache-2.0.txt", "bytes": 11358,
         "sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
         "url": "https://www.apache.org/licenses/LICENSE-2.0.txt"},
    ],
}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


MODEL_FINGERPRINT = hashlib.sha256(_canonical(MODEL_SPEC)).hexdigest()
_MANIFEST = _canonical({"model_fingerprint": MODEL_FINGERPRINT, "spec": MODEL_SPEC}) + b"\n"
_HOSTS = {"huggingface.co", "us.aws.cdn.hf.co", "cdn-lfs.huggingface.co",
          "cdn-lfs-us-1.hf.co", "cas-bridge.xethub.hf.co", "www.apache.org", "apache.org"}


class ArtifactError(Exception):
    def __init__(self, code, *, receipt=None):
        self.code = code
        self.receipt = receipt
        super().__init__(code)


def _current(assert_current):
    if assert_current is not None:
        result = assert_current()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ArtifactError("invalid_guard")


def _checkpoint(cancellation, deadline):
    if cancellation is not None and cancellation.is_set():
        raise ArtifactError("cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise ArtifactError("timeout")


def _physical_directory(path: Path, *, private=True):
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ArtifactError("invalid_model")
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ArtifactError("invalid_model")
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise ArtifactError("invalid_model")


def _read_file(path, expected, cancellation, deadline, *, capture=False):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != expected["bytes"]):
        raise ArtifactError("invalid_model")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest, count = hashlib.sha256(), 0
    payload = bytearray() if capture else None
    try:
        pinned = os.fstat(descriptor)
        if (pinned.st_dev, pinned.st_ino) != (before.st_dev, before.st_ino):
            raise ArtifactError("invalid_model")
        while True:
            _checkpoint(cancellation, deadline)
            block = os.read(descriptor, 512 * 1024)
            if not block:
                break
            count += len(block)
            if count > expected["bytes"]:
                raise ArtifactError("invalid_model")
            digest.update(block)
            if payload is not None:
                payload.extend(block)
        after = os.fstat(descriptor)
        final = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
        if (any(getattr(before, k) != getattr(after, k) for k in fields)
                or any(getattr(before, k) != getattr(final, k) for k in fields)
                or count != expected["bytes"] or digest.hexdigest() != expected["sha256"]):
            raise ArtifactError("invalid_model")
    finally:
        os.close(descriptor)
    return bytes(payload) if payload is not None else None


def read_model_asset(directory, name, *, cancellation=None, deadline=None):
    """Return the very bytes verified before handing them to a native decoder."""
    row = next((row for row in MODEL_SPEC["files"] if row["path"] == name), None)
    if row is None:
        raise ArtifactError("invalid_model")
    root = Path(directory)
    try:
        _physical_directory(root)
        if name.startswith("onnx/"):
            _physical_directory(root / "onnx")
        return _read_file(root / name, row, cancellation, deadline, capture=True)
    except FileNotFoundError:
        raise ArtifactError("missing_model") from None
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ArtifactError("invalid_model") from None


def inspect_model(directory, *, cancellation=None, deadline=None):
    """Verify exactly the approved files without discovering any owner directories."""
    root = Path(directory)
    try:
        _physical_directory(root)
        _physical_directory(root / "onnx")
        identities = {path: path.lstat() for path in (root, root / "onnx")}
        expected = {row["path"] for row in MODEL_SPEC["files"]} | {"manifest.json"}
        observed = set()
        for parent, directories, files in os.walk(root, followlinks=False):
            for name in directories:
                child = Path(parent) / name
                if child.relative_to(root).as_posix() != "onnx":
                    raise ArtifactError("invalid_model")
                _physical_directory(child)
            for name in files:
                observed.add((Path(parent) / name).relative_to(root).as_posix())
        if observed != expected:
            raise ArtifactError("invalid_model")
        _read_file(root / "manifest.json",
                   {"bytes": len(_MANIFEST), "sha256": hashlib.sha256(_MANIFEST).hexdigest()},
                   cancellation, deadline)
        for row in MODEL_SPEC["files"]:
            _read_file(root / row["path"], row, cancellation, deadline)
        _checkpoint(cancellation, deadline)
        for path, before in identities.items():
            after = path.lstat()
            if any(getattr(before, key) != getattr(after, key) for key in (
                "st_dev", "st_ino", "st_mode", "st_uid", "st_mtime_ns", "st_ctime_ns",
            )):
                raise ArtifactError("invalid_model")
        return {"ready": True, "model_fingerprint": MODEL_FINGERPRINT,
                "root_identity": [identities[root].st_dev, identities[root].st_ino],
                "dimension": 384, "max_tokens": 128, "license": "Apache-2.0",
                "revision": REVISION, "bytes": sum(row["bytes"] for row in MODEL_SPEC["files"]),
                "source": "official_pinned_public_artifacts", "offline": True}
    except FileNotFoundError:
        raise ArtifactError("missing_model") from None
    except (OSError, ValueError, TypeError, RuntimeError):
        raise ArtifactError("invalid_model") from None


def _public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in _HOSTS
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in {None, 443}):
        raise ArtifactError("download_rejected")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, request, response, code, message, headers, newurl):
        _public_url(newurl)
        result = super().redirect_request(request, response, code, message, headers, newurl)
        if result is None:
            raise ArtifactError("download_rejected")
        return result


def _publish_new(source, destination):
    """An exclusive atomic directory rename, never replacement after a check."""
    library = ctypes.CDLL(None, use_errno=True)
    name, flag = ("renameatx_np", 4) if sys.platform == "darwin" else ("renameat2", 1)
    function = getattr(library, name, None)
    if function is None:
        raise ArtifactError("unsupported_storage")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                         ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100 if sys.platform != "darwin" else -2, os.fsencode(source),
                -100 if sys.platform != "darwin" else -2, os.fsencode(destination), flag):
        code = ctypes.get_errno()
        if code in {errno.EEXIST, errno.ENOTEMPTY}:
            raise ArtifactError("invalid_model")
        raise OSError(code, "exclusive model publication failed")


def _copy_response(response, output, row, cancellation, deadline):
    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
        raise ArtifactError("download_rejected")
    length = response.headers.get("Content-Length")
    if length is not None and length != str(row["bytes"]):
        raise ArtifactError("download_rejected")
    digest, count = hashlib.sha256(), 0
    while True:
        _checkpoint(cancellation, deadline)
        block = response.read(min(512 * 1024, row["bytes"] + 1 - count))
        if not block:
            break
        count += len(block)
        if count > row["bytes"] or count > MAX_DOWNLOAD_BYTES:
            raise ArtifactError("capacity")
        digest.update(block)
        output.write(block)
    if count != row["bytes"] or digest.hexdigest() != row["sha256"]:
        raise ArtifactError("invalid_model")
    output.flush()
    os.fsync(output.fileno())


_BUNDLED_PATHS = frozenset({
    "onnx/model_qint8_arm64.onnx", "tokenizer.json", "README.md", "LICENSE.Apache-2.0.txt",
})


class _BundledModelSource:
    """Fixed, read-only public bundle files; never weaken private cache checks."""

    def __init__(self, directory, cancellation, deadline, assert_current):
        self.root = Path(directory)
        self.cancellation, self.deadline = cancellation, deadline
        self.guard = assert_current
        self.directories, self.files = [], {}
        try:
            _checkpoint(cancellation, deadline)
            _current(assert_current)
            if not self.root.is_absolute() or self.root.resolve(strict=True) != self.root:
                raise ArtifactError("bundled_model_invalid")
            root_fd = self._directory(self.root)
            onnx_fd = self._directory(self.root / "onnx", parent=root_fd, name="onnx")
            rows = list(MODEL_SPEC["files"])
            if (len({row["path"] for row in rows}) != len(rows)
                    or any(row["path"] not in _BUNDLED_PATHS for row in rows)):
                raise ArtifactError("bundled_model_invalid")
            rows.append({"path": "manifest.json", "bytes": len(_MANIFEST),
                         "sha256": hashlib.sha256(_MANIFEST).hexdigest()})
            for row in rows:
                relative = row["path"]
                if relative not in _BUNDLED_PATHS | {"manifest.json"}:
                    raise ArtifactError("bundled_model_invalid")
                parent = onnx_fd if relative.startswith("onnx/") else root_fd
                name = relative.rsplit("/", 1)[-1]
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_uid not in {0, os.getuid()}
                        or stat.S_IMODE(before.st_mode) & 0o022
                        or before.st_size != row["bytes"]):
                    raise ArtifactError("bundled_model_invalid")
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                self.files[relative] = (fd, before, parent, name)
                if not _same_file(before, os.fstat(fd)):
                    raise ArtifactError("bundled_model_invalid")
            self.assert_current()
            manifest_fd = self.files["manifest.json"][0]
            if os.pread(manifest_fd, len(_MANIFEST) + 1, 0) != _MANIFEST:
                raise ArtifactError("bundled_model_invalid")
            self.assert_current()
        except OSError:
            self.close()
            raise ArtifactError("bundled_model_invalid") from None
        except BaseException:
            self.close()
            raise

    def _directory(self, path, *, parent=None, name=None):
        before = path.lstat() if parent is None else os.stat(
            name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISDIR(before.st_mode) or before.st_uid not in {0, os.getuid()}
                or stat.S_IMODE(before.st_mode) & 0o022):
            raise ArtifactError("bundled_model_invalid")
        fd = os.open(path if parent is None else name,
                     os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        self.directories.append((fd, before, path))
        if not _same_file(before, os.fstat(fd)):
            raise ArtifactError("bundled_model_invalid")
        return fd

    def assert_current(self):
        _checkpoint(self.cancellation, self.deadline)
        _current(self.guard)
        try:
            if self.root.resolve(strict=True) != self.root:
                raise ArtifactError("bundled_model_invalid")
            for fd, before, path in self.directories:
                if (not _same_file(before, os.fstat(fd))
                        or not _same_file(before, path.lstat())):
                    raise ArtifactError("bundled_model_invalid")
            for fd, before, parent, name in self.files.values():
                if (not _same_file(before, os.fstat(fd))
                        or not _same_file(before, os.stat(name, dir_fd=parent,
                                                       follow_symlinks=False))):
                    raise ArtifactError("bundled_model_invalid")
        except OSError:
            raise ArtifactError("bundled_model_invalid") from None

    def copy(self, row, output):
        source = self
        descriptor = self.files[row["path"]][0]

        class Response:
            def __init__(self):
                self.offset = 0
                self.headers = {"Content-Length": str(row["bytes"])}

            def read(self, count):
                source.assert_current()
                try:
                    value = os.pread(descriptor, count, self.offset)
                except OSError:
                    raise ArtifactError("bundled_model_invalid") from None
                self.offset += len(value)
                return value

        try:
            _copy_response(Response(), output, row, self.cancellation, self.deadline)
            self.assert_current()
        except ArtifactError as error:
            if error.code == "invalid_model":
                raise ArtifactError("bundled_model_invalid") from None
            raise

    def close(self):
        for fd, before, *_ in [*self.files.values(), *self.directories]:
            try:
                current = os.fstat(fd)
                if (before.st_dev, before.st_ino) == (current.st_dev, current.st_ino):
                    os.close(fd)
            except OSError:
                pass
        self.files.clear()
        self.directories.clear()


def prepare_model(directory, *, cancellation=None, deadline=None, assert_current=None,
                  bundled_source=None):
    """Publish a private copy from fixed public assets or the explicit offline bundle.

    Supplying ``bundled_source`` permanently selects that source for this call:
    missing or changed bytes fail closed, without constructing a network opener.
    """
    destination = Path(directory)
    if deadline is None:
        deadline = time.monotonic() + 300
    _checkpoint(cancellation, deadline)
    _current(assert_current)
    if destination.exists() or destination.is_symlink():
        result = inspect_model(destination, cancellation=cancellation, deadline=deadline)
        _current(assert_current)
        return result
    _physical_directory(destination.parent, private=False)
    total = sum(row["bytes"] for row in MODEL_SPEC["files"]) + len(_MANIFEST)
    if total > MAX_DOWNLOAD_BYTES or shutil.disk_usage(destination.parent).free < total + 1024**3:
        raise ArtifactError("capacity")
    staging = destination.with_name(".jarvis-model-acquire-" + uuid4().hex)
    staging_identity = None
    created = False
    published = False
    failure = None
    bundled = None
    try:
        if bundled_source is not None:
            bundled = _BundledModelSource(bundled_source, cancellation, deadline, assert_current)
            if (destination == bundled.root or destination.is_relative_to(bundled.root)
                    or bundled.root.is_relative_to(destination)):
                raise ArtifactError("bundled_model_invalid")
        staging.mkdir(mode=0o700)
        created = True
        info = staging.lstat()
        staging_identity = info.st_dev, info.st_ino
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}), _Redirects())
                  if bundled is None else None)
        (staging / "onnx").mkdir(mode=0o700)
        for row in MODEL_SPEC["files"]:
            _checkpoint(cancellation, deadline)
            _current(assert_current)
            if bundled is not None:
                descriptor = os.open(staging / row["path"], os.O_WRONLY | os.O_CREAT
                                     | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    bundled.copy(row, output)
            else:
                request = urllib.request.Request(_public_url(row["url"]), headers={
                    "Accept-Encoding": "identity", "User-Agent": "Jarvis-Model-Acquisition/1"})
                timeout = max(0.1, min(20, deadline - time.monotonic()))
                with opener.open(request, timeout=timeout) as response:
                    if response.status != 200:
                        raise ArtifactError("download_rejected")
                    _public_url(response.url)
                    descriptor = os.open(staging / row["path"], os.O_WRONLY | os.O_CREAT
                                         | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(descriptor, "wb") as output:
                        _copy_response(response, output, row, cancellation, deadline)
        descriptor = os.open(staging / "manifest.json", os.O_WRONLY | os.O_CREAT
                             | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(_MANIFEST)
            output.flush()
            os.fsync(output.fileno())
        result = inspect_model(staging, cancellation=cancellation, deadline=deadline)
        _checkpoint(cancellation, deadline)
        # Destination is an admitted new name. Never replace an existing installation.
        if destination.exists() or destination.is_symlink():
            raise ArtifactError("invalid_model")
        _current(assert_current)
        if bundled is not None:
            bundled.assert_current()
        _publish_new(staging, destination)
        published = True
        final = destination.lstat()
        if (final.st_dev, final.st_ino) != staging_identity:
            raise ArtifactError("invalid_model")
        descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {**result, "root_identity": list(staging_identity)}
    except BaseException as error:
        failure = error
        if not published and staging_identity is not None:
            try:
                final = destination.lstat()
                published = (final.st_dev, final.st_ino) == staging_identity
            except FileNotFoundError:
                pass
        if published:
            failure = ArtifactError("publication_unknown", receipt={
                "status": "unknown", "directory": str(destination),
                "root_identity": list(staging_identity),
                "model_fingerprint": MODEL_FINGERPRINT, "inspection_available": True,
                "automatic_retry": False,
            })
            raise failure from None
        if isinstance(error, ArtifactError):
            raise
        if isinstance(error, (OSError, urllib.error.URLError)):
            failure = ArtifactError("bundled_model_unavailable" if bundled_source is not None
                                    else "download_unavailable")
            raise failure from None
        raise
    finally:
        if bundled is not None:
            bundled.close()
        if not published:
            try:
                if created and (staging.exists() or staging.is_symlink()):
                    info = staging.lstat()
                    if (not stat.S_ISDIR(info.st_mode) or staging.is_symlink()
                            or (info.st_dev, info.st_ino) != staging_identity):
                        raise ArtifactError("cleanup_pending")
                    shutil.rmtree(staging)
            except (OSError, ArtifactError):
                raise ArtifactError("cleanup_pending", receipt={
                    "status": "not_published", "cleanup_confirmed": False,
                    "staging_directory": str(staging),
                    "root_identity": list(staging_identity) if staging_identity else None,
                    "automatic_retry": False,
                }) from None
            if failure is not None:
                failure.receipt = {"status": "not_published", "cleanup_confirmed": True,
                                   "staging_removed": True, "directory": str(destination)}


def _known_copy_bytes(directory):
    """Metadata of fixed cache names only; never walk or read unknown content."""
    total = 0
    for name in [row["path"] for row in MODEL_SPEC["files"]] + ["manifest.json"]:
        if name.startswith("onnx/"):
            try:
                parent = (directory / "onnx").lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(parent.st_mode):
                continue  # Never traverse a replaced/symlinked cache ancestor.
        try:
            info = (directory / name).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def repair_model(directory, replacement_directory, *, assert_current,
                 cancellation=None, deadline=None, bundled_source=None):
    """Host-approved fresh copy; activation/CAS and the two slots belong to the host.

    The original is never moved, repaired in place, traversed recursively or
    deleted. Unknown entries remain byte-for-byte at their original locations.
    """
    original, replacement = Path(directory), Path(replacement_directory)
    try:
        _current(assert_current)
        _physical_directory(original)
        _physical_directory(replacement.parent)
        if (original == replacement or replacement.exists() or replacement.is_symlink()
                or replacement.is_relative_to(original) or original.is_relative_to(replacement)):
            raise ArtifactError("invalid_model")
        info = original.lstat()
        identity = info.st_dev, info.st_ino
        previous_bytes = _known_copy_bytes(original)
        required = sum(row["bytes"] for row in MODEL_SPEC["files"]) + len(_MANIFEST)
        if previous_bytes + required > MAX_DOWNLOAD_BYTES:
            raise ArtifactError("capacity")
    except BaseException as error:
        error.receipt = {"status": "not_published", "cleanup_confirmed": True,
                         "staging_removed": True, "directory": str(replacement)}
        raise
    try:
        result = prepare_model(replacement, cancellation=cancellation, deadline=deadline,
                               assert_current=assert_current, bundled_source=bundled_source)
    except BaseException as error:
        # Missing receipts here come only from prepare's read-only preflight;
        # acquisition/cleanup failures carry their own explicit outcome above.
        if getattr(error, "receipt", None) is None:
            error.receipt = {"status": "not_published", "cleanup_confirmed": True,
                             "staging_removed": True, "directory": str(replacement)}
        raise
    try:
        _current(assert_current)
        after = original.lstat()
        if (after.st_dev, after.st_ino) != identity:
            raise ArtifactError("invalid_model")
        replacement_info = replacement.lstat()
        if [replacement_info.st_dev, replacement_info.st_ino] != result["root_identity"]:
            raise ArtifactError("invalid_model")
    except BaseException:
        raise ArtifactError("publication_unknown", receipt={
            "status": "unknown", "directory": str(replacement),
            "root_identity": result.get("root_identity"),
            "model_fingerprint": MODEL_FINGERPRINT, "inspection_available": True,
            "automatic_retry": False, "previous_untouched": True,
        }) from None
    return {**result, "directory": str(replacement), "previous_identity": identity,
            "replacement_identity": list(result["root_identity"]),
            "previous_bytes": previous_bytes, "previous_untouched": True,
            "activation_required": True, "transient_slots_required": 2}


def _ownership_record(value, expected):
    if value is None:
        return False
    if (not isinstance(value, dict) or set(value) != {
            "format", "profile_binding", "slot_id", "root_identity", "asset_fingerprint", "source"}
            or value["format"] != "jarvis-model-copy-ownership/1"
            or value["source"] != "managed_preparation"
            or value["asset_fingerprint"] != MODEL_FINGERPRINT
            or not isinstance(value["profile_binding"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["profile_binding"]) is None
            or not isinstance(value["slot_id"], str)
            or re.fullmatch(r"[a-f0-9]{32}", value["slot_id"]) is None
            or not isinstance(value["root_identity"], (list, tuple))
            or tuple(value["root_identity"]) != expected
            or any(type(part) is not int for part in value["root_identity"])):
        raise ArtifactError("invalid_model")
    # The host callback must bind this exact record to its existing native-owner
    # control. A freshly discovered root/stat is never a replacement for that control.
    return True


def _retire_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or not 0 <= info.st_size <= MAX_DOWNLOAD_BYTES):
        raise ArtifactError("invalid_model")


def _same_file(before, after):
    fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_uid", "st_nlink",
              "st_mtime_ns", "st_ctime_ns")
    return all(getattr(before, key) == getattr(after, key) for key in fields)


def _names(descriptor, allowed):
    result = set()
    with os.scandir(descriptor) as entries:
        for entry in entries:
            if entry.name not in allowed:
                raise ArtifactError("invalid_model")
            result.add(entry.name)
    return result


def retire_model_copy(directory, *, expected_root_identity, assert_current, ownership_record=None):
    """Explicit native-owner retirement of an inactive, registered public copy.

    The host proves this is not its current copy and that all users/workers have
    closed. Without its historical managed-preparation record the manifest must
    still be exact. Never follow links, walk another directory, or delete extras.
    """
    root = Path(directory)
    if (not isinstance(expected_root_identity, (tuple, list))
            or len(expected_root_identity) != 2
            or any(type(part) is not int or part < 0 for part in expected_root_identity)):
        raise ArtifactError("invalid_model")
    expected = tuple(expected_root_identity)
    registered = _ownership_record(ownership_record, expected)
    _current(assert_current)
    _physical_directory(root)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    onnx_fd = None
    opened, removed, removed_bytes = [], [], 0
    root_removed = False
    onnx_removed = False
    direct = {row["path"] for row in MODEL_SPEC["files"] if "/" not in row["path"]}
    allowed = direct | {"manifest.json", "onnx"}
    try:
        root_info = os.fstat(root_fd)
        if (root_info.st_dev, root_info.st_ino) != expected:
            raise ArtifactError("invalid_model")
        names = _names(root_fd, allowed)
        onnx_identity = None
        if "onnx" in names:
            onnx_info = os.stat("onnx", dir_fd=root_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(onnx_info.st_mode) or onnx_info.st_uid != os.getuid()
                    or stat.S_IMODE(onnx_info.st_mode) != 0o700):
                raise ArtifactError("invalid_model")
            onnx_fd = os.open("onnx", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            pinned = os.fstat(onnx_fd)
            onnx_identity = onnx_info.st_dev, onnx_info.st_ino
            if (pinned.st_dev, pinned.st_ino) != onnx_identity:
                raise ArtifactError("invalid_model")
            children = _names(onnx_fd, {"model_qint8_arm64.onnx"})
        else:
            children = set()
        present = (names - {"onnx"}) | {"onnx/" + name for name in children}
        expected_files = {row["path"] for row in MODEL_SPEC["files"]} | {"manifest.json"}
        if not registered and present != expected_files:
            raise ArtifactError("invalid_model")
        total = 0
        for relative in sorted(present, key=lambda name: (name == "manifest.json", name)):
            parent = onnx_fd if relative.startswith("onnx/") else root_fd
            name = relative.rsplit("/", 1)[-1]
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _retire_file(info)
            total += info.st_size
            if total > MAX_DOWNLOAD_BYTES:
                raise ArtifactError("capacity")
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            pinned = os.fstat(descriptor)
            if not _same_file(info, pinned):
                os.close(descriptor)
                raise ArtifactError("invalid_model")
            opened.append((relative, parent, name, descriptor, info))
        if not registered:
            manifest = next(row for row in opened if row[0] == "manifest.json")
            if (manifest[4].st_size != len(_MANIFEST)
                    or os.pread(manifest[3], len(_MANIFEST) + 1, 0) != _MANIFEST):
                raise ArtifactError("invalid_model")

        def current():
            _current(assert_current)
            _physical_directory(root)
            linked = root.lstat()
            if (linked.st_dev, linked.st_ino) != expected:
                raise ArtifactError("invalid_model")
            if _names(root_fd, allowed) != names:
                raise ArtifactError("invalid_model")
            if onnx_fd is not None and not onnx_removed:
                linked_onnx = os.stat("onnx", dir_fd=root_fd, follow_symlinks=False)
                if (linked_onnx.st_dev, linked_onnx.st_ino) != onnx_identity:
                    raise ArtifactError("invalid_model")
                if _names(onnx_fd, {"model_qint8_arm64.onnx"}) != children:
                    raise ArtifactError("invalid_model")
            for relative, parent, name, descriptor, before in opened:
                if relative not in removed:
                    after = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    _retire_file(after)
                    if (not _same_file(before, after)
                            or not _same_file(before, os.fstat(descriptor))):
                        raise ArtifactError("invalid_model")

        current()  # Validate every admitted entry before the first effect.
        for relative, parent, name, _, info in opened:
            if relative == "manifest.json" and onnx_fd is not None and not onnx_removed:
                current()
                os.rmdir("onnx", dir_fd=root_fd)
                names.remove("onnx")
                onnx_removed = True
            current()
            os.unlink(name, dir_fd=parent)
            removed.append(relative)
            removed_bytes += info.st_size
            if relative.startswith("onnx/"):
                children.remove(name)
            else:
                names.remove(name)
        if onnx_fd is not None and not onnx_removed:
            current()
            os.rmdir("onnx", dir_fd=root_fd)
            names.remove("onnx")
            onnx_removed = True
        current()
        os.rmdir(root)
        root_removed = True
        descriptor = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return {"status": "completed", "retired": True, "bytes": removed_bytes,
                "files": len(removed), "root_removed": True}
    except BaseException as error:
        if removed or onnx_removed or root_removed:
            raise ArtifactError("retirement_unknown", receipt={
                "status": "unknown", "bytes": removed_bytes, "files": len(removed),
                "removed_files": tuple(removed), "root_removed": root_removed,
                "automatic_retry": False,
            }) from None
        if isinstance(error, ArtifactError):
            raise
        if isinstance(error, OSError):
            raise ArtifactError("invalid_model") from None
        raise
    finally:
        for _, _, _, descriptor, _ in opened:
            os.close(descriptor)
        if onnx_fd is not None:
            os.close(onnx_fd)
        os.close(root_fd)
