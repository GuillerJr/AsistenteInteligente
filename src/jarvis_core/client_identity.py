"""Explicit local client identities, independent of the discovery bearer token.

Ed25519 is supplied by cryptography; no cryptographic primitive is implemented
here. A launch pin admits the first desktop identity, never a socket bearer.
Credentials live next to, outside, the selected profile and are not portable.
This authenticates possession of an admitted key, not an Apple code signature or
a sandbox against a malicious process running as the same macOS user.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from jarvis_core.private_json import PrivateJsonDocument, PrivateJsonError
from jarvis_core.recovery_fs import Directory

_HEX = re.compile(r"[a-f0-9]{64}\Z")


class ClientIdentityError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def client_directory(profile):
    profile = Path(profile)
    if not profile.is_absolute() or profile.name in {"", ".", ".."}:
        raise ClientIdentityError("client_profile_invalid")
    return profile.with_name(profile.name + ".clients")


def profile_binding(profile):
    with Directory(profile) as directory:
        return hashlib.sha256(
            f"jarvis-client-profile/1:{directory.identity.device}:{directory.identity.inode}".encode()
        ).hexdigest()


def key_id(public_key):
    if not isinstance(public_key, str) or not _HEX.fullmatch(public_key):
        raise ClientIdentityError("client_public_key_invalid")
    return hashlib.sha256(bytes.fromhex(public_key)).hexdigest()


@dataclass(frozen=True)
class ClientIdentity:
    private_key: Ed25519PrivateKey

    @classmethod
    def generate(cls):
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load(cls, path):
        path = Path(path)
        with Directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent.fd)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                        or info.st_size != 32):
                    raise ClientIdentityError("client_key_unsafe")
                raw = os.read(fd, 33)
                linked = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns) != (
                        linked.st_dev, linked.st_ino, linked.st_mtime_ns, linked.st_ctime_ns):
                    raise ClientIdentityError("client_key_changed")
                parent.check()
                return cls(Ed25519PrivateKey.from_private_bytes(raw))
            finally:
                os.close(fd)

    @classmethod
    def for_profile(cls, profile, *, create=False):
        explicit = os.environ.get("JARVIS_RPC_CLIENT_KEY_FILE")
        if explicit:
            return cls.load(Path(explicit))
        directory = client_directory(profile)
        if create:
            from jarvis_core.state import ensure_private_directory
            ensure_private_directory(directory)
            candidate = cls.generate()
            with Directory(directory) as parent:
                try:
                    fd = os.open("desktop.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW, 0o600, dir_fd=parent.fd)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, "wb") as output:
                        output.write(candidate.private_key.private_bytes_raw())
                        output.flush()
                        os.fsync(output.fileno())
                    os.fsync(parent.fd)
        return cls.load(directory / "desktop.key")

    @property
    def public_key(self):
        return self.private_key.public_key().public_bytes_raw().hex()

    @property
    def client_id(self):
        return key_id(self.public_key)

    def sign(self, challenge, sequence, body):
        return self.private_key.sign(signing_bytes(challenge, sequence, body)).hex()


def signing_bytes(challenge, sequence, body):
    if type(sequence) is not int or not 1 <= sequence <= 2**53 - 1:
        raise ClientIdentityError("client_sequence_invalid")
    fields = ["jarvis-client-signature/1", challenge["profile_binding"],
              challenge["server_id"], challenge["nonce"], challenge["client_id"],
              str(sequence), hashlib.sha256(body).hexdigest()]
    return "\n".join(fields).encode("ascii")


class ClientAuthority:
    """Public-key admission with CAS revocation; private keys are never read.

    Only an explicit process-launch public pin may establish the first owner.
    Subsequent clients need a fresh exact owner review. A revoked launch pin
    cannot silently bootstrap itself again, even following a service restart.
    """

    def __init__(self, profile, *, bootstrap_public_key=None, clock=time.monotonic):
        self.profile = Path(profile)
        self.binding = profile_binding(profile)
        self.clock = clock
        self.server_id = secrets.token_hex(32)
        self._reviews = {}
        self.on_revoke = None
        self.document = PrivateJsonDocument(
            client_directory(profile), "clients.json",
            initial=lambda: {"version": 1, "profile_binding": self.binding,
                             "revision": 0, "clients": []}, validate=self._validate,
            max_bytes=65536)
        current = self.document.read()
        if bootstrap_public_key is not None:
            identity = key_id(bootstrap_public_key)
            if not current["clients"]:
                def initialize(value):
                    if value["clients"] or value["revision"] != 0:
                        raise ClientIdentityError("client_bootstrap_changed")
                    value["clients"] = [{"client_id": identity,
                                          "public_key": bootstrap_public_key,
                                          "scope": "owner", "revoked": False}]
                    value["revision"] = 1
                    return value
                self.document.update(initialize)
            else:
                self.lookup(identity, scope="owner")

    def _validate(self, value):
        if (not isinstance(value, dict)
                or set(value) != {"version", "profile_binding", "revision", "clients"}
                or value["version"] != 1 or value["profile_binding"] != self.binding
                or type(value["revision"]) is not int or value["revision"] < 0
                or not isinstance(value["clients"], list) or len(value["clients"]) > 32):
            raise PrivateJsonError("client_registry_invalid")
        identities = set()
        for row in value["clients"]:
            if (not isinstance(row, dict)
                    or set(row) != {"client_id", "public_key", "scope", "revoked"}
                    or key_id(row["public_key"]) != row["client_id"]
                    or row["client_id"] in identities or row["scope"] not in {"owner", "read"}
                    or type(row["revoked"]) is not bool):
                raise PrivateJsonError("client_registry_invalid")
            identities.add(row["client_id"])

    def lookup(self, identity, *, scope=None):
        if profile_binding(self.profile) != self.binding:
            raise ClientIdentityError("client_profile_changed")
        row = next((row for row in self.document.read()["clients"]
                    if row["client_id"] == identity and not row["revoked"]), None)
        if row is None or (scope is not None and row["scope"] != scope):
            raise ClientIdentityError("client_not_admitted")
        return row

    def challenge(self, identity):
        self.lookup(identity)
        return {"client_id": identity, "profile_binding": self.binding,
                "server_id": self.server_id, "nonce": secrets.token_hex(32)}

    def verify(self, challenge, sequence, body, signature):
        row = self.lookup(challenge["client_id"])
        if not isinstance(signature, str) or re.fullmatch(r"[a-f0-9]{128}", signature) is None:
            raise ClientIdentityError("client_signature_invalid")
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(row["public_key"])).verify(
                bytes.fromhex(signature), signing_bytes(challenge, sequence, body))
        except (ValueError, InvalidSignature):
            raise ClientIdentityError("client_signature_invalid") from None
        return row

    def status(self, identity):
        self.lookup(identity, scope="owner")
        value = self.document.read()
        return {"profile_binding": self.binding, "server_id": self.server_id,
                "client_id": identity, "revision": value["revision"],
                "clients": [{key: row[key] for key in ("client_id", "scope", "revoked")}
                            for row in value["clients"]]}

    def review(self, identity, params):
        self.lookup(identity, scope="owner")
        if (set(params) != {"public_key", "scope", "expected_revision"}
                or params.get("scope") not in {"owner", "read"}
                or type(params.get("expected_revision")) is not int):
            raise ClientIdentityError("client_review_invalid")
        client_id = key_id(params["public_key"])
        current = self.document.read()
        if current["revision"] != params["expected_revision"]:
            raise ClientIdentityError("client_revision_changed")
        if any(row["client_id"] == client_id for row in current["clients"]):
            raise ClientIdentityError("client_already_registered")
        if len(current["clients"]) >= 32:
            raise ClientIdentityError("client_registry_full")
        self._reviews = {key: value for key, value in self._reviews.items()
                         if value["expires"] > self.clock()}
        if len(self._reviews) >= 16:
            raise ClientIdentityError("client_review_limit")
        review_id = secrets.token_hex(16)
        digest = hashlib.sha256((identity + self.binding + client_id + params["scope"]
                                 + str(current["revision"]) + review_id).encode()).hexdigest()
        self._reviews[review_id] = {"owner": identity, "params": dict(params),
                                    "digest": digest, "expires": self.clock() + 120}
        return {"review_id": review_id, "review_digest": digest,
                "client_id": client_id, "scope": params["scope"],
                "expected_revision": current["revision"], "expires_in_seconds": 120}

    def admit(self, identity, params):
        self.lookup(identity, scope="owner")
        if set(params) != {"review_id", "review_digest"}:
            raise ClientIdentityError("client_review_invalid")
        review = self._reviews.pop(params["review_id"], None)
        if (review is None or review["owner"] != identity
                or review["digest"] != params["review_digest"]
                or review["expires"] <= self.clock()):
            raise ClientIdentityError("client_review_expired_or_changed")
        details = review["params"]
        def update(value):
            if value["revision"] != details["expected_revision"]:
                raise ClientIdentityError("client_revision_changed")
            value["clients"].append({"client_id": key_id(details["public_key"]),
                                     "public_key": details["public_key"],
                                     "scope": details["scope"], "revoked": False})
            value["revision"] += 1
            return value
        self.document.update(update)
        return self.status(identity)

    def revoke(self, identity, params):
        self.lookup(identity, scope="owner")
        if (set(params) != {"client_id", "expected_revision"}
                or not isinstance(params.get("client_id"), str)
                or type(params.get("expected_revision")) is not int):
            raise ClientIdentityError("client_revocation_invalid")
        def update(value):
            if value["revision"] != params["expected_revision"]:
                raise ClientIdentityError("client_revision_changed")
            row = next((row for row in value["clients"]
                        if row["client_id"] == params["client_id"] and not row["revoked"]), None)
            if row is None:
                raise ClientIdentityError("client_not_admitted")
            if row["scope"] == "owner" and sum(
                    item["scope"] == "owner" and not item["revoked"]
                    for item in value["clients"]) <= 1:
                raise ClientIdentityError("client_last_owner")
            row["revoked"] = True
            value["revision"] += 1
            return value
        self.document.update(update)
        if self.on_revoke is not None:
            self.on_revoke(params["client_id"])
        return {"revoked": True, "revision": self.document.read()["revision"]}


def decode_signed_body(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise ClientIdentityError("client_body_invalid")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise ClientIdentityError("client_body_invalid") from None
