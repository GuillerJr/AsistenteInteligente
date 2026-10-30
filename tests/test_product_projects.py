"""Finite project fixtures; real Git repositories are exercised only in CI."""

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from jarvis_core.product_projects import (
    BoundedGitRunner,
    GitOutput,
    ProjectError,
    ProjectLimits,
    ProjectsBackend,
    ProjectsStore,
    RepositoryPin,
)


def repository(tmp_path):
    root = tmp_path.resolve() / "repo"
    root.mkdir(mode=0o700)
    (root / ".git").mkdir(mode=0o700)
    (root / ".git" / "HEAD").write_text("a" * 40 + "\n")
    (root / ".git" / "config").write_text("")
    (root / "nota.txt").write_text("uno\n")
    return root


class ReadRunner:
    def __init__(self):
        self.calls = []
        self.untracked = b""

    async def run(self, pin, arguments, **options):
        self.calls.append(arguments)
        if arguments == ("rev-parse", "--is-inside-work-tree"):
            return GitOutput(0, b"true\n", b"")
        if arguments[:2] == ("rev-parse", "--verify"):
            return GitOutput(0, b"a" * 40 + b"\n", b"")
        oid = hashlib.sha1(b"blob 4\0uno\n").hexdigest().encode()
        if arguments[0] == "ls-tree":
            return GitOutput(0, b"100644 blob " + oid + b"\tnota.txt\0", b"")
        if arguments[:2] == ("cat-file", "blob"):
            return GitOutput(0, b"uno\n", b"")
        if arguments[:2] == ("ls-files", "--stage"):
            return GitOutput(0, b"100644 " + oid + b" 0\tnota.txt\0", b"")
        if arguments[:2] == ("ls-files", "--others"):
            return GitOutput(0, self.untracked, b"")
        raise AssertionError("No se esperaba un efecto Git en este fixture.")


def backend(tmp_path):
    root = repository(tmp_path)
    worktrees = tmp_path.resolve() / "worktrees"
    worktrees.mkdir(mode=0o700)
    store = ProjectsStore(tmp_path.resolve() / "private")
    runner = ReadRunner()
    return root, ProjectsBackend(store, worktrees, runner=runner), runner


def test_registry_persistence_and_temporary_bindings_do_not_copy_paths(tmp_path):
    root = repository(tmp_path)
    store = ProjectsStore(tmp_path.resolve() / "private")
    permanent = store.register("Proyecto", RepositoryPin.capture(root))
    store.bind_session("persistente", permanent["project_id"])
    store.bind_session("temporal", permanent["project_id"], temporary=True)
    second = ProjectsStore(store.document.directory)
    assert second.binding("persistente")["project_id"] == permanent["project_id"]
    assert second.binding("temporal", temporary=True) is None
    assert b"temporal" not in store.document.path.read_bytes()
    assert store.binding("temporal", temporary=True)["revision"] == 1
    with pytest.raises(ProjectError, match="revision_conflict"):
        store.bind_session("persistente", permanent["project_id"], expected_revision=0)


def test_temporary_project_never_enters_disk_registry(tmp_path):
    root = repository(tmp_path)
    store = ProjectsStore(tmp_path.resolve() / "private")
    row = store.register("Efímero", RepositoryPin.capture(root), temporary=True)
    store.bind_session("temporal", row["project_id"], temporary=True)
    assert not store.document.path.exists()
    assert not ProjectsStore(store.document.directory).list()
    with pytest.raises(ProjectError, match="binding_invalid"):
        store.bind_session("durable", row["project_id"])


def test_repository_pin_rejects_symlink_and_replacement(tmp_path):
    root = repository(tmp_path)
    pin = RepositoryPin.capture(root)
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError):
        RepositoryPin.capture(alias)
    root.rename(root.with_name("original"))
    root.mkdir(mode=0o700)
    (root / ".git").mkdir(mode=0o700)
    with pytest.raises(ProjectError, match="repository_changed"):
        pin.assert_current()


@pytest.mark.asyncio
async def test_own_diff_reads_literal_bytes_without_git_filters(tmp_path):
    root, api, runner = backend(tmp_path)
    row = await api.register(root, "Proyecto")
    before = await api.inspect(row["project_id"])
    assert before["status"] == ""
    (root / "nota.txt").write_text("dos\n")
    after = await api.inspect(row["project_id"])
    assert after["state_digest"] != before["state_digest"]
    assert "+dos" in after["diff"] and "-uno" in after["diff"]
    assert after["comparison"] == "literal_bytes_no_filters"
    assert not any(call[0] in {"status", "diff", "checkout"} for call in runner.calls)


@pytest.mark.asyncio
async def test_state_and_diff_pages_share_global_revision(tmp_path):
    root, api, runner = backend(tmp_path)
    row = await api.register(root, "Proyecto")
    (root / "x.txt").write_text("x\n")
    (root / "y.txt").write_text("y\n")
    runner.untracked = b"x.txt\0y.txt\0"
    first = await api.inspect(row["project_id"], limit=1)
    second = await api.inspect(row["project_id"], offset=first["next_offset"], limit=1)
    assert first["change_count"] == second["change_count"] == 2
    assert first["state_digest"] == second["state_digest"]
    assert "x.txt" in first["status"] and "y.txt" not in first["status"]
    assert "y.txt" in second["status"] and second["next_offset"] is None
    assert "b/y.txt" not in first["diff"]


def test_snapshot_blob_roundtrip_keeps_payload_out_of_registry(tmp_path):
    root = repository(tmp_path)
    store = ProjectsStore(tmp_path.resolve() / "private")
    project = store.register("Proyecto", RepositoryPin.capture(root))
    raw = b"custodia sintetica\n"
    files = {"nota.txt": {"base64": base64.b64encode(raw).decode(), "mode": 0o600,
                           "sha256": hashlib.sha256(raw).hexdigest()}}
    metadata = store.save_snapshot_files(files, temporary=False, authorize=lambda: None)
    assert set(metadata["nota.txt"]) == {"sha256", "mode", "bytes"}

    def save(value):
        value["snapshots"]["f" * 32] = {"project_id": project["project_id"], "files": metadata,
            "digest": hashlib.sha256(json.dumps(metadata, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(), "head": "a" * 40}
    store._update(save)
    assert b"base64" not in store.document.path.read_bytes()
    assert raw not in store.document.path.read_bytes()
    reopened = ProjectsStore(store.document.directory)
    assert reopened.load_snapshot_files(metadata, temporary=False) == files
    blob = store.document.directory / "project_blobs" / files["nota.txt"]["sha256"]
    blob.write_bytes(b"alterado")
    with pytest.raises(ProjectError, match="blob_changed"):
        reopened.load_snapshot_files(metadata, temporary=False)


def test_configurable_snapshot_quota_rejects_before_replacing_original(tmp_path):
    root = repository(tmp_path)
    worktrees = tmp_path.resolve() / "worktrees"
    worktrees.mkdir(mode=0o700)
    limits = ProjectLimits(max_files=1, max_file_bytes=4, max_total_bytes=4, max_depth=2,
                           max_entries=8)
    api = ProjectsBackend(ProjectsStore(limits=limits), worktrees, runner=ReadRunner())
    (root / "extra.txt").write_text("x")
    with pytest.raises(ProjectError, match="snapshot_limit"):
        api._files(RepositoryPin.capture(root))
    assert (root / "nota.txt").read_text() == "uno\n"
    assert (root / "extra.txt").read_text() == "x"


@pytest.mark.asyncio
async def test_review_is_scope_bound_and_requires_live_authority(tmp_path):
    root, api, _ = backend(tmp_path)
    row = await api.register(root, "Proyecto")
    review = await api.review("worktree.create", row["project_id"],
                              {"name": "Copia", "ref": "a" * 40},
                              session_id="sesion", run_id="run")
    with pytest.raises(ProjectError, match="authority_required"):
        await api.apply(review["review_id"], review["digest"], session_id="sesion",
                        run_id="run", authorize=None)
    with pytest.raises(ProjectError, match="review_changed"):
        await api.apply(review["review_id"], review["digest"], session_id="otra",
                        run_id="run", authorize=lambda: None)
    assert not tuple(api.root.iterdir())


@pytest.mark.asyncio
async def test_metadata_changed_inside_authority_callback_blocks_before_dispatch(tmp_path):
    root, api, runner = backend(tmp_path)
    row = await api.register(root, "Proyecto")
    review = await api.review("worktree.create", row["project_id"],
                              {"name": "Copia", "ref": "a" * 40},
                              session_id="sesion", run_id="run")

    def changed():
        (root / ".git" / "HEAD").write_text("b" * 40 + "\n")

    with pytest.raises(ProjectError, match="review_changed"):
        await api.apply(review["review_id"], review["digest"], session_id="sesion",
                        run_id="run", authorize=changed)
    assert api.store.operation(review["review_id"]) is None
    assert not any(call[:2] == ("worktree", "add") for call in runner.calls)


def test_snapshot_refuses_symlinks_hardlinks_and_private_files(tmp_path):
    root, api, _ = backend(tmp_path)
    pin = RepositoryPin.capture(root)
    (root / "enlace").symlink_to(root / "nota.txt")
    with pytest.raises((OSError, ProjectError)):
        api._files(pin)
    (root / "enlace").unlink()
    os.link(root / "nota.txt", root / "duro")
    with pytest.raises(ProjectError, match="file_unsafe"):
        api._files(pin)
    (root / "duro").unlink()
    (root / "auth.json").write_text("No debe leerse")
    with pytest.raises(ValueError, match="private_path"):
        api._files(pin)


def test_runner_has_no_executable_path_fallback(tmp_path):
    with pytest.raises(ProjectError, match="git_unavailable"):
        BoundedGitRunner(tmp_path.resolve() / "missing-git")


def test_git_metadata_includes_and_object_symlinks_are_rejected_before_git(tmp_path):
    root = repository(tmp_path)
    pin = RepositoryPin.capture(root)
    runner = BoundedGitRunner()
    (root / ".git" / "config").write_text('[include]\npath = /synthetic/auth.json\n')
    with pytest.raises(ProjectError, match="external_config_forbidden"):
        runner._safe_metadata(pin)
    (root / ".git" / "config").write_text("")
    (root / ".git" / "objects").mkdir(mode=0o700)
    (root / ".git" / "objects" / "linked").symlink_to(root / "nota.txt")
    with pytest.raises(ProjectError, match="metadata_unsafe"):
        runner._safe_metadata(pin)


@pytest.mark.asyncio
async def test_unknown_git_effect_survives_restart_and_blocks_automatic_new_review(tmp_path):
    root, api, original_runner = backend(tmp_path)

    class UncertainRunner(ReadRunner):
        async def run(self, pin, arguments, **options):
            if arguments[:2] == ("worktree", "add"):
                options["authorize"]()
                raise TimeoutError("Se desconoce el efecto del Git sintético.")
            return await super().run(pin, arguments, **options)

    api.runner = UncertainRunner()
    row = await api.register(root, "Proyecto")
    review = await api.review("worktree.create", row["project_id"],
                              {"name": "Copia", "ref": "a" * 40},
                              session_id="sesion", run_id="run")
    result = await api.apply(review["review_id"], review["digest"], session_id="sesion",
                             run_id="run", authorize=lambda: None)
    assert result["effect_status"] == "unknown" and not result["verified"]
    reopened = ProjectsStore(api.store.document.directory)
    persisted = reopened.operation(review["review_id"])["receipt"]
    assert persisted["target_path"] == result["target_path"]
    second = ProjectsBackend(reopened, api.root, runner=original_runner)
    assert "project_effect_unconfirmed" in second.blockers
    with pytest.raises(ProjectError, match="previous_effect_unconfirmed"):
        await second.review("worktree.create", row["project_id"],
                            {"name": "Otra copia", "ref": "a" * 40},
                            session_id="sesion", run_id="otro")


@pytest.fixture
def real_git(tmp_path):
    if os.environ.get("CI") != "true":
        pytest.skip("Los repositorios y procesos Git reales se ejecutan sólo en CI.")
    root = tmp_path.resolve() / "source"
    root.mkdir(mode=0o700)
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}

    def git(*arguments):
        return subprocess.run(
            ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", *arguments], cwd=root,
            env=env, check=True, capture_output=True, timeout=10).stdout.decode().strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Jarvis CI")
    git("config", "user.email", "jarvis@example.invalid")
    (root / "nota.txt").write_text("original\n")
    git("add", "nota.txt")
    git("commit", "-m", "Fixture pequeño de Jarvis")
    return root, git


@pytest.mark.asyncio
async def test_real_git_create_remove_and_restore_exact_snapshot(real_git, tmp_path):
    root, git = real_git
    worktrees = tmp_path.resolve() / "worktrees"
    worktrees.mkdir(mode=0o700)
    api = ProjectsBackend(ProjectsStore(tmp_path.resolve() / "private"), worktrees)
    source = await api.register(root, "Proyecto CI")
    head = git("rev-parse", "HEAD")

    async def apply(kind, project_id, arguments):
        review = await api.review(kind, project_id, arguments, session_id="ci", run_id="run")
        result = await api.apply(review["review_id"], review["digest"], session_id="ci",
                                 run_id="run", authorize=lambda: None)
        assert result["verified"], result
        with pytest.raises(ProjectError, match="review_changed"):
            await api.apply(review["review_id"], review["digest"], session_id="ci",
                            run_id="run", authorize=lambda: None)
        return result

    created = await apply("worktree.create", source["project_id"], {"name": "Copia", "ref": head})
    child = created["project"]
    path = Path(child["pin"]["root"])
    assert (path / "nota.txt").read_text() == "original\n"
    assert not (await api.inspect(child["project_id"]))["status"]
    archived = await apply("worktree.archive", child["project_id"], {})
    assert archived["removed"] and not path.exists()
    restored = await apply("worktree.restore", source["project_id"],
                           {"name": "Recuperada", "snapshot_id": archived["snapshot_id"]})
    assert (Path(restored["project"]["pin"]["root"]) / "nota.txt").read_text() == "original\n"
    assert git("rev-parse", "HEAD") == head


@pytest.mark.asyncio
async def test_real_git_dirty_archive_preserves_original_and_file_restore(real_git, tmp_path):
    root, git = real_git
    worktrees = tmp_path.resolve() / "worktrees"
    worktrees.mkdir(mode=0o700)
    api = ProjectsBackend(ProjectsStore(tmp_path.resolve() / "private"), worktrees)
    source = await api.register(root, "Proyecto CI", temporary=True)

    async def apply(kind, project_id, arguments):
        review = await api.review(kind, project_id, arguments, session_id="ci", run_id="run")
        return await api.apply(review["review_id"], review["digest"], session_id="ci",
                               run_id="run", authorize=lambda: None)

    created = await apply("worktree.create", source["project_id"],
                          {"name": "Copia", "ref": git("rev-parse", "HEAD")})
    assert created["verified"], created
    child = created["project"]
    file = Path(child["pin"]["root"]) / "nota.txt"
    file.write_text("custodia\n")
    archived = await apply("worktree.archive", child["project_id"], {})
    assert archived["verified"] and archived["original_preserved"]
    file.write_text("posterior\n")
    restored = await apply("file.restore", child["project_id"], {
        "snapshot_id": archived["snapshot_id"], "path": "nota.txt",
        "expected_sha256": hashlib.sha256(b"posterior\n").hexdigest()})
    assert restored["verified"] and file.read_text() == "custodia\n"
    assert not api.store.document.path.exists()


@pytest.mark.asyncio
async def test_real_git_filters_and_hooks_are_never_executed(real_git, tmp_path):
    root, git = real_git
    marker = tmp_path.resolve() / "must-not-exist"
    # Malicious repository configuration is synthetic and never invoked by the
    # fixture's own commands after installation.
    git("config", "filter.fixture.clean", "touch " + str(marker))
    git("config", "filter.fixture.smudge", "touch " + str(marker))
    (root / ".gitattributes").write_text("nota.txt filter=fixture\n")
    hook = root / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\ntouch " + str(marker) + "\n")
    hook.chmod(0o700)
    worktrees = tmp_path.resolve() / "worktrees"
    worktrees.mkdir(mode=0o700)
    api = ProjectsBackend(ProjectsStore(), worktrees)
    row = await api.register(root, "Sin ejecutar filtros")
    state = await api.inspect(row["project_id"])
    review = await api.review("worktree.create", row["project_id"],
                              {"name": "Copia", "ref": state["head"]},
                              session_id="ci", run_id="run")
    result = await api.apply(review["review_id"], review["digest"], session_id="ci",
                             run_id="run", authorize=lambda: None)
    assert result["verified"], result
    assert not marker.exists()
