"""GitHub service contracts with fresh synthetic credentials and finite peers."""

import asyncio
import json

import pytest

from jarvis_core.product_projects import ProjectError
from jarvis_core.product_projects_github import (
    GitHubCredentialService,
    GitHubProjects,
    GitHubPublicationsStore,
    GitHubResponse,
)


class GitHubPeer:
    def __init__(self):
        self.head = "a" * 40
        self.base = "b" * 40
        self.repository_id = 42
        self.calls = []
        self.fail_post = False
        self.unauthorized = False

    async def request(self, method, path, headers, body):
        assert headers["Authorization"] == "Bearer jarvis-synthetic-only"
        assert headers["X-GitHub-Api-Version"] == "2026-03-10"
        self.calls.append((method, path, body))
        if self.unauthorized:
            return GitHubResponse(401, b"{}")
        if method == "POST":
            assert path == "/repos/jarvis/prueba/pulls"
            if self.fail_post:
                raise TimeoutError("Respuesta perdida tras despacho sintético")
            value = {"number": 7, "html_url": "https://github.com/jarvis/prueba/pull/7",
                     "head": {"sha": self.head},
                     "base": {"repo": {"id": self.repository_id}}}
        elif path == "/user":
            value = {"id": 23, "login": "jarvis-ci"}
        elif path == "/repos/jarvis/prueba":
            value = {"id": self.repository_id, "full_name": "jarvis/prueba"}
        elif path.endswith("/git/ref/heads/cambio"):
            value = {"ref": "refs/heads/cambio", "object": {"sha": self.head}}
        elif path.endswith("/git/ref/heads/main"):
            value = {"ref": "refs/heads/main", "object": {"sha": self.base}}
        elif path.endswith("/pulls/7"):
            value = {"number": 7, "head": {"sha": self.head}, "changed_files": 1,
                     "base": {"repo": {"full_name": "jarvis/prueba"}}}
        elif "/check-runs?" in path:
            value = {"total_count": 1, "check_runs": [{"id": 9, "conclusion": "success"}]}
        elif "/pulls/7/files?" in path:
            value = [{"filename": "nota.txt", "patch": "@@ -1 +1 @@\n-uno\n+dos"}]
        else:
            raise AssertionError("Ruta fuera del contrato elegido: " + path)
        return GitHubResponse(201 if method == "POST" else 200, json.dumps(value).encode())


def arguments():
    return {"owner": "jarvis", "repository": "prueba", "head": "cambio", "base": "main",
            "title": "Cambio revisado", "body": "Corpus sintético", "expected_head_sha": "a" * 40}


def api(tmp_path=None):
    service = GitHubCredentialService()
    service.install("jarvis-synthetic-only")
    peer = GitHubPeer()
    store = GitHubPublicationsStore(tmp_path.resolve() / "private" if tmp_path else None)
    return GitHubProjects(service, store, transport=peer), peer, service


def test_credentials_are_volatile_revocable_and_never_exposed_in_repr():
    now = [100.0]
    service = GitHubCredentialService(clock=lambda: now[0])
    assert not service.status()["configured"]
    service.install("jarvis-synthetic-only", ttl=30)
    credential = service.current()
    assert "jarvis-synthetic-only" not in repr(credential)
    assert "token" not in service.status()
    now[0] += 31
    with pytest.raises(ProjectError, match="credential_required"):
        service.current()
    service.install("otra-sintetica")
    with pytest.raises(ProjectError, match="credential_changed"):
        service.assert_current(credential)
    service.revoke()
    assert not service.status()["configured"]


@pytest.mark.parametrize("token", ["", "bad\r\nHeader: value", "bad token", "á", "\0"])
def test_invalid_credential_never_installs(token):
    service = GitHubCredentialService()
    with pytest.raises(ProjectError):
        service.install(token)
    assert not service.status()["configured"]


@pytest.mark.asyncio
async def test_identity_checks_and_diff_remain_explicit_peer_data():
    adapter, peer, _ = api()
    assert (await adapter.identity())["login"] == "jarvis-ci"
    review = await adapter.pull_request("jarvis", "prueba", 7)
    assert review["head_sha"] == peer.head
    assert review["files_complete"] and review["checks_complete"]
    assert review["content_trust"] == "github_data"
    assert not any(method == "POST" for method, _, _ in peer.calls)


@pytest.mark.asyncio
async def test_publication_revalidates_and_persists_bound_receipt_without_token(tmp_path):
    adapter, peer, _ = api(tmp_path)
    review = await adapter.review_publication(arguments(), session_id="sesion", run_id="run")
    assert not any(method == "POST" for method, _, _ in peer.calls)
    calls = []
    receipt = await adapter.publish(review["review_id"], review["digest"], session_id="sesion",
                                    run_id="run", authorize=lambda: calls.append("authority"))
    assert receipt["verified"] and receipt["number"] == 7
    assert len(calls) >= 3
    assert len([method for method, _, _ in peer.calls if method == "POST"]) == 1
    path = adapter.publications.document.path
    assert b"jarvis-synthetic-only" not in path.read_bytes()
    reopened = GitHubPublicationsStore(path.parent)
    assert reopened.get(review["review_id"])["phase"] == "completed"
    with pytest.raises(ProjectError, match="review_changed"):
        await adapter.publish(review["review_id"], review["digest"], session_id="sesion",
                              run_id="run", authorize=lambda: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["head", "base", "repository", "credential"])
async def test_changed_remote_identity_ref_or_credential_blocks_post(change):
    adapter, peer, service = api()
    review = await adapter.review_publication(arguments(), session_id="sesion", run_id="run")
    if change == "head":
        peer.head = "c" * 40
    elif change == "base":
        peer.base = "c" * 40
    elif change == "repository":
        peer.repository_id = 99
    else:
        service.install("jarvis-synthetic-only")
    with pytest.raises(ProjectError, match="review_changed"):
        await adapter.publish(review["review_id"], review["digest"], session_id="sesion",
                              run_id="run", authorize=lambda: None)
    assert not any(method == "POST" for method, _, _ in peer.calls)


@pytest.mark.asyncio
async def test_cancel_and_other_scope_do_not_publish():
    adapter, peer, _ = api()
    review = await adapter.review_publication(arguments(), session_id="sesion", run_id="run")
    with pytest.raises(ProjectError, match="review_changed"):
        await adapter.publish(review["review_id"], review["digest"], session_id="otra",
                              run_id="run", authorize=lambda: None)
    cancellation = asyncio.Event()
    cancellation.set()
    with pytest.raises(ProjectError, match="publication_cancelled"):
        await adapter.publish(review["review_id"], review["digest"], session_id="sesion",
                              run_id="run", authorize=lambda: None, cancellation=cancellation)
    assert not any(method == "POST" for method, _, _ in peer.calls)


@pytest.mark.asyncio
async def test_timeout_after_dispatch_retains_unknown_and_blocks_new_review(tmp_path):
    adapter, peer, _ = api(tmp_path)
    review = await adapter.review_publication(arguments(), session_id="sesion", run_id="run")
    peer.fail_post = True
    receipt = await adapter.publish(review["review_id"], review["digest"], session_id="sesion",
                                    run_id="run", authorize=lambda: None)
    assert receipt["effect_status"] == "unknown" and not receipt["succeeded"]
    reopened = GitHubPublicationsStore(adapter.publications.document.directory)
    assert reopened.get(review["review_id"])["receipt"]["effect_status"] == "unknown"
    with pytest.raises(ProjectError, match="previous_publication_unconfirmed"):
        await adapter.review_publication(arguments(), session_id="sesion", run_id="run2")
    assert len([method for method, _, _ in peer.calls if method == "POST"]) == 1


@pytest.mark.asyncio
async def test_unauthorized_response_revokes_ram_credential():
    adapter, peer, service = api()
    peer.unauthorized = True
    with pytest.raises(ProjectError, match="credential_revoked"):
        await adapter.identity()
    assert not service.status()["configured"]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,repository", [("../owner", "repo"), ("owner", "../repo"),
                                              ("owner@other", "repo"), ("owner", "repo?x")])
async def test_invalid_destination_never_reaches_transport(owner, repository):
    adapter, peer, _ = api()
    args = arguments()
    args.update(owner=owner, repository=repository)
    with pytest.raises(ProjectError, match="repository_invalid"):
        await adapter.review_publication(args, session_id="sesion", run_id="run")
    assert not peer.calls


class RepositoryPeer:
    """Finite official-API responses; any write or PR dependency is an error."""

    def __init__(self):
        self.calls = []
        self.head = "a" * 40
        self.branch = "main"
        self.change = None
        self.repo_reads = self.ref_reads = 0
        self.files = [{"filename": "nota.txt", "status": "modified", "additions": 1,
                       "deletions": 1, "changes": 2, "patch": "@@ -1 +1 @@\n-uno\n+dos"}]
        self.checks = {"total_count": 1, "check_runs": [{"id": 9, "name": "CI sintética",
                        "status": "completed", "conclusion": "success", "head_sha": self.head}]}

    async def request(self, method, path, headers, body):
        assert method == "GET" and body == b""
        assert headers["Authorization"] == "Bearer jarvis-synthetic-only"
        assert headers["X-GitHub-Api-Version"] == "2026-03-10"
        self.calls.append((method, path))
        if path == "/repos/jarvis/prueba":
            self.repo_reads += 1
            value = {"id": 42, "full_name": "jarvis/prueba", "default_branch": "main"}
            if self.repo_reads == 2:
                if self.change == "repository_id":
                    value["id"] = 99
                elif self.change == "repository_name":
                    value["full_name"] = "otro/prueba"
                elif self.change == "default_branch":
                    value["default_branch"] = "otra"
        elif path == "/user":
            value = {"id": 23, "login": "jarvis-ci"}
        elif path == "/repos/jarvis/prueba/git/ref/heads/" + self.branch:
            self.ref_reads += 1
            value = {"ref": "refs/heads/" + self.branch,
                     "object": {"type": "commit", "sha": self.head}}
            if self.change == "head" and self.ref_reads == 2:
                value["object"]["sha"] = "b" * 40
            if self.change == "wrong_ref":
                value["ref"] += "-other"
        elif path.startswith("/repos/jarvis/prueba/commits/" + self.head + "?"):
            value = {"sha": self.head, "commit": {"message": "Cambio sintético\n\nDetalle"},
                     "files": self.files}
            if self.change == "commit_sha":
                value["sha"] = "b" * 40
        elif path == "/repos/jarvis/prueba/commits/" + self.head + "/check-runs?per_page=100":
            if self.change == "revoked":
                return GitHubResponse(401, b"{}")
            value = self.checks
        else:
            raise AssertionError("Lectura fuera del repositorio/ref elegidos: " + path)
        return GitHubResponse(200, json.dumps(value, ensure_ascii=False).encode())


def repository_api(tmp_path=None):
    credentials = GitHubCredentialService()
    credentials.install("jarvis-synthetic-only")
    peer = RepositoryPeer()
    publications = GitHubPublicationsStore(tmp_path.resolve() / "private" if tmp_path else None)
    return GitHubProjects(credentials, publications, transport=peer), peer, credentials


@pytest.mark.asyncio
async def test_repository_review_default_branch_needs_no_pr_or_publication(tmp_path):
    adapter, peer, _ = repository_api(tmp_path)
    value = await adapter.repository_review("jarvis", "prueba")
    assert value["repository_id"] == 42 and value["identity"]["id"] == 23
    assert value["ref"] == {"kind": "branch", "name": "refs/heads/main"}
    assert value["default_branch"] == "main" and value["head_sha"] == peer.head
    assert value["files"][0]["patch"].endswith("+dos")
    assert value["files_complete"] and value["patches_complete"] and value["checks_complete"]
    assert value["read_only"] and not value["verified"]
    assert value["content_trust"] == "github_data"
    assert value["reference_rechecked"] and value["repository_identity_rechecked"]
    assert len(value["review_digest"]) == 64
    assert peer.ref_reads == peer.repo_reads == 2
    assert not any("/pulls" in path for _, path in peer.calls)
    assert not adapter.publications.document.path.exists() and not adapter._reviews
    assert not adapter.blockers


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["feature/selected", "heads/feature/selected",
                                  "refs/heads/feature/selected"])
async def test_repository_review_uses_one_exact_selected_branch(ref):
    adapter, peer, _ = repository_api()
    peer.branch = "feature/selected"
    value = await adapter.repository_review("jarvis", "prueba", ref)
    assert value["ref"] == {"kind": "branch", "name": "refs/heads/feature/selected"}
    assert peer.ref_reads == 2
    assert not any("/matching-refs/" in path for _, path in peer.calls)


@pytest.mark.asyncio
async def test_repository_review_immutable_commit_is_not_a_branch_lookup():
    adapter, peer, _ = repository_api()
    value = await adapter.repository_review("jarvis", "prueba", peer.head)
    assert value["ref"] == {"kind": "commit", "name": peer.head}
    assert peer.ref_reads == 0 and peer.repo_reads == 2
    assert sum("/commits/" + peer.head + "?" in path for _, path in peer.calls) == 2
    assert not any("/git/ref/" in path for _, path in peer.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["head", "wrong_ref", "repository_id", "repository_name",
                                     "default_branch", "commit_sha"])
async def test_repository_review_rejects_concurrent_or_wrong_identity_without_writing(change):
    adapter, peer, _ = repository_api()
    peer.change = change
    with pytest.raises(ProjectError, match=r"(review_changed|destination_changed|commit_invalid)"):
        await adapter.repository_review("jarvis", "prueba")
    assert all(method == "GET" for method, _ in peer.calls)
    assert not adapter.publications.unconfirmed()


@pytest.mark.asyncio
async def test_repository_review_revoked_credential_stops_before_final_reread():
    adapter, peer, credentials = repository_api()
    peer.change = "revoked"
    with pytest.raises(ProjectError, match="credential_revoked"):
        await adapter.repository_review("jarvis", "prueba")
    assert not credentials.status()["configured"]
    assert peer.ref_reads == peer.repo_reads == 1


@pytest.mark.asyncio
async def test_repository_review_marks_patch_and_check_pagination_limits_explicitly():
    adapter, peer, _ = repository_api()
    peer.files[0].update(patch="+x\n" * 5000, additions=5000, deletions=0, changes=5000)
    peer.checks["total_count"] = 101
    value = await adapter.repository_review("jarvis", "prueba")
    assert value["files_complete"]
    assert value["files"][0]["patch_truncated"] and not value["patches_complete"]
    assert len(value["files"][0]["patch"].encode()) <= 8192
    assert value["view_truncated"] and not value["checks_complete"]
    assert value["pagination"]["automatic_follow"] is False
    assert not any("page=2" in path for _, path in peer.calls)


@pytest.mark.asyncio
async def test_repository_review_binary_or_missing_patch_is_not_a_complete_diff():
    adapter, peer, _ = repository_api()
    peer.files[0].pop("patch")
    value = await adapter.repository_review("jarvis", "prueba")
    assert value["files_complete"] and not value["patches_complete"]
    assert value["files"][0]["patch"] is None
    assert not value["files"][0]["patch_available"]


@pytest.mark.asyncio
async def test_repository_review_caps_escaped_output_without_claiming_complete_files():
    adapter, peer, _ = repository_api()
    peer.files = [{"filename": "á" * 990 + str(index), "status": "added", "additions": 1,
                   "deletions": 0, "changes": 1, "patch": "+x"} for index in range(100)]
    value = await adapter.repository_review("jarvis", "prueba")
    assert len(value["files"]) < 100 and value["files_api_count"] == 100
    assert not value["files_complete"] and not value["patches_complete"]
    assert value["view_truncated"]
    assert len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()) <= 196608


@pytest.mark.asyncio
async def test_repository_review_checks_from_another_commit_are_rejected():
    adapter, peer, _ = repository_api()
    peer.checks["check_runs"][0]["head_sha"] = "b" * 40
    with pytest.raises(ProjectError, match="checks_invalid"):
        await adapter.repository_review("jarvis", "prueba")


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["../owner", "main?other=1", "refs/tags/v1", "tags/v1", 4, {}])
async def test_repository_review_invalid_ref_never_reaches_transport(ref):
    adapter, peer, _ = repository_api()
    with pytest.raises(ProjectError):
        await adapter.repository_review("jarvis", "prueba", ref)
    assert not peer.calls
