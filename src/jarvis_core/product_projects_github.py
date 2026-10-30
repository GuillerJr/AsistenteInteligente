"""Explicit GitHub API connection with new RAM credentials and reviewed PRs.

No gh, Git remotes, environment credentials, owner profiles or token files are
read. The owner supplies a fresh token through the host's service. Transport is
injectable; the default connects only to api.github.com with TLS, no redirects,
one bounded attempt and bounded HTTP frames. Reviews never grant authority.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .private_json import PrivateJsonDocument
from .product_projects import ProjectError, _hash, _id, _json, _text
from .web import tls_context


@dataclass(frozen=True)
class GitHubCredential:
    credential_id: str
    revision: int
    expires: float
    token: str = field(repr=False)


class GitHubCredentialService:
    """Host-owned volatile credential; installation is an explicit owner action."""

    def __init__(self, *, clock=time.monotonic):
        self.clock, self._credential, self._revision = clock, None, 0

    def install(self, token, *, ttl=900):
        _text(token, 4096)
        if (not token.isascii() or any(char.isspace() for char in token)
                or type(ttl) not in {int, float} or not 30 <= ttl <= 3600):
            raise ProjectError("github_credential_invalid")
        self._revision += 1
        self._credential = GitHubCredential(uuid4().hex, self._revision, self.clock() + ttl, token)
        return self.status()

    def revoke(self):
        self._credential = None
        self._revision += 1

    def status(self):
        credential = self._credential
        return {"configured": credential is not None and credential.expires > self.clock(),
                "credential_id": credential.credential_id if credential else None,
                "revision": self._revision, "storage": "memory_only"}

    def current(self):
        if self._credential is None or self._credential.expires <= self.clock():
            raise ProjectError("github_credential_required")
        return self._credential

    def assert_current(self, expected):
        current = self.current()
        if current.credential_id != expected.credential_id or current.revision != expected.revision:
            raise ProjectError("github_credential_changed")


@dataclass(frozen=True)
class GitHubResponse:
    status: int
    body: bytes


class GitHubHTTPS:
    """Minimal finite HTTP/1.1 client for the single official API origin."""

    async def request(self, method, path, headers, body):
        if (method not in {"GET", "POST"} or not path.startswith("/")
                or "\r" in path or "\n" in path):
            raise ProjectError("github_request_invalid")
        writer = None
        try:
            async with asyncio.timeout(15):
                reader, writer = await asyncio.open_connection(
                    "api.github.com", 443, ssl=tls_context(),
                    server_hostname="api.github.com", limit=16384)
                fields = {"Host": "api.github.com", "Connection": "close",
                          "Content-Length": str(len(body)), **headers}
                wire = [f"{method} {path} HTTP/1.1\r\n"]
                for key, value in fields.items():
                    if any(char in key + value for char in "\r\n"):
                        raise ProjectError("github_request_invalid")
                    wire.append(f"{key}: {value}\r\n")
                writer.write("".join(wire).encode("ascii") + b"\r\n" + body)
                await writer.drain()
                status_line = await reader.readline()
                match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3}) [^\r\n]*\r\n", status_line)
                if match is None:
                    raise ProjectError("github_response_invalid")
                response_headers, header_size = {}, len(status_line)
                while True:
                    line = await reader.readline()
                    header_size += len(line)
                    if header_size > 16384 or not line:
                        raise ProjectError("github_response_limit")
                    if line == b"\r\n":
                        break
                    try:
                        key, value = line.decode("ascii").strip().split(":", 1)
                    except (ValueError, UnicodeError):
                        raise ProjectError("github_response_invalid") from None
                    key = key.lower()
                    if key in response_headers:
                        raise ProjectError("github_response_invalid")
                    response_headers[key] = value.strip()
                payload = bytearray()
                if response_headers.get("transfer-encoding", "").lower() == "chunked":
                    while True:
                        line = await reader.readline()
                        if len(line) > 128:
                            raise ProjectError("github_response_invalid")
                        try:
                            size = int(line.strip().split(b";", 1)[0], 16)
                        except ValueError:
                            raise ProjectError("github_response_invalid") from None
                        if size < 0 or len(payload) + size > 262144:
                            raise ProjectError("github_response_limit")
                        if size == 0:
                            break
                        payload.extend(await reader.readexactly(size))
                        if await reader.readexactly(2) != b"\r\n":
                            raise ProjectError("github_response_invalid")
                else:
                    length = response_headers.get("content-length")
                    if length is None or not length.isdigit() or int(length) > 262144:
                        raise ProjectError("github_response_limit")
                    payload.extend(await reader.readexactly(int(length)))
                if response_headers.get("content-encoding", "identity") != "identity":
                    raise ProjectError("github_response_invalid")
                return GitHubResponse(int(match[1]), bytes(payload))
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError):
            raise ProjectError("github_transport_failed") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(2):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass


def _publications_initial():
    return {"version": 1, "operations": {}}


def _publications_validate(value):
    if (not isinstance(value, dict) or set(value) != {"version", "operations"}
            or value["version"] != 1 or not isinstance(value["operations"], dict)
            or len(value["operations"]) > 256):
        raise ProjectError("github_registry_invalid")
    for operation_id, row in value["operations"].items():
        _id(operation_id)
        if (not isinstance(row, dict) or set(row) != {"digest", "phase", "receipt"}
                or row["phase"] not in {"dispatched", "completed", "unknown"}
                or not isinstance(row["digest"], str)
                or re.fullmatch(r"[a-f0-9]{64}", row["digest"]) is None
                or not isinstance(row["receipt"], dict)
                or len(_json(row["receipt"]).encode()) > 8192):
            raise ProjectError("github_registry_invalid")


class GitHubPublicationsStore:
    def __init__(self, directory: Path | None = None):
        self.document = None if directory is None else PrivateJsonDocument(
            directory, "github_publications.json", initial=_publications_initial,
            validate=_publications_validate, max_bytes=2097152)
        self._memory = _publications_initial()

    def get(self, operation_id):
        value = self.document.read() if self.document else self._memory
        row = value["operations"].get(_id(operation_id))
        return json.loads(_json(row)) if row is not None else None

    def unconfirmed(self):
        value = self.document.read() if self.document else self._memory
        return tuple({"operation_id": identity, **json.loads(_json(row))}
                     for identity, row in value["operations"].items()
                     if row["phase"] != "completed")

    def record(self, operation_id, digest, phase, receipt, *, first=False):
        def change(value):
            if first and operation_id in value["operations"]:
                raise ProjectError("github_publication_already_dispatched")
            value["operations"][operation_id] = {"digest": digest, "phase": phase,
                                                  "receipt": receipt}
            _publications_validate(value)
            return value
        if self.document:
            self.document.update(change)
        else:
            self._memory = change(json.loads(_json(self._memory)))


def _repository(owner, repository):
    if (not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner)
            or not isinstance(repository, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", repository)
            or repository in {".", ".."}):
        raise ProjectError("github_repository_invalid")
    return "/repos/" + owner + "/" + repository


def _branch(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", value)
            or ".." in value or "//" in value or value.endswith(("/", ".", ".lock"))
            or any(part.startswith(".") for part in value.split("/"))):
        raise ProjectError("github_branch_invalid")
    return value


class GitHubProjects:
    """Identity, PR/check/diff inspection and one exact reviewed PR creation.

    Publication uses an existing remote head; it never pushes, creates commits,
    merges or retries a request. A transport failure after dispatch is unknown.
    ``authorize`` is the same central host-authority callback as local projects.
    """

    def __init__(self, credentials: GitHubCredentialService, publications=None, *,
                 transport=None, clock=time.monotonic):
        self.credentials = credentials
        self.publications = publications or GitHubPublicationsStore()
        self.transport, self.clock = transport or GitHubHTTPS(), clock
        self._reviews, self._lock = {}, asyncio.Lock()
        self._active_requests = 0

    @property
    def blockers(self):
        result = ["github_publication_unconfirmed"] if self.publications.unconfirmed() else []
        if self._active_requests:
            result.append("github_request_active")
        return tuple(result)

    async def aclose(self):
        self.credentials.revoke()

    async def _request(self, method, path, credential, body=None, authorize=None):
        self.credentials.assert_current(credential)
        payload = b"" if body is None else _json(body).encode()
        if len(payload) > 65536:
            raise ProjectError("github_request_limit")
        headers = {"Authorization": "Bearer " + credential.token,
                   "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                   "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "Jarvis"}
        if authorize is not None:
            authorize()
            self.credentials.assert_current(credential)
        self._active_requests += 1
        try:
            async with asyncio.timeout(18):
                result = await self.transport.request(method, path, headers, payload)
        finally:
            self._active_requests -= 1
        self.credentials.assert_current(credential)
        if result.status == 401:
            self.credentials.revoke()
            raise ProjectError("github_credential_revoked")
        if result.status not in {200, 201}:
            raise ProjectError("github_request_rejected")
        if not isinstance(result.body, bytes) or len(result.body) > 262144:
            raise ProjectError("github_response_limit")
        try:
            return json.loads(result.body)
        except (ValueError, UnicodeError, RecursionError):
            raise ProjectError("github_response_invalid") from None

    async def identity(self):
        value = await self._request("GET", "/user", self.credentials.current())
        if (not isinstance(value, dict) or type(value.get("id")) is not int
                or not isinstance(value.get("login"), str)):
            raise ProjectError("github_identity_invalid")
        return {"id": value["id"], "login": _text(value["login"], 128),
                "service": "github", "credential_storage": "memory_only"}

    async def pull_request(self, owner, repository, number):
        prefix = _repository(owner, repository)
        if type(number) is not int or not 1 <= number < 2**31:
            raise ProjectError("github_pull_invalid")
        credential = self.credentials.current()
        row = await self._request("GET", prefix + f"/pulls/{number}", credential)
        sha = row.get("head", {}).get("sha") if isinstance(row, dict) else None
        if (not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha)
                or row.get("number") != number
                or row.get("base", {}).get("repo", {}).get("full_name", "").casefold()
                != (owner + "/" + repository).casefold()):
            raise ProjectError("github_pull_invalid")
        checks = await self._request("GET", prefix + f"/commits/{sha}/check-runs?per_page=100",
                                     credential)
        files = await self._request(
            "GET", prefix + f"/pulls/{number}/files?per_page=100", credential)
        after = await self._request("GET", prefix + f"/pulls/{number}", credential)
        if (not isinstance(after, dict) or after.get("head", {}).get("sha") != sha
                or not isinstance(files, list) or len(files) > 100
                or not isinstance(checks, dict) or not isinstance(checks.get("check_runs"), list)):
            raise ProjectError("github_review_changed")
        # All GitHub bodies, patches and check names remain untrusted peer data.
        return {"owner": owner, "repository": repository, "number": number, "head_sha": sha,
                "pull_request": row, "checks": checks, "files": files,
                "files_complete": type(row.get("changed_files")) is int
                and row["changed_files"] <= len(files),
                "checks_complete": checks.get("total_count") == len(checks["check_runs"]),
                "content_trust": "github_data"}

    async def _publication_state(self, args, credential):
        prefix = _repository(args["owner"], args["repository"])
        repo = await self._request("GET", prefix, credential)
        identity = await self._request("GET", "/user", credential)
        head = await self._request("GET", prefix + "/git/ref/heads/" + quote(args["head"], safe=""),
                                   credential)
        base = await self._request("GET", prefix + "/git/ref/heads/" + quote(args["base"], safe=""),
                                   credential)
        if (not isinstance(repo, dict) or type(repo.get("id")) is not int
                or repo.get("full_name", "").casefold()
                != (args["owner"] + "/" + args["repository"]).casefold()
                or not isinstance(identity, dict) or type(identity.get("id")) is not int
                or not isinstance(identity.get("login"), str)):
            raise ProjectError("github_destination_changed")
        shas = []
        for value, branch in ((head, args["head"]), (base, args["base"])):
            sha = value.get("object", {}).get("sha") if isinstance(value, dict) else None
            if (not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha)
                    or value.get("ref") != "refs/heads/" + branch):
                raise ProjectError("github_destination_changed")
            shas.append(sha)
        return {"repository_id": repo["id"], "account_id": identity["id"],
                "account_login": identity["login"], "head_sha": shas[0], "base_sha": shas[1],
                "credential_id": credential.credential_id,
                "credential_revision": credential.revision}

    async def review_publication(self, arguments, *, session_id, run_id):
        _text(session_id, 128)
        _text(run_id, 128)
        if (not isinstance(arguments, dict) or set(arguments) != {
                "owner", "repository", "head", "base", "title", "body", "expected_head_sha"}):
            raise ProjectError("github_publication_invalid")
        args = json.loads(_json(arguments))
        _repository(args["owner"], args["repository"])
        _branch(args["head"])
        _branch(args["base"])
        _text(args["title"], 256)
        if (not isinstance(args["body"], str) or len(args["body"].encode()) > 16384
                or "\0" in args["body"]):
            raise ProjectError("github_publication_invalid")
        if not isinstance(args["expected_head_sha"], str) or not re.fullmatch(
                r"[a-f0-9]{40}", args["expected_head_sha"]):
            raise ProjectError("github_publication_invalid")
        state = await self._publication_state(args, self.credentials.current())
        if state["head_sha"] != args["expected_head_sha"]:
            raise ProjectError("github_review_changed")
        if any(row["receipt"].get("repository_id") == state["repository_id"]
               and row["receipt"].get("head_sha") == state["head_sha"]
               for row in self.publications.unconfirmed()):
            raise ProjectError("github_previous_publication_unconfirmed")
        self._reviews = {key: row for key, row in self._reviews.items()
                         if row["expires"] > self.clock()}
        if len(self._reviews) >= 32:
            raise ProjectError("github_review_limit")
        row = {"review_id": uuid4().hex, "arguments": args, "state": state,
               "session_id": session_id, "run_id": run_id, "expires": self.clock() + 120}
        row["digest"] = _hash(row)
        self._reviews[row["review_id"]] = row
        return json.loads(_json(row))

    async def publish(self, review_id, digest, *, session_id, run_id, authorize, cancellation=None):
        if not callable(authorize):
            raise ProjectError("github_authority_required")
        async with self._lock:
            row = self._reviews.get(_id(review_id))
            if (row is None or row["digest"] != digest or row["session_id"] != session_id
                    or row["run_id"] != run_id or self.clock() >= row["expires"]):
                raise ProjectError("github_review_changed")
            credential = self.credentials.current()
            args = row["arguments"]
            state = await self._publication_state(args, credential)
            if state != row["state"]:
                raise ProjectError("github_review_changed")
            if any(operation["receipt"].get("repository_id") == state["repository_id"]
                   and operation["receipt"].get("head_sha") == state["head_sha"]
                   for operation in self.publications.unconfirmed()):
                raise ProjectError("github_previous_publication_unconfirmed")

            def guard():
                if (self.clock() >= row["expires"]
                        or (cancellation is not None and cancellation.is_set())):
                    raise ProjectError("github_publication_cancelled")
                authorize()
                self.credentials.assert_current(credential)

            guard()
            recovery = {"operation_id": review_id, "session_id": session_id,
                        "repository_id": state["repository_id"], "head_sha": state["head_sha"],
                        "effect_status": "unknown"}
            self.publications.record(review_id, digest, "dispatched", recovery, first=True)
            self._reviews.pop(review_id)
            try:
                response = await self._request(
                    "POST", _repository(args["owner"], args["repository"]) + "/pulls", credential,
                    {key: args[key] for key in ("head", "base", "title", "body")}, authorize=guard)
                number = response.get("number") if isinstance(response, dict) else None
                expected_url = f"https://github.com/{args['owner']}/{args['repository']}/pull/{number}"
                if (type(number) is not int or number < 1
                        or response.get("html_url") != expected_url
                        or response.get("head", {}).get("sha") != state["head_sha"]
                        or response.get("base", {}).get("repo", {}).get("id")
                        != state["repository_id"]):
                    raise ProjectError("github_publication_unconfirmed")
                receipt = {"operation_id": review_id, "session_id": session_id,
                           "repository_id": state["repository_id"], "head_sha": state["head_sha"],
                           "number": number, "url": expected_url, "effect_status": "known",
                           "succeeded": True, "verified": True}
                guard()
                self.publications.record(review_id, digest, "completed", receipt)
                return receipt
            except (Exception, asyncio.CancelledError):
                return {**recovery, "succeeded": False, "verified": False,
                        "error": "github_publication_unconfirmed"}
