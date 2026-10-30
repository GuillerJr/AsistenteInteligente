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
