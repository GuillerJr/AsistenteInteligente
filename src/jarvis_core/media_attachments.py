"""Session-scoped immutable attachments in a private workspace artifact area.

Temporary attachments stay in RAM. Persistent artifacts are not profile backup
roles: existing workspace backup policies apply. Revocation writes an exact
tombstone, retains the original bytes, and never silently evicts old artifacts.
Host guards provide authorization; knowing an attachment identifier is not consent.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import wraps
from uuid import uuid4

from .media_documents import MediaError, MediaLimits, checkpoint, media_name, sniff_media
from .media_sources import read_media_source
from .workspace import WorkspaceError, WorkspaceTools, _fingerprint

DIRECTORY = ".jarvis-attachments"
LEGACY_FORMAT = "jarvis-attachment/1"
FORMAT = "jarvis-attachment/2"
_MAX_SAFE_INTEGER = 9007199254740991
_ID = re.compile(r"[a-f0-9]{32}")
_HASH = re.compile(r"[a-f0-9]{64}")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_MANIFEST_KEYS = {
    "format",
    "attachment_id",
    "session_id",
    "workspace_id",
    "temporary",
    "name",
    "mime",
    "kind",
    "byte_length",
    "sha256",
    "source",
    "revoked",
}
_CATALOG_KEYS = {"imported_at_unix_ms", "catalog_sequence", "ingress_run_id", "source_kind"}
_SOURCE_KINDS = {"photo", "document", "audio", "voice", "video", "animation"}


def _identity(value, pattern):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MediaError("media_identity_invalid")
    return value


def _public_io(function):
    @wraps(function)
    def execute(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BlockingIOError:
            raise MediaError("media_store_busy") from None
        except WorkspaceError:
            raise MediaError("media_workspace_unavailable") from None
        except OSError:
            raise MediaError("media_storage_unavailable") from None

    return execute


@dataclass(frozen=True)
class AttachmentScope:
    session_id: str
    workspace_id: str
    temporary: bool = False

    def __post_init__(self):
        _identity(self.session_id, _ID)
        _identity(self.workspace_id, _HASH)
        if type(self.temporary) is not bool:
            raise MediaError("media_scope_invalid")


@dataclass(frozen=True)
class AttachmentManifest:
    attachment_id: str
    session_id: str
    workspace_id: str
    temporary: bool
    name: str
    mime: str
    kind: str
    byte_length: int
    sha256: str
    source: str
    revoked: bool = False
    imported_at_unix_ms: int | None = None
    catalog_sequence: int | None = None
    ingress_run_id: str | None = None
    source_kind: str | None = None

    def public(self):
        value = {
            "format": FORMAT if self.catalog_sequence is not None else LEGACY_FORMAT,
            **{key: getattr(self, key) for key in _MANIFEST_KEYS - {"format"}},
        }
        if self.catalog_sequence is not None:
            value.update({key: getattr(self, key) for key in _CATALOG_KEYS})
        return value


@dataclass(frozen=True)
class AttachmentRead:
    payload: bytes = field(repr=False)
    manifest: AttachmentManifest

    def __iter__(self):
        yield self.payload
        yield self.manifest


class AttachmentStore:
    def __init__(self, workspace, workspace_id, *, limits=None):
        if not isinstance(workspace, WorkspaceTools):
            raise MediaError("media_workspace_invalid")
        self.workspace = workspace
        self.workspace_id = _identity(workspace_id, _HASH)
        self.limits = limits or MediaLimits()
        self._temporary, self._closed, self._mutex = {}, False, threading.RLock()

    def _scope(self, scope):
        if (
            type(scope) is not AttachmentScope
            or scope.workspace_id != self.workspace_id
            or self._closed
        ):
            raise MediaError("media_scope_unavailable")
        AttachmentScope(scope.session_id, scope.workspace_id, scope.temporary)

    def _guard(self, scope, guard, cancellation=None, deadline=None):
        self._scope(scope)
        if not callable(guard):
            raise MediaError("media_authority_required")
        checkpoint(cancellation, deadline)
        guard()
        self._scope(scope)
        checkpoint(cancellation, deadline)

    @staticmethod
    def _private(info, directory=False):
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if (
            not expected(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)
        ):
            raise MediaError("media_store_unsafe")

    def _same_root(self, descriptor):
        with self.workspace._parent(()) as parent:
            actual = os.stat(DIRECTORY, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(descriptor)
            if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
                raise MediaError("media_store_changed")
            self._private(actual, True)

    @contextmanager
    def _locked(self, *, create=False, check=None):
        with self.workspace._parent(()) as parent:
            if create:
                if check is not None:
                    check()
                try:
                    os.mkdir(DIRECTORY, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            try:
                root = os.open(DIRECTORY, _DIR, dir_fd=parent)
            except FileNotFoundError:
                yield None
                return
            lock = None
            try:
                self._private(os.fstat(root), True)
                # Reading the catalog must not create an otherwise absent
                # store. The first reviewed publication creates the lock.
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
                if create:
                    if check is not None:
                        check()
                    flags |= os.O_CREAT
                try:
                    lock = os.open(".lock", flags, 0o600, dir_fd=root)
                except FileNotFoundError:
                    raise MediaError("media_store_incomplete") from None
                self._private(os.fstat(lock))
                # Do not wait indefinitely for another process or guess that
                # its owner died. A fresh explicit request can inspect again.
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._same_root(root)
                yield root
            finally:
                if lock is not None:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    os.close(lock)
                os.close(root)

    @contextmanager
    def _session(self, root, scope, *, create=False, check=None):
        if root is None:
            yield None
            return
        if create:
            if check is not None:
                check()
            try:
                os.mkdir(scope.session_id, 0o700, dir_fd=root)
            except FileExistsError:
                pass
        try:
            fd = os.open(scope.session_id, _DIR, dir_fd=root)
        except FileNotFoundError:
            yield None
            return
        try:
            self._private(os.fstat(fd), True)
            yield fd
        finally:
            os.close(fd)

    def _same_session(self, root, session, scope):
        self._same_root(root)
        info = os.stat(scope.session_id, dir_fd=root, follow_symlinks=False)
        before = os.fstat(session)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise MediaError("media_store_changed")
        self._private(info, True)

    def _read_file(self, parent, name, limit):
        fd = os.open(name, _FILE, dir_fd=parent)
        try:
            before = os.fstat(fd)
            self._private(before)
            if not 1 <= before.st_size <= limit:
                raise MediaError("media_store_invalid")
            chunks, size = [], 0
            while True:
                data = os.read(fd, min(65536, limit + 1 - size))
                if not data:
                    break
                chunks.append(data)
                size += len(data)
                if size > limit:
                    raise MediaError("media_file_limit")
            final = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _fingerprint(before) != _fingerprint(os.fstat(fd)) or _fingerprint(
                before
            ) != _fingerprint(final):
                raise MediaError("media_attachment_changed")
            return b"".join(chunks)
        finally:
            os.close(fd)

    def _manifest(self, parent, scope, attachment_id):
        raw = self._read_file(parent, attachment_id + ".json", 4096)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise MediaError("media_manifest_invalid") from None
        if (
            not isinstance(value, dict)
            or not (
                (set(value) == _MANIFEST_KEYS and value["format"] == LEGACY_FORMAT)
                or (set(value) == _MANIFEST_KEYS | _CATALOG_KEYS and value["format"] == FORMAT)
            )
            or value["attachment_id"] != attachment_id
            or value["session_id"] != scope.session_id
            or value["workspace_id"] != scope.workspace_id
            or value["temporary"] is not False
            or value["revoked"] is not False
            or value["kind"] not in {"document", "pdf", "image", "audio", "video"}
            or type(value["byte_length"]) is not int
            or not 1 <= value["byte_length"] <= self.limits.max_file_bytes
            or not isinstance(value["mime"], str)
            or len(value["mime"]) > 255
            or not isinstance(value["source"], str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value["source"]) is None
        ):
            raise MediaError("media_manifest_invalid")
        if value["format"] == FORMAT:
            if any(
                type(value[key]) is not int or not 1 <= value[key] <= _MAX_SAFE_INTEGER
                for key in ("imported_at_unix_ms", "catalog_sequence")
            ):
                raise MediaError("media_manifest_invalid")
            self._provenance(value["ingress_run_id"], value["source_kind"])
        media_name(value["name"])
        _identity(value["sha256"], _HASH)
        manifest = AttachmentManifest(**{k: v for k, v in value.items() if k != "format"})
        try:
            marker = self._read_file(parent, attachment_id + ".revoked", 512)
        except FileNotFoundError:
            return manifest
        expected = json.dumps(
            {
                "attachment_id": attachment_id,
                "sha256": manifest.sha256,
                "session_id": scope.session_id,
                "workspace_id": scope.workspace_id,
            },
            sort_keys=True,
        ).encode()
        if marker != expected:
            raise MediaError("media_manifest_invalid")
        return replace(manifest, revoked=True)

    @staticmethod
    def _provenance(ingress_run_id, source_kind):
        if ingress_run_id is not None:
            _identity(ingress_run_id, _ID)
        if source_kind is not None and (
            not isinstance(source_kind, str) or source_kind not in _SOURCE_KINDS
        ):
            raise MediaError("media_source_invalid")

    @staticmethod
    def _ordered(rows):
        # Legacy metadata cannot establish chronology. Keep it separate rather
        # than assigning a date from an inode that may have been restored.
        return tuple(sorted(rows, key=lambda row: (
            row.catalog_sequence is None, -(row.catalog_sequence or 0), row.attachment_id
        )))

    def _catalog(self, parent, scope):
        entries = os.listdir(parent)
        if len(entries) > self.limits.max_session_attachments * 3:
            raise MediaError("media_store_limit")
        rows = [
            self._manifest(parent, scope, name[:-5])
            for name in entries
            if re.fullmatch(r"[a-f0-9]{32}\.json", name)
        ]
        sequences = [row.catalog_sequence for row in rows if row.catalog_sequence is not None]
        if len(sequences) != len(set(sequences)):
            raise MediaError("media_catalog_conflict")
        return rows

    @staticmethod
    def _catalogued(row, rows):
        sequence = max((r.catalog_sequence or 0 for r in rows), default=0) + 1
        imported_at = time.time_ns() // 1000000
        if not 1 <= sequence <= _MAX_SAFE_INTEGER or not 1 <= imported_at <= _MAX_SAFE_INTEGER:
            raise MediaError("media_catalog_limit")
        return replace(row, catalog_sequence=sequence, imported_at_unix_ms=imported_at)

    @staticmethod
    def _same_import(previous, candidate):
        if any(
            getattr(previous, key) != getattr(candidate, key)
            for key in _MANIFEST_KEYS - {"format"}
        ):
            return False
        # A repeated import must retain its first publication time/order. A
        # legacy original remains legacy instead of inventing old provenance.
        return previous.catalog_sequence is None or (
            previous.ingress_run_id == candidate.ingress_run_id
            and previous.source_kind == candidate.source_kind
        )

    def _inventory(self, root, scope):
        total = session_total = files = session_files = 0
        if root is None:
            return total, session_total, files, session_files
        entries = os.listdir(root)
        if len(entries) > self.limits.max_workspace_attachments + 1:
            raise MediaError("media_store_limit")
        for name in entries:
            if name == ".lock":
                continue
            _identity(name, _ID)
            fd = os.open(name, _DIR, dir_fd=root)
            try:
                self._private(os.fstat(fd), True)
                members = os.listdir(fd)
                if len(members) > self.limits.max_workspace_attachments * 3:
                    raise MediaError("media_store_limit")
                for member in members:
                    if re.fullmatch(r"[a-f0-9]{32}\.(blob|json|revoked)", member) is None:
                        raise MediaError("media_store_incomplete")
                    info = os.stat(member, dir_fd=fd, follow_symlinks=False)
                    self._private(info)
                    # Metadata/tombstones count too; revocation is not an
                    # eviction strategy that can bypass the storage ceiling.
                    total += info.st_size
                    if name == scope.session_id:
                        session_total += info.st_size
                    if member.endswith(".blob"):
                        files += 1
                        session_files += name == scope.session_id
                    if member.endswith(".json") and member[:-5] + ".revoked" not in members:
                        # Reserve a bounded future tombstone so revocation
                        # never needs to exceed the accepted storage ceiling.
                        total += 512
                        if name == scope.session_id:
                            session_total += 512
            finally:
                os.close(fd)
        return total, session_total, files, session_files

    def _temporary_quota(self, scope, size):
        rows = list(self._temporary.values())
        retained = [r for r in rows if r.manifest.session_id == scope.session_id]
        with self._locked() as root:
            disk_total, disk_session, disk_files, disk_session_files = self._inventory(root, scope)
        if (
            disk_total + sum(len(r.payload) for r in rows) + size > self.limits.max_workspace_bytes
            or disk_session + sum(len(r.payload) for r in retained) + size
            > self.limits.max_session_bytes
            or disk_files + len(rows) >= self.limits.max_workspace_attachments
            or disk_session_files + len(retained) >= self.limits.max_session_attachments
        ):
            raise MediaError("media_store_limit")

    def _publish(self, root, parent, scope, name, data, guard, cancellation, deadline):
        self._same_session(root, parent, scope)
        self._guard(scope, guard, cancellation, deadline)
        temporary = ".stage-" + uuid4().hex
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        published = False
        try:
            view = memoryview(data)
            while view:
                self._guard(scope, guard, cancellation, deadline)
                count = os.write(fd, view[:65536])
                if count <= 0:
                    raise OSError("short attachment write")
                view = view[count:]
            os.fsync(fd)
            self._same_session(root, parent, scope)
            self._guard(scope, guard, cancellation, deadline)
            os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            published = True
            os.unlink(temporary, dir_fd=parent)
            os.fsync(parent)
            self._same_session(root, parent, scope)
            if self._read_file(parent, name, len(data)) != data:
                raise MediaError("media_attachment_changed")
        except BaseException as error:
            if published:
                raise MediaError("media_publication_unknown", effect_status="unknown") from error
            raise
        finally:
            os.close(fd)
            if not published:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    @_public_io
    def ingest_bytes(
        self,
        scope,
        payload,
        name,
        mime=None,
        *,
        attachment_id,
        guard,
        cancellation=None,
        deadline=None,
        source="composer",
        ingress_run_id=None,
        source_kind=None,
    ):
        with self._mutex:
            self._guard(scope, guard, cancellation, deadline)
            _identity(attachment_id, _ID)
            kind = sniff_media(payload, name, mime, limits=self.limits)
            if not isinstance(source, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,31}", source) is None:
                raise MediaError("media_source_invalid")
            self._provenance(ingress_run_id, source_kind)
            row = AttachmentManifest(
                attachment_id,
                scope.session_id,
                scope.workspace_id,
                scope.temporary,
                name,
                kind.mime,
                kind.kind,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
                source,
                ingress_run_id=ingress_run_id,
                source_kind=source_kind,
            )
            if scope.temporary:
                key = (scope.session_id, attachment_id)
                previous = self._temporary.get(key)
                if previous is not None:
                    if not self._same_import(previous.manifest, row) or previous.payload != payload:
                        raise MediaError("media_attachment_conflict")
                    return previous.manifest
                self._temporary_quota(scope, len(payload))
                self._guard(scope, guard, cancellation, deadline)
                row = self._catalogued(row, [
                    r.manifest for r in self._temporary.values()
                    if r.manifest.session_id == scope.session_id
                ])
                self._temporary[key] = AttachmentRead(payload, row)
                return row

            def check():
                self._guard(scope, guard, cancellation, deadline)

            with (
                self._locked(create=True, check=check) as root,
                self._session(root, scope, create=True, check=check) as fd,
            ):
                try:
                    previous = self._manifest(fd, scope, attachment_id)
                except FileNotFoundError:
                    previous = None
                if previous is not None:
                    if not self._same_import(previous, row):
                        raise MediaError("media_attachment_conflict")
                    actual = self._read_file(
                        fd, attachment_id + ".blob", self.limits.max_file_bytes
                    )
                    if actual != payload:
                        raise MediaError("media_attachment_changed")
                    self._guard(scope, guard, cancellation, deadline)
                    return previous
                try:
                    os.stat(attachment_id + ".blob", dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise MediaError(
                        "media_publication_unknown",
                        effect_status="unknown",
                        attachment_id=attachment_id,
                    )
                row = self._catalogued(row, self._catalog(fd, scope))
                serialized = json.dumps(row.public(), sort_keys=True, ensure_ascii=False).encode()
                total, session_total, files, session_files = self._inventory(root, scope)
                temporary_rows = list(self._temporary.values())
                session_temporary = [
                    r for r in temporary_rows if r.manifest.session_id == scope.session_id
                ]
                total += sum(len(r.payload) for r in temporary_rows)
                session_total += sum(len(r.payload) for r in session_temporary)
                files += len(temporary_rows)
                session_files += len(session_temporary)
                needed = len(payload) + len(serialized) + 512
                if (
                    total + needed > self.limits.max_workspace_bytes
                    or session_total + needed > self.limits.max_session_bytes
                    or files >= self.limits.max_workspace_attachments
                    or session_files >= self.limits.max_session_attachments
                ):
                    raise MediaError("media_store_limit")
                blob_published = False
                try:
                    self._publish(
                        root,
                        fd,
                        scope,
                        attachment_id + ".blob",
                        payload,
                        guard,
                        cancellation,
                        deadline,
                    )
                    blob_published = True
                    self._publish(
                        root,
                        fd,
                        scope,
                        attachment_id + ".json",
                        serialized,
                        guard,
                        cancellation,
                        deadline,
                    )
                except BaseException as error:
                    if blob_published or getattr(error, "effect_status", None) == "unknown":
                        raise MediaError(
                            "media_publication_unknown",
                            effect_status="unknown",
                            attachment_id=attachment_id,
                        ) from error
                    raise
                return row

    @_public_io
    def ingest_path(
        self,
        scope,
        path,
        expected_sha256,
        *,
        attachment_id,
        guard,
        cancellation=None,
        deadline=None,
        source="workspace",
    ):
        self._guard(scope, guard, cancellation, deadline)
        selected = read_media_source(
            self.workspace,
            path,
            expected_sha256,
            limits=self.limits,
            cancellation=cancellation,
            deadline=deadline,
            guard=guard,
        )
        return self.ingest_bytes(
            scope,
            selected.payload,
            selected.name,
            selected.media_type.mime,
            attachment_id=attachment_id,
            guard=guard,
            cancellation=cancellation,
            deadline=deadline,
            source=source,
        )

    @_public_io
    def list(self, scope, *, guard=None):
        with self._mutex:
            self._scope(scope)
            if guard is not None:
                guard()
            if scope.temporary:
                return self._ordered(
                    row.manifest
                    for key, row in self._temporary.items()
                    if key[0] == scope.session_id
                )
            with self._locked() as root, self._session(root, scope) as fd:
                if fd is None:
                    return ()
                rows = self._catalog(fd, scope)
                self._same_session(root, fd, scope)
                return self._ordered(rows)

    @_public_io
    def read(
        self, scope, attachment_id, expected_sha256, *, guard, cancellation=None, deadline=None
    ):
        with self._mutex:
            self._guard(scope, guard, cancellation, deadline)
            _identity(attachment_id, _ID)
            _identity(expected_sha256, _HASH)
            if scope.temporary:
                result = self._temporary.get((scope.session_id, attachment_id))
                if result is None:
                    raise MediaError("media_attachment_unavailable")
            else:
                with self._locked() as root, self._session(root, scope) as fd:
                    if fd is None:
                        raise MediaError("media_attachment_unavailable")
                    try:
                        manifest = self._manifest(fd, scope, attachment_id)
                        payload = self._read_file(
                            fd, attachment_id + ".blob", self.limits.max_file_bytes
                        )
                    except FileNotFoundError:
                        raise MediaError("media_attachment_unavailable") from None
                    self._same_session(root, fd, scope)
                    result = AttachmentRead(payload, manifest)
            if result.manifest.revoked:
                raise MediaError("media_attachment_revoked")
            if (
                result.manifest.sha256 != expected_sha256
                or hashlib.sha256(result.payload).hexdigest() != expected_sha256
                or len(result.payload) != result.manifest.byte_length
            ):
                raise MediaError("media_attachment_changed")
            actual_type = sniff_media(
                result.payload, result.manifest.name, result.manifest.mime, limits=self.limits
            )
            if actual_type.kind != result.manifest.kind:
                raise MediaError("media_manifest_invalid")
            self._guard(scope, guard, cancellation, deadline)
            return result

    @_public_io
    def revoke(self, scope, attachment_id, expected_sha256, *, guard):
        with self._mutex:
            self._guard(scope, guard)
            _identity(attachment_id, _ID)
            _identity(expected_sha256, _HASH)
            if scope.temporary:
                key = (scope.session_id, attachment_id)
                value = self._temporary.get(key)
                if value is None or value.manifest.sha256 != expected_sha256:
                    raise MediaError("media_attachment_unavailable")
                self._guard(scope, guard)
                changed = replace(value.manifest, revoked=True)
                self._temporary[key] = AttachmentRead(value.payload, changed)
                return changed
            with self._locked() as root, self._session(root, scope) as fd:
                if fd is None:
                    raise MediaError("media_attachment_unavailable")
                row = self._manifest(fd, scope, attachment_id)
                if row.sha256 != expected_sha256:
                    raise MediaError("media_attachment_changed")
                if not row.revoked:
                    marker = json.dumps(
                        {
                            "attachment_id": attachment_id,
                            "sha256": row.sha256,
                            "session_id": scope.session_id,
                            "workspace_id": scope.workspace_id,
                        },
                        sort_keys=True,
                    ).encode()
                    self._publish(
                        root, fd, scope, attachment_id + ".revoked", marker, guard, None, None
                    )
                return replace(row, revoked=True)

    def clear_temporary(self, scope):
        with self._mutex:
            self._scope(scope)
            if not scope.temporary:
                raise MediaError("media_scope_invalid")
            self._temporary = {
                key: value for key, value in self._temporary.items() if key[0] != scope.session_id
            }

    def close(self):
        with self._mutex:
            self._closed = True
            self._temporary.clear()
