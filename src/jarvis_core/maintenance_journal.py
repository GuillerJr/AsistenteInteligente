"""Bounded host receipts; no paths, credentials, payloads or automatic replay."""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass

from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError

_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_TERMINAL = frozenset({"complete", "staged_disarmed", "cancelled", "denied", "failed",
                       "publication_unknown", "cleanup_pending", "interrupted_unknown"})
_PHASES = _TERMINAL | {"awaiting_owner", "quiescing", "working"}
_PURPOSES = {"backup.create", "restore.stage"}


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
                                       "complete", "staged_disarmed", "cancelled", "denied",
                                       "failed"}
                                   and not (item["receipt"] or {}).get("cleanup_pending")
                                   and (item["receipt"] or {}).get("effect_status") != "unknown"),
                                  None)
                if disposable is None:
                    raise PrivateJsonError("maintenance_journal_capacity")
                rows.pop(disposable)
            return {"version": 1, "operations": rows}

        self.document.update(change)
