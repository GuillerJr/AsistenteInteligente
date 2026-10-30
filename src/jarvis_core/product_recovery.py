"""Finite portable history for product, coordination and local project metadata.

The existing recovery host owns the quiescent cut and approval authority. This
module reads only fixed paths inside its explicitly selected profile. Import
publishes one inert sidecar; it never rebuilds live ACLs, reconnects workspaces,
opens Git repositories, admits client keys or replays an effect. Previous
sidecars and content-addressed snapshot blobs survive subsequent backups.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path

from . import product_interactions, session_coordination, state
from .product_projects import _validate as validate_projects
from .product_projects_github import _publications_validate as validate_publications
from .recovery_fs import Directory, PinnedFile
from .recovery_sqlite import deny_extension_loading

FORMAT = "jarvis-product-recovery/1"
ROLE = "product"
SIDECAR = "product-recovery.json"
MAX_BYTES = 96 * 1024 * 1024
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_DISARMS = {
    "live_activation_allowed": False,
    "permissions_restored": False,
    "members_active": False,
    "client_ids_active": False,
    "external_paths_active": False,
    "notification_preferences_enabled": False,
    "questions_resumed": False,
    "notifications_dispatched": False,
    "messages_delivered": False,
    "reservations_dispatched": False,
    "tool_effects_replayed": False,
    "github_credentials_restored": False,
}
_EXCLUSIONS = (
    "temporary_sqlite_and_project_state", "volatile_question_answers",
    "github_credentials_and_live_connections", "transport_and_registry_locks",
    "external_workspaces_and_live_worktree_files", "live_approval_capabilities",
)
_COORD_TABLES = (
    "workspaces", "session_links", "members", "messages", "message_requests",
    "message_correlations", "budget_limits", "reservations", "run_requests",
)
_INTERACTION_TABLES = ("product_format", "product_records")


class ProductRecoveryError(ValueError):
    def __init__(self, code, *, effect_status="known"):
        super().__init__(code)
        self.code, self.effect_status = code, effect_status


@dataclass(frozen=True)
class ProductRecoveryLimits:
    max_bytes: int = MAX_BYTES
    max_sqlite_bytes: int = 64 * 1024 * 1024
    max_rows: int = 100000
    max_cell_bytes: int = 65536
    max_blob_bytes: int = 4194304
    max_blob_total_bytes: int = 64 * 1024 * 1024
    max_blobs: int = 16384
    max_archives: int = 32
    max_vm_steps: int = 20000000

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ProductRecoveryError("product_recovery_limits_invalid")
        if self.max_bytes > MAX_BYTES:
            raise ProductRecoveryError("product_recovery_limits_invalid")


@dataclass(frozen=True)
class ProductSqliteSource:
    connection: sqlite3.Connection
    check_current: object

    def __post_init__(self):
        if not isinstance(self.connection, sqlite3.Connection) or not callable(self.check_current):
            raise ProductRecoveryError("product_recovery_source_invalid")


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProductRecoveryError("product_recovery_json_invalid") from None


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ProductRecoveryError("product_recovery_duplicate_key")
        value[key] = item
    return value


def decode_product_document(data, *, limits=None):
    limits = limits or ProductRecoveryLimits()
    if not isinstance(data, bytes) or len(data) > limits.max_bytes:
        raise ProductRecoveryError("product_recovery_size_limit")
    try:
        value = json.loads(data, object_pairs_hook=_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ProductRecoveryError("product_recovery_json_invalid") from None
    validate_product_document(value, limits=limits)
    return value


def _reference_schema(role):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        if role == "coordination":
            session_coordination.SessionCoordinationStore._initialize(connection)
        else:
            state.StateStore._initialize(connection)
            for statement in product_interactions._SCHEMA:
                connection.execute(statement)
        return {row[0]: (row[1], " ".join(row[2].split()) if row[2] else None)
                for row in connection.execute(
                    "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    finally:
        connection.close()


def _schema_columns(role):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        if role == "coordination":
            session_coordination.SessionCoordinationStore._initialize(connection)
            tables = _COORD_TABLES
        else:
            state.StateStore._initialize(connection)
            for statement in product_interactions._SCHEMA:
                connection.execute(statement)
            tables = _INTERACTION_TABLES
        return {table: tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
                for table in tables}
    finally:
        connection.close()


def _validate_tables(role, value, limits):
    columns = _schema_columns(role)
    if not isinstance(value, dict) or set(value) != set(columns):
        raise ProductRecoveryError("product_recovery_tables_invalid")
    count = 0
    for table, names in columns.items():
        rows = value[table]
        if not isinstance(rows, list):
            raise ProductRecoveryError("product_recovery_rows_invalid")
        count += len(rows)
        if count > limits.max_rows:
            raise ProductRecoveryError("product_recovery_row_limit")
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(names):
                raise ProductRecoveryError("product_recovery_row_invalid")
            for cell in row.values():
                if cell is not None and type(cell) not in {str, int}:
                    raise ProductRecoveryError("product_recovery_cell_invalid")
                if isinstance(cell, str) and len(cell.encode()) > limits.max_cell_bytes:
                    raise ProductRecoveryError("product_recovery_cell_limit")
    if role == "interactions":
        if value["product_format"] != [{"version": 1}]:
            raise ProductRecoveryError("product_recovery_format_unsupported")
        for row in value["product_records"]:
            try:
                scope = json.loads(row["scope_json"])
                product_interactions.InteractionScope(**scope)
                payload = json.loads(row["payload_json"])
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise ProductRecoveryError("product_recovery_interaction_invalid") from None
            if scope["temporary"] or not isinstance(payload, dict):
                raise ProductRecoveryError("product_recovery_temporary_data_forbidden")
            # The live domain never durably stores an answer. Reject a future
            # accidental schema drift rather than importing volatile values.
            if row["kind"] == "question" and set(payload) != {
                    "questions", "grants_tool_approval"}:
                raise ProductRecoveryError("product_recovery_answer_data_forbidden")


def validate_product_document(value, *, limits=None):
    """Validate portable history and every blob hash without following a path."""
    limits = limits or ProductRecoveryLimits()
    if (not isinstance(value, dict) or set(value) != {
            "format", "state", "archives", "blobs", "disarms", "exclusions"}
            or value["format"] != FORMAT or value["state"] not in {"backup", "restored_history"}
            or value["disarms"] != _DISARMS or value["exclusions"] != list(_EXCLUSIONS)
            or any(flag is not False for flag in value["disarms"].values())
            or not isinstance(value["archives"], list)
            or not 1 <= len(value["archives"]) <= limits.max_archives
            or not isinstance(value["blobs"], dict) or len(value["blobs"]) > limits.max_blobs):
        raise ProductRecoveryError("product_recovery_document_invalid")
    if len(_canonical(value)) > limits.max_bytes:
        raise ProductRecoveryError("product_recovery_size_limit")
    blob_sizes, total, seen = {}, 0, set()
    for digest, encoded in value["blobs"].items():
        if (not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or not isinstance(encoded, str)):
            raise ProductRecoveryError("product_recovery_blob_invalid")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ProductRecoveryError("product_recovery_blob_invalid") from None
        total += len(raw)
        if len(raw) > limits.max_blob_bytes or total > limits.max_blob_total_bytes:
            raise ProductRecoveryError("product_recovery_blob_limit")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ProductRecoveryError("product_recovery_blob_changed")
        blob_sizes[digest] = len(raw)
    for archive in value["archives"]:
        if (not isinstance(archive, dict) or set(archive) != {"archive_id", "source"}
                or archive["archive_id"] != _digest(archive["source"])
                or archive["archive_id"] in seen):
            raise ProductRecoveryError("product_recovery_archive_invalid")
        seen.add(archive["archive_id"])
        source = archive["source"]
        if (not isinstance(source, dict) or set(source) != {
                "coordination", "interactions", "projects", "github_publications", "blob_hashes"}
                or not isinstance(source["github_publications"], dict)
                or len(source["github_publications"]) > 128
                or not isinstance(source["blob_hashes"], list)
                or any(not isinstance(digest, str) or not _HASH.fullmatch(digest)
                       for digest in source["blob_hashes"])
                or len(source["blob_hashes"]) != len(set(source["blob_hashes"]))
                or any(digest not in blob_sizes for digest in source["blob_hashes"])):
            raise ProductRecoveryError("product_recovery_archive_invalid")
        for role in ("coordination", "interactions"):
            if source[role] is not None:
                _validate_tables(role, source[role], limits)
        if source["projects"] is not None:
            validate_projects(source["projects"])
            if any(row["temporary"] for row in source["projects"]["projects"].values()):
                raise ProductRecoveryError("product_recovery_temporary_data_forbidden")
            for snapshot in source["projects"]["snapshots"].values():
                for item in snapshot["files"].values():
                    if (item["sha256"] not in source["blob_hashes"]
                            or blob_sizes[item["sha256"]] != item["bytes"]):
                        raise ProductRecoveryError("product_recovery_snapshot_blob_missing")
        for client_id, publications in source["github_publications"].items():
            if not isinstance(client_id, str) or not _HASH.fullmatch(client_id):
                raise ProductRecoveryError("product_recovery_client_invalid")
            validate_publications(publications)
    if set(blob_sizes) != {digest for archive in value["archives"]
                           for digest in archive["source"]["blob_hashes"]}:
        raise ProductRecoveryError("product_recovery_unclassified_blob")


def _read(directory, name, maximum, checkpoint):
    try:
        with PinnedFile(directory, name, maximum) as file:
            return file.read(lambda: checkpoint("product_read"))
    except FileNotFoundError:
        return None


def _json_read(directory, name, maximum, checkpoint):
    raw = _read(directory, name, maximum, checkpoint)
    if raw is None:
        return None
    try:
        return json.loads(raw, object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ProductRecoveryError("product_recovery_source_json_invalid") from None


def _sqlite_image(directory, name, role, sources, checkpoint, limits):
    source = sources.get(role)
    if source is not None:
        source.check_current()
        if source.connection.in_transaction:
            raise ProductRecoveryError("product_recovery_source_busy")
        main = [row[2] for row in source.connection.execute("PRAGMA database_list")
                if row[1] == "main"]
        if main != [str(directory.path / name)]:
            raise ProductRecoveryError("product_recovery_source_path_mismatch")
        with PinnedFile(directory, name, limits.max_sqlite_bytes):
            pass
        target = sqlite3.connect(":memory:", isolation_level=None)
        try:
            deny_extension_loading(target)
            page_size = source.connection.execute("PRAGMA page_size").fetchone()[0]

            def progress(_status, _remaining, total):
                checkpoint("product_sqlite_backup")
                source.check_current()
                if total * page_size > limits.max_sqlite_bytes:
                    raise ProductRecoveryError("product_recovery_sqlite_limit")
            source.connection.backup(target, pages=32, progress=progress, sleep=0)
            source.check_current()
            data = target.serialize()
        finally:
            target.close()
        if len(data) > limits.max_sqlite_bytes:
            raise ProductRecoveryError("product_recovery_sqlite_limit")
        return data
    for suffix in ("-wal", "-journal"):
        try:
            info = os.stat(name + suffix, dir_fd=directory.fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if info.st_size:
            raise ProductRecoveryError("product_recovery_live_sqlite_source_required")
    return _read(directory, name, limits.max_sqlite_bytes, checkpoint)


def _dump_sqlite(data, role, checkpoint, limits):
    if data is None:
        return None
    connection = sqlite3.connect(":memory:", isolation_level=None)
    steps = [0]
    stopped = []

    def progress():
        steps[0] += 1000
        try:
            checkpoint("product_sqlite_inspect")
            if steps[0] > limits.max_vm_steps:
                raise ProductRecoveryError("product_recovery_work_limit")
        except BaseException as error:
            stopped.append(error)
            return 1
        return 0
    try:
        deny_extension_loading(connection)
        connection.deserialize(data)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.set_progress_handler(progress, 1000)
        actual = {row[0]: (row[1], " ".join(row[2].split()) if row[2] else None)
                  for row in connection.execute(
                      "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        if actual != _reference_schema(role):
            raise ProductRecoveryError("product_recovery_schema_unsupported")
        expected_id = (session_coordination._APPLICATION_ID if role == "coordination"
                       else state._APPLICATION_ID)
        if connection.execute("PRAGMA application_id").fetchone()[0] != expected_id:
            raise ProductRecoveryError("product_recovery_sqlite_identity_invalid")
        if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise ProductRecoveryError("product_recovery_sqlite_invalid")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ProductRecoveryError("product_recovery_sqlite_invalid")
        if role == "interactions":
            if any(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                   for table in ("sessions", "runs", "messages", "events")):
                raise ProductRecoveryError("product_recovery_unexpected_conversation_data")
        tables = _schema_columns(role)
        result, count = {}, 0
        for table, columns in tables.items():
            count += connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            if count > limits.max_rows:
                raise ProductRecoveryError("product_recovery_row_limit")
            order = ",".join('"' + column + '"' for column in columns)
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}')
            result[table] = [dict(zip(columns, row, strict=True)) for row in rows]
        _validate_tables(role, result, limits)
        return result
    except sqlite3.Error:
        if stopped:
            raise stopped[0] from None
        raise ProductRecoveryError("product_recovery_sqlite_invalid") from None
    finally:
        connection.close()


def _projects(directory, checkpoint, limits):
    source, publications, blobs, blob_total = None, {}, {}, 0
    try:
        projects = Directory(directory.path / "projects")
    except FileNotFoundError:
        return source, publications, blobs
    with projects:
        entries = os.listdir(projects.fd)
        if len(entries) > 132:
            raise ProductRecoveryError("product_recovery_inventory_limit")
        source = _json_read(projects, "projects.json", 4194304, checkpoint)
        if source is not None:
            validate_projects(source)
        for name in entries:
            checkpoint("product_project_inventory")
            if name in {"projects.json", "projects.json.lock"}:
                continue
            if name == "project_blobs":
                with Directory(projects.path / name) as blob_directory:
                    names = os.listdir(blob_directory.fd)
                    if len(names) > limits.max_blobs:
                        raise ProductRecoveryError("product_recovery_blob_limit")
                    for digest in sorted(names):
                        if not _HASH.fullmatch(digest):
                            raise ProductRecoveryError("product_recovery_unclassified_blob")
                        raw = _read(blob_directory, digest, limits.max_blob_bytes, checkpoint)
                        if raw is None or hashlib.sha256(raw).hexdigest() != digest:
                            raise ProductRecoveryError("product_recovery_blob_changed")
                        blob_total += len(raw)
                        if blob_total > limits.max_blob_total_bytes:
                            raise ProductRecoveryError("product_recovery_blob_limit")
                        blobs[digest] = base64.b64encode(raw).decode()
                continue
            if not _HASH.fullmatch(name):
                raise ProductRecoveryError("product_recovery_inventory_unclassified")
            with Directory(projects.path / name) as client_directory:
                if set(os.listdir(client_directory.fd)) - {
                        "github_publications.json", "github_publications.json.lock"}:
                    raise ProductRecoveryError("product_recovery_inventory_unclassified")
                value = _json_read(
                    client_directory, "github_publications.json", 2097152, checkpoint)
                if value is not None:
                    validate_publications(value)
                    publications[name] = value
        projects.check()
    return source, publications, blobs


def product_source_fingerprint(profile_directory):
    """Metadata-only CAS for exactly the files participating in product export."""
    values = []

    def observe(parent, name, relative, *, is_directory=False):
        try:
            info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
        except FileNotFoundError:
            values.append((relative, None))
            return False
        expected = stat.S_ISDIR if is_directory else stat.S_ISREG
        if (not expected(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != (0o700 if is_directory else 0o600)
                or (not is_directory and info.st_nlink != 1)):
            raise ProductRecoveryError("product_recovery_inventory_unsafe")
        values.append((relative, (info.st_dev, info.st_ino, info.st_size,
                                  info.st_mtime_ns, info.st_ctime_ns)))
        return True

    with Directory(profile_directory) as root:
        observe(root, SIDECAR, SIDECAR)
        for name in ("session-coordination.sqlite3", "product-interactions.sqlite3"):
            for suffix in ("", ".lock", "-journal", "-wal", "-shm"):
                observe(root, name + suffix, name + suffix)
        if observe(root, "projects", "projects", is_directory=True):
            with Directory(root.path / "projects") as projects:
                names = sorted(os.listdir(projects.fd))
                if len(names) > 132:
                    raise ProductRecoveryError("product_recovery_inventory_limit")
                for name in names:
                    if name in {"projects.json", "projects.json.lock"}:
                        observe(projects, name, "projects/" + name)
                    elif name == "project_blobs" or _HASH.fullmatch(name):
                        observe(projects, name, "projects/" + name, is_directory=True)
                        with Directory(projects.path / name) as child:
                            children = sorted(os.listdir(child.fd))
                            if len(children) > 16384:
                                raise ProductRecoveryError("product_recovery_inventory_limit")
                            for entry in children:
                                if ((name == "project_blobs" and not _HASH.fullmatch(entry))
                                        or (name != "project_blobs" and entry not in {
                                            "github_publications.json",
                                            "github_publications.json.lock"})):
                                    raise ProductRecoveryError(
                                        "product_recovery_inventory_unclassified")
                                observe(child, entry, "projects/" + name + "/" + entry)
                    else:
                        raise ProductRecoveryError("product_recovery_inventory_unclassified")
    return _digest(values)


def export_product_state(profile_directory, *, sqlite_sources=None, checkpoint=lambda _stage: None,
                         limits=None):
    """Capture durable sources under the host's existing quiescent profile cut.

    ``sqlite_sources`` may contain coordination/interactions live connections,
    each with its host pin check. RAM stores and credentials are never accepted.
    Missing sources return None, so the recovery manifest can mark an absent role.
    """
    limits = limits or ProductRecoveryLimits()
    sources = {} if sqlite_sources is None else dict(sqlite_sources)
    if (set(sources) - {"coordination", "interactions"}
            or any(not isinstance(source, ProductSqliteSource) for source in sources.values())):
        raise ProductRecoveryError("product_recovery_source_invalid")
    checkpoint("product_export_start")
    with Directory(profile_directory) as directory:
        previous_raw = _read(directory, SIDECAR, limits.max_bytes, checkpoint)
        previous = (decode_product_document(previous_raw, limits=limits)
                    if previous_raw is not None else None)
        projects, publications, blobs = _projects(directory, checkpoint, limits)
        source = {"coordination": _dump_sqlite(_sqlite_image(
                      directory, "session-coordination.sqlite3", "coordination", sources,
                      checkpoint, limits), "coordination", checkpoint, limits),
                  "interactions": _dump_sqlite(_sqlite_image(
                      directory, "product-interactions.sqlite3", "interactions", sources,
                      checkpoint, limits), "interactions", checkpoint, limits),
                  "projects": projects, "github_publications": publications,
                  "blob_hashes": sorted(blobs)}
        archives = [] if previous is None else previous["archives"]
        if previous is not None:
            for digest, blob in previous["blobs"].items():
                if digest in blobs and blobs[digest] != blob:
                    raise ProductRecoveryError("product_recovery_blob_changed")
                blobs[digest] = blob
        if sum(len(blob) // 4 * 3 - (len(blob) - len(blob.rstrip("=")))
               for blob in blobs.values()) > limits.max_blob_total_bytes:
            raise ProductRecoveryError("product_recovery_blob_limit")
        if (projects is not None or publications or source["blob_hashes"]
                or source["coordination"] is not None or source["interactions"] is not None):
            archive = {"archive_id": _digest(source), "source": source}
            if not any(row["archive_id"] == archive["archive_id"] for row in archives):
                archives.append(archive)
        if not archives:
            return None
        value = {"format": FORMAT, "state": "backup", "archives": archives, "blobs": blobs,
                 "disarms": dict(_DISARMS), "exclusions": list(_EXCLUSIONS)}
        validate_product_document(value, limits=limits)
        directory.check()
        checkpoint("product_export_complete")
        return _canonical(value)


def disarm_product_document(value, *, limits=None):
    """Keep original history; mark it exclusively for explicit later review.

    Archived client IDs, member rows, paths and pending statuses are evidence,
    not operational tables. No live importer consumes this document. A future
    adoption flow must make a new owner choice and obtain new authority.
    """
    validate_product_document(value, limits=limits)
    result = json.loads(_canonical(value))
    result["state"] = "restored_history"
    result["disarms"] = dict(_DISARMS)
    validate_product_document(result, limits=limits)
    return result


def import_product_state(value, destination, *, authorize, checkpoint=lambda _stage: None,
                         limits=None):
    """Publish one fixed sidecar to a new profile; preserve every existing file."""
    if not callable(authorize):
        raise ProductRecoveryError("product_recovery_authority_required")
    if isinstance(value, bytes):
        value = decode_product_document(value, limits=limits)
    data = _canonical(disarm_product_document(value, limits=limits))
    with Directory(Path(destination)) as directory:
        directory.check()
        authorize()
        checkpoint("product_history_write")
        directory.check()
        descriptor = os.open(SIDECAR, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory.fd)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                output.write(data)
                output.flush()
                os.fsync(descriptor)
            os.fsync(directory.fd)
            actual = _read(directory, SIDECAR, len(data), checkpoint)
            if actual != data:
                raise ProductRecoveryError("product_recovery_publication_unknown",
                                           effect_status="unknown")
        except BaseException as error:
            raise ProductRecoveryError("product_recovery_publication_unknown",
                                       effect_status="unknown") from error
        finally:
            os.close(descriptor)
        return {"path": SIDECAR, "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data), "history_only": True, "live_objects_created": False,
                "external_paths_activated": False, "effect_status": "known"}
