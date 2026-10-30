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


def _review_ref(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProjectError("github_ref_invalid")
    if re.fullmatch(r"[a-f0-9]{40}", value):
        return {"kind": "commit", "name": value}
    if value.startswith("refs/heads/"):
        value = value[len("refs/heads/"):]
    elif value.startswith("heads/"):
        value = value[len("heads/"):]
    elif value.startswith(("refs/", "tags/")):
        raise ProjectError("github_ref_invalid")
    return {"kind": "branch", "name": "refs/heads/" + _branch(value)}


def _repository_identity(value, owner, repository):
    if (not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] < 1
            or not isinstance(value.get("full_name"), str)
            or value["full_name"].casefold() != (owner + "/" + repository).casefold()
            or not isinstance(value.get("default_branch"), str)):
        raise ProjectError("github_destination_changed")
    _branch(value["default_branch"])
    return value["id"], value["full_name"].casefold(), value["default_branch"]


def _reference_head(value, reference):
    item = value.get("object") if isinstance(value, dict) else None
    if (not isinstance(item, dict) or item.get("type") != "commit"
            or not isinstance(item.get("sha"), str)
            or not re.fullmatch(r"[a-f0-9]{40}", item["sha"])
            or value.get("ref") != reference):
        raise ProjectError("github_review_changed")
    return item["sha"]


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

    async def repository_review(self, owner, repository, ref=None):
        """Read one branch head or immutable commit, including checks and diff.

        ``None`` selects the repository's default branch. Strings accept a short
        branch, heads/branch, refs/heads/branch, or a full lower-case commit SHA.
        At most the first 100 files/checks are inspected; completeness refers to
        those finite API pages and supplied patches, never a verified code audit.
        References and repository identity are read again before returning. No
        POST, branch creation, push, local Git or publication journal is involved.
        """
        prefix = _repository(owner, repository)
        selection = _review_ref(ref)
        try:
            async with asyncio.timeout(60):
                return await self._repository_review(owner, repository, prefix, selection)
        except TimeoutError:
            raise ProjectError("github_review_timeout") from None

    async def _repository_review(self, owner, repository, prefix, selection):
        credential = self.credentials.current()
        repo = await self._request("GET", prefix, credential)
        repo_identity = _repository_identity(repo, owner, repository)
        account = await self._request("GET", "/user", credential)
        if (not isinstance(account, dict) or type(account.get("id")) is not int
                or account["id"] < 1 or not isinstance(account.get("login"), str)):
            raise ProjectError("github_identity_invalid")
        account_identity = {"id": account["id"], "login": _text(account["login"], 128)}
        selection = selection or {"kind": "branch", "name": "refs/heads/" + repo_identity[2]}
        reference_path = None
        if selection["kind"] == "branch":
            reference_path = prefix + "/git/ref/" + quote(selection["name"][len("refs/"):],
                                                         safe="/")
            head = _reference_head(await self._request("GET", reference_path, credential),
                                   selection["name"])
        else:
            head = selection["name"]
        commit_path = prefix + "/commits/" + head
        commit = await self._request("GET", commit_path + "?per_page=100&page=1", credential)
        metadata = commit.get("commit") if isinstance(commit, dict) else None
        if (not isinstance(metadata, dict) or commit.get("sha") != head
                or not isinstance(metadata.get("message"), str)
                or not isinstance(commit.get("files"), list)):
            raise ProjectError("github_commit_invalid")
        checks = await self._request("GET", prefix + f"/commits/{head}/check-runs?per_page=100",
                                     credential)
        if (not isinstance(checks, dict) or type(checks.get("total_count")) is not int
                or checks["total_count"] < 0 or not isinstance(checks.get("check_runs"), list)):
            raise ProjectError("github_checks_invalid")
        if reference_path is not None:
            after_head = _reference_head(
                await self._request("GET", reference_path, credential), selection["name"])
        else:
            after_commit = await self._request(
                "GET", commit_path + "?per_page=1&page=1", credential)
            after_head = after_commit.get("sha") if isinstance(after_commit, dict) else None
        after_repo = await self._request("GET", prefix, credential)
        if (after_head != head
                or _repository_identity(after_repo, owner, repository) != repo_identity):
            raise ProjectError("github_review_changed")
        self.credentials.assert_current(credential)
        files, patch_budget, patches_complete = [], 65536, True
        for item in commit["files"][:100]:
            if not isinstance(item, dict):
                raise ProjectError("github_commit_invalid")
            filename = _text(item.get("filename"), 2048)
            patch = item.get("patch")
            if patch is not None and not isinstance(patch, str):
                raise ProjectError("github_commit_invalid")
            try:
                raw = b"" if patch is None else patch.encode()
            except UnicodeError:
                raise ProjectError("github_commit_invalid") from None
            selected = raw[:min(8192, patch_budget)].decode("utf-8", errors="ignore")
            patch_budget -= len(selected.encode())
            truncated = patch is not None and selected != patch
            complete = patch is not None and not truncated
            row = {"filename": filename, "patch": selected if patch is not None else None,
                   "patch_available": patch is not None, "patch_truncated": truncated}
            if "status" in item:
                row["status"] = _text(item["status"], 64)
            if "previous_filename" in item:
                row["previous_filename"] = _text(item["previous_filename"], 2048)
            for name in ("additions", "deletions", "changes"):
                count = item.get(name)
                if type(count) is not int or not 0 <= count <= 2**53 - 1:
                    raise ProjectError("github_commit_invalid")
                row[name] = count
            if complete:
                lines = selected.splitlines()
                complete = (sum(line.startswith("+") for line in lines) == row["additions"]
                            and sum(line.startswith("-") for line in lines) == row["deletions"])
            row["patch_complete"] = complete
            patches_complete = patches_complete and complete
            files.append(row)
        selected_checks = []
        for item in checks["check_runs"][:100]:
            if (not isinstance(item, dict) or type(item.get("id")) is not int
                    or item["id"] < 1 or item.get("head_sha") != head):
                raise ProjectError("github_checks_invalid")
            conclusion = item.get("conclusion")
            if conclusion is not None:
                _text(conclusion, 64)
            selected_checks.append({"id": item["id"], "name": _text(item.get("name"), 512),
                                    "status": _text(item.get("status"), 64),
                                    "conclusion": conclusion, "head_sha": head})
        message = metadata["message"]
        try:
            message_raw = message.encode()
        except UnicodeError:
            raise ProjectError("github_commit_invalid") from None
        message = message_raw[:8192].decode("utf-8", errors="ignore")
        result = {"owner": owner, "repository": repository, "repository_id": repo_identity[0],
                  "identity": account_identity, "default_branch": repo_identity[2],
                  "ref": selection, "head_sha": head,
                  "repository_metadata": {"id": repo_identity[0], "full_name": repo["full_name"],
                      "default_branch": repo_identity[2],
                      "html_url": f"https://github.com/{owner}/{repository}"},
                  "commit": {"sha": head, "message": message,
                      "message_truncated": len(message_raw) > 8192,
                      "html_url": f"https://github.com/{owner}/{repository}/commit/{head}"},
                  "checks": {"total_count": checks["total_count"], "check_runs": selected_checks},
                  "files": files, "files_api_count": len(commit["files"]),
                  "files_complete": len(commit["files"]) < 100,
                  "patches_complete": patches_complete and len(commit["files"]) < 100,
                  "checks_complete": checks["total_count"] == len(selected_checks),
                  "view_truncated": len(commit["files"]) >= 100 or len(checks["check_runs"]) > 100
                  or checks["total_count"] != len(selected_checks)
                  or len(message_raw) > 8192 or any(row["patch_truncated"] for row in files),
                  "content_trust": "github_data", "verified": False, "read_only": True,
                  "reference_rechecked": True, "repository_identity_rechecked": True,
                  "pagination": {"page": 1, "per_page": 100, "automatic_follow": False}}
        while len(_json(result).encode()) > 196480:
            result["view_truncated"] = True
            if result["files"]:
                result["files"].pop()
                result["files_complete"] = result["patches_complete"] = False
            elif result["checks"]["check_runs"]:
                result["checks"]["check_runs"].pop()
                result["checks_complete"] = False
            else:
                raise ProjectError("github_response_limit")
        result["review_digest"] = _hash(result)
        return result

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
