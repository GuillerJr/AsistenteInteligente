"""Bounded host receipts; no paths, credentials, payloads or automatic replay."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass

from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError

_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_TERMINAL = frozenset({"complete", "staged_disarmed", "activated_not_opened", "cancelled",
                       "denied", "failed",
                       "publication_unknown", "cleanup_pending", "interrupted_unknown"})
_PHASES = _TERMINAL | {"awaiting_owner", "quiescing", "working"}
_PURPOSES = {"backup.create", "restore.stage", "profile.restore.activate",
             "memory.restore.copy_scope", "doctor.repair.apply", "workspace.backup.create",
             "workspace.restore.stage", "release.update.apply", "release.rollback.apply"}
_PURPOSES.add("telegram.restore.reconcile")


def wire(value):
    """Do not lose 64-bit file identities through JSON/Swift Double transport."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {key: (str(item) if key in {"device", "inode", "expires_at_ns"} else wire(item))
                for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(item) for item in value]
    return value


def _valid(value):
    if (type(value) is not dict or set(value) != {"version", "operations"}
            or value["version"] != 1 or type(value["operations"]) is not list
            or len(value["operations"]) > 64):
        raise PrivateJsonError("maintenance_journal_invalid")
    seen = set()
    for row in value["operations"]:
        if (type(row) is not dict or set(row) != {
                "operation_id", "purpose", "review_digest", "phase", "receipt"}
                or type(row["operation_id"]) is not str
                or not _ID.fullmatch(row["operation_id"])
                or row["operation_id"] in seen
                or row["purpose"] not in _PURPOSES
                or type(row["review_digest"]) is not str
                or not _HASH.fullmatch(row["review_digest"])
                or row["phase"] not in _PHASES
                or (row["receipt"] is not None and type(row["receipt"]) is not dict)):
            raise PrivateJsonError("maintenance_journal_invalid")
        seen.add(row["operation_id"])
        receipt = row["receipt"]
        if receipt is not None:
            if row["purpose"] == "memory.restore.copy_scope":
                _memory_receipt(row, receipt)
                continue
            if row["purpose"] == "profile.restore.activate":
                _activation_receipt(row, receipt)
                continue
            if row["purpose"] == "telegram.restore.reconcile":
                _channel_receipt(row, receipt)
                continue
            allowed = {"operation_id", "purpose", "status", "effect_status", "target_name",
                       "artifact_id", "manifest_sha256", "target_identity", "new_profile_id",
                       "error_code", "cleanup_error_code", "cleanup_pending"}
            if set(receipt) != allowed or receipt["operation_id"] != row["operation_id"]:
                raise PrivateJsonError("maintenance_journal_invalid")
            if (receipt["purpose"] != row["purpose"]
                    or type(receipt["cleanup_pending"]) is not bool
                    or type(receipt["target_name"]) is not str
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}",
                                    receipt["target_name"]) is None):
                raise PrivateJsonError("maintenance_journal_invalid")
            for key, pattern in (("artifact_id", _ID), ("new_profile_id", _ID),
                                 ("manifest_sha256", _HASH)):
                item = receipt[key]
                if item is not None and (type(item) is not str or not pattern.fullmatch(item)):
                    raise PrivateJsonError("maintenance_journal_invalid")
            identity = receipt["target_identity"]
            if identity is not None and (type(identity) is not dict
                    or set(identity) != {"device", "inode"}
                    or any(type(item) is not str or not item.isascii() or not item.isdecimal()
                           or len(item) > 20 or int(item) >= 1 << 64
                           for item in identity.values())):
                raise PrivateJsonError("maintenance_journal_invalid")
            # Only the recovery core's fixed receipt DTO may enter this registry.
            for key in {"error_code", "cleanup_error_code", "effect_status", "status"}:
                item = receipt[key]
                if item is not None and (type(item) is not str
                        or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", item) is None):
                    raise PrivateJsonError("maintenance_journal_invalid")


def _channel_receipt(row, receipt):
    from jarvis_core.telegram import TelegramError, _route

    policy = {"chat_type", "allowed_sender_ids", "message_thread_id", "allow_attachments"}
    required = {"operation_id", "purpose", "request_digest", "status", "effect_status",
                "bot_id", "chat_id", "route_revision_before", "route_revision_after",
                "enabled_before", "enabled_after", "configuration_hash_before",
                "configuration_hash_after", "error_code", "cleanup_pending", "network_started",
                "provider_started", "grants_restored", "historical_events_replayed",
                "permits_replay"}
    if (set(receipt) not in (required, required | policy)
            or receipt["operation_id"] != row["operation_id"]
            or receipt["purpose"] != row["purpose"]
            or receipt["request_digest"] != row["review_digest"]
            or receipt["status"] not in {"completed", "failed", "cancelled", "unknown"}
            or receipt["effect_status"] not in {
                "route_reconciled", "no_route_change_confirmed", "route_change_unknown"}
            or type(receipt["cleanup_pending"]) is not bool
            or any(receipt[key] is not False for key in (
                "network_started", "provider_started", "grants_restored",
                "historical_events_replayed", "permits_replay"))
            or type(receipt["enabled_before"]) is not bool
            or type(receipt["enabled_after"]) is not bool
            or receipt["enabled_before"] is receipt["enabled_after"]):
        raise PrivateJsonError("maintenance_journal_invalid")
    for key in ("bot_id", "route_revision_before"):
        if type(receipt[key]) is not int or not 1 <= receipt[key] < 1 << 52:
            raise PrivateJsonError("maintenance_journal_invalid")
    try:
        _route({"chat_id": receipt["chat_id"], "agent_id": "jarvis", "session_id": "a" * 32,
                "revision": 1, "enabled": False,
                **{key: receipt[key] for key in policy if key in receipt}})
    except TelegramError:
        raise PrivateJsonError("maintenance_journal_invalid") from None
    for key in ("request_digest", "configuration_hash_before", "configuration_hash_after"):
        item = receipt[key]
        if item is None and key == "configuration_hash_after":
            continue
        if type(item) is not str or _HASH.fullmatch(item) is None:
            raise PrivateJsonError("maintenance_journal_invalid")
    after = receipt["route_revision_after"]
    if after is not None and (
            type(after) is not int or after != receipt["route_revision_before"] + 1):
        raise PrivateJsonError("maintenance_journal_invalid")
    error = receipt["error_code"]
    if error is not None and (type(error) is not str or re.fullmatch(
            r"[a-z][a-z0-9_]{0,127}", error) is None):
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] == "completed" and (
            receipt["effect_status"] != "route_reconciled" or after is None
            or receipt["configuration_hash_after"] is None or error is not None
            or receipt["cleanup_pending"]):
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] == "unknown" and receipt["effect_status"] != "route_change_unknown":
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] in {"failed", "cancelled"} and (
            receipt["effect_status"] != "no_route_change_confirmed" or after is not None
            or receipt["configuration_hash_after"] is not None or receipt["cleanup_pending"]):
        raise PrivateJsonError("maintenance_journal_invalid")


def _activation_receipt(row, receipt):
    required = {"operation_id", "purpose", "status", "effect_status", "target_identity",
                "marker_sha256", "admission_sha256", "error_code", "cleanup_pending",
                "cleanup_error_code", "services_started", "grants_restored"}
    if (set(receipt) != required or receipt["operation_id"] != row["operation_id"]
            or receipt["purpose"] != row["purpose"]
            or type(receipt["cleanup_pending"]) is not bool
            or receipt["services_started"] is not False
            or receipt["grants_restored"] is not False
            or receipt["status"] not in {"completed", "failed", "cancelled", "unknown"}
            or receipt["effect_status"] not in {
                "local_admission_published", "not_activated", "publication_unknown"}):
        raise PrivateJsonError("maintenance_journal_invalid")
    if (receipt["status"] == "completed"
            and (receipt["effect_status"] != "local_admission_published"
                 or receipt["cleanup_pending"]
                 or receipt["target_identity"] is None
                 or receipt["admission_sha256"] is None
                 or receipt["marker_sha256"] is None)):
        raise PrivateJsonError("maintenance_journal_invalid")
    if (receipt["effect_status"] == "publication_unknown"
            and receipt["status"] != "unknown"):
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] in {"failed", "cancelled"} and (
            receipt["effect_status"] != "not_activated" or receipt["cleanup_pending"]):
        raise PrivateJsonError("maintenance_journal_invalid")
    identity = receipt["target_identity"]
    if identity is not None and (type(identity) is not dict
            or set(identity) != {"device", "inode"}
            or any(type(item) is not str or not item.isascii() or not item.isdecimal()
                   or len(item) > 20 or int(item) >= 1 << 64
                   for item in identity.values())):
        raise PrivateJsonError("maintenance_journal_invalid")
    for key in ("marker_sha256", "admission_sha256"):
        item = receipt[key]
        if item is not None and (type(item) is not str or not _HASH.fullmatch(item)):
            raise PrivateJsonError("maintenance_journal_invalid")
    for key in ("error_code", "cleanup_error_code"):
        item = receipt[key]
        if item is not None and (type(item) is not str
                or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", item) is None):
            raise PrivateJsonError("maintenance_journal_invalid")


def _memory_receipt(row, receipt):
    required = {"operation_id", "purpose", "request_digest", "core_review_digest", "status",
                "effect_status", "document_count", "byte_length", "mapping_sha256",
                "destination_generation_after", "error_code", "cleanup_pending"}
    if (set(receipt) != required or receipt["operation_id"] != row["operation_id"]
            or receipt["purpose"] != row["purpose"]
            or receipt["request_digest"] != row["review_digest"]
            or type(receipt["cleanup_pending"]) is not bool
            or receipt["status"] not in {"completed", "cancelled", "failed", "unknown"}
            or receipt["effect_status"] not in {
                "scope_copy_committed", "no_scope_copy_committed", "scope_copy_unknown"}):
        raise PrivateJsonError("maintenance_journal_invalid")
    for key in ("request_digest", "core_review_digest"):
        if type(receipt[key]) is not str or not _HASH.fullmatch(receipt[key]):
            raise PrivateJsonError("maintenance_journal_invalid")
    for key, maximum in (("document_count", 100_000), ("byte_length", 64 * 1024 * 1024)):
        if type(receipt[key]) is not int or not 0 <= receipt[key] <= maximum:
            raise PrivateJsonError("maintenance_journal_invalid")
    mapping = receipt["mapping_sha256"]
    if mapping is not None and (type(mapping) is not str or not _HASH.fullmatch(mapping)):
        raise PrivateJsonError("maintenance_journal_invalid")
    generation = receipt["destination_generation_after"]
    if generation is not None and (type(generation) is not str
            or re.fullmatch(r"0|[1-9][0-9]{0,15}", generation) is None
            or int(generation) > (1 << 52) - 1):
        raise PrivateJsonError("maintenance_journal_invalid")
    error = receipt["error_code"]
    if error is not None and (type(error) is not str
            or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", error) is None):
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] == "completed" and (
            receipt["effect_status"] != "scope_copy_committed" or receipt["cleanup_pending"]
            or not receipt["document_count"]
            or receipt["byte_length"] < receipt["document_count"]
            or mapping is None or generation is None or error is not None):
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] in {"cancelled", "failed"} and (
            receipt["effect_status"] != "no_scope_copy_committed" or receipt["cleanup_pending"]
            or receipt["document_count"] != 0 or receipt["byte_length"] != 0
            or mapping is not None or generation is not None):
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["status"] == "unknown" and receipt["effect_status"] != "scope_copy_unknown":
        raise PrivateJsonError("maintenance_journal_invalid")
    if receipt["cleanup_pending"] and receipt["status"] != "unknown":
        raise PrivateJsonError("maintenance_journal_invalid")


class MaintenanceJournal:
    def __init__(self, directory):
        self.document = PrivateJsonDocument(
            directory, "maintenance.json",
            initial=lambda: {"version": 1, "operations": []}, validate=_valid,
        )
        # Reading a previous receipt never starts cleanup or writes a recovery.
        self.previous = tuple(self._interrupted(row) for row in
                              self.document.read()["operations"])

    @staticmethod
    def _interrupted(row):
        if row["phase"] in _TERMINAL:
            return dict(row)
        return {**row, "phase": "interrupted_unknown", "receipt": None}

    def record(self, operation_id, purpose, digest, phase, receipt=None):
        row = {"operation_id": operation_id, "purpose": purpose,
               "review_digest": digest, "phase": phase, "receipt": wire(receipt)}

        def change(value):
            rows = [self._interrupted(item) for item in value["operations"]
                    if item["operation_id"] != operation_id]
            rows.append(row)
            while len(rows) > 64:
                disposable = next((index for index, item in enumerate(rows)
                                   if item["phase"] in {
                                       "complete", "staged_disarmed", "activated_not_opened",
                                       "cancelled", "denied", "failed"}
                                   and not (item["receipt"] or {}).get("cleanup_pending")
                                   and (item["receipt"] or {}).get("status") != "unknown"
                                   and (item["receipt"] or {}).get("effect_status")
                                   not in {"unknown", "publication_unknown"}),
                                  None)
                if disposable is None:
                    raise PrivateJsonError("maintenance_journal_capacity")
                rows.pop(disposable)
            return {"version": 1, "operations": rows}

        self.document.update(change)
