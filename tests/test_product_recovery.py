"""Product backups preserve original history and never admit operational state."""

import base64
import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, replace

import pytest
from test_host_profile import service as service
from test_product_service import product_connection as product_connection
from test_recovery import bundle as bundle
from test_recovery import ctx, target

from jarvis_core import product_interactions, session_coordination, state
from jarvis_core.product_projects import ProjectsBackend, ProjectsStore, RepositoryPin
from jarvis_core.product_projects_github import GitHubCredentialService, GitHubProjects
from jarvis_core.product_recovery import (
    SIDECAR,
    ProductRecoveryError,
    ProductRecoveryLimits,
    ProductSqliteSource,
    decode_product_document,
    disarm_product_document,
    export_product_state,
    import_product_state,
    validate_product_document,
)
from jarvis_core.recovery import (
    JsonSource,
    RecoveryError,
    RecoveryLimits,
    create_backup,
    restore_new,
    verify_backup,
)


def private_profile(tmp_path, name="profile"):
    root = tmp_path.resolve() / name
    root.mkdir(mode=0o700)
    return root


def populate_coordination(root):
    path = root / "session-coordination.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    session_coordination.SessionCoordinationStore._initialize(connection)
    client, workspace, session = "a" * 64, "b" * 32, "c" * 32
    connection.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,1,?,1)",
                       (workspace, "Origen histórico", "/synthetic/external", 1, 2, client))
    connection.execute("INSERT INTO session_links(session_id,workspace_id,owner_id,group_name,"
                       "pinned,parent_session_id,depth,revision,created_ns) "
                       "VALUES(?,?,?,NULL,1,NULL,0,1,1)",
                       (session, workspace, client))
    connection.execute("INSERT INTO members VALUES(?,?,?)", (session, "d" * 64, "collaborate"))
    connection.execute("INSERT INTO reservations VALUES(?,?,?,?,NULL,?,1,NULL,NULL,NULL,NULL,"
                       "'dispatched','known',0,1,1,NULL)",
                       ("e" * 32, "f" * 32, session, client, "0" * 64))
    connection.close()
    path.chmod(0o600)
    return path


def populate_interactions(root):
    path = root / "product-interactions.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    state.StateStore._initialize(connection)
    for statement in product_interactions._SCHEMA:
        connection.execute(statement)
    connection.execute("INSERT INTO product_format VALUES(1)")
    scope = product_interactions.InteractionScope("c" * 32, "f" * 32, "jarvis", "b" * 32)
    question = {"questions": [{"id": "q", "prompt": "Elige una opción"}],
                "grants_tool_approval": False}
    connection.execute("INSERT INTO product_records VALUES(?,?,?,?,?,?,1,'pending',999,?,1,1)",
        ("1" * 32, "2" * 64, "3" * 64, json.dumps(asdict(scope)), "a" * 64,
         "question", json.dumps(question)))
    connection.execute("INSERT INTO product_records VALUES(?,?,?,?,?,?,1,'active',0,?,1,1)",
        ("4" * 32, "2" * 64, "3" * 64, json.dumps(asdict(scope)), "a" * 64,
         "notification_preferences", json.dumps({"enabled": True, "silenced": False})))
    connection.close()
    path.chmod(0o600)
    return path


def populate_projects(root, tmp_path):
    external = tmp_path.resolve() / "external-repo"
    external.mkdir(mode=0o700)
    (external / ".git").mkdir(mode=0o700)
    store = ProjectsStore(root / "projects")
    project = store.register("Proyecto histórico", RepositoryPin.capture(external))
    raw = b"original sintetico\n"
    digest = hashlib.sha256(raw).hexdigest()
    files = {"nota.txt": {"base64": base64.b64encode(raw).decode(), "mode": 0o600,
                           "sha256": digest}}
    metadata = store.save_snapshot_files(files, temporary=False, authorize=lambda: None)

    def save(value):
        value["snapshots"]["9" * 32] = {"project_id": project["project_id"], "head": "a" * 40,
            "files": metadata, "digest": hashlib.sha256(json.dumps(metadata, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()}
    store._update(save)
    return external, digest, raw


def test_export_absence_is_explicit_and_creates_nothing(tmp_path):
    root = private_profile(tmp_path)
    assert export_product_state(root) is None
    assert not tuple(root.iterdir())


def test_history_import_keeps_original_rows_but_creates_no_live_authorities(tmp_path):
    source = private_profile(tmp_path)
    coord = populate_coordination(source)
    interactions = populate_interactions(source)
    before = {path.name: path.read_bytes() for path in (coord, interactions)}
    exported = decode_product_document(export_product_state(source))
    archived = exported["archives"][0]["source"]
    assert archived["coordination"]["members"][0]["role"] == "collaborate"
    assert archived["coordination"]["reservations"][0]["status"] == "dispatched"
    assert archived["interactions"]["product_records"][0]["status"] == "pending"
    disarmed = disarm_product_document(exported)
    assert disarmed["state"] == "restored_history"
    assert all(flag is False for flag in disarmed["disarms"].values())
    destination = private_profile(tmp_path, "restored")
    calls = []
    receipt = import_product_state(disarmed, destination, authorize=lambda: calls.append(True))
    assert receipt["history_only"] and not receipt["live_objects_created"] and calls == [True]
    assert {path.name for path in destination.iterdir()} == {SIDECAR}
    assert decode_product_document((destination / SIDECAR).read_bytes())["archives"] == (
        exported["archives"])
    assert before == {path.name: path.read_bytes() for path in (coord, interactions)}


def test_snapshots_and_previous_sidecar_survive_subsequent_backup_without_external_reads(
        tmp_path, monkeypatch):
    source = private_profile(tmp_path)
    external, digest, raw = populate_projects(source, tmp_path)
    monkeypatch.setattr(RepositoryPin, "capture", lambda _path: pytest.fail(
        "El respaldo no debe resolver ni activar repositorios externos."))
    first = decode_product_document(export_product_state(source))
    assert base64.b64decode(first["blobs"][digest]) == raw
    restored = private_profile(tmp_path, "restored")
    import_product_state(first, restored, authorize=lambda: None)
    second = decode_product_document(export_product_state(restored))
    assert second["archives"] == first["archives"]
    assert second["blobs"] == first["blobs"]
    assert external.exists() and set(path.name for path in external.iterdir()) == {".git"}


def test_export_deduplicates_unchanged_durable_source_against_previous_sidecar(tmp_path):
    root = private_profile(tmp_path)
    populate_coordination(root)
    first = decode_product_document(export_product_state(root))
    import_product_state(first, root, authorize=lambda: None)
    again = decode_product_document(export_product_state(root))
    assert len(again["archives"]) == 1
    assert again["archives"] == first["archives"]


def test_import_refuses_to_overwrite_sidecar_or_follow_symlink(tmp_path):
    source = private_profile(tmp_path)
    populate_coordination(source)
    exported = export_product_state(source)
    target = private_profile(tmp_path, "target")
    (target / SIDECAR).write_text("conservar")
    with pytest.raises(FileExistsError):
        import_product_state(exported, target, authorize=lambda: None)
    assert (target / SIDECAR).read_text() == "conservar"
    (target / SIDECAR).unlink()
    (target / SIDECAR).symlink_to(source / "session-coordination.sqlite3")
    with pytest.raises(FileExistsError):
        import_product_state(exported, target, authorize=lambda: None)


def test_blob_corruption_and_missing_custody_are_rejected(tmp_path):
    source = private_profile(tmp_path)
    _, digest, _ = populate_projects(source, tmp_path)
    valid = decode_product_document(export_product_state(source))
    valid["blobs"][digest] = base64.b64encode(b"alterado").decode()
    with pytest.raises(ProductRecoveryError, match="blob_changed"):
        validate_product_document(valid)
    blob = source / "projects" / "project_blobs" / digest
    blob.unlink()
    with pytest.raises(ProductRecoveryError, match="snapshot_blob_missing"):
        export_product_state(source)


def test_unknown_project_entry_is_not_silently_omitted_or_read(tmp_path):
    root = private_profile(tmp_path)
    (root / "projects").mkdir(mode=0o700)
    original = root / "projects" / "auth.json"
    original.write_text("valor sintético que no se debe leer")
    original.chmod(0o600)
    with pytest.raises(ProductRecoveryError, match="inventory_unclassified"):
        export_product_state(root)


def test_hot_cold_sqlite_requires_custodian_instead_of_touching_original(tmp_path):
    root = private_profile(tmp_path)
    source = populate_coordination(root)
    original = source.read_bytes()
    journal = source.with_name(source.name + "-journal")
    journal.write_bytes(b"sintetico")
    journal.chmod(0o600)
    with pytest.raises(ProductRecoveryError, match="live_sqlite_source_required"):
        export_product_state(root)
    assert source.read_bytes() == original and journal.read_bytes() == b"sintetico"


def test_ram_sqlite_source_cannot_be_exported_as_durable(tmp_path):
    root = private_profile(tmp_path)
    connection = sqlite3.connect(":memory:", isolation_level=None)
    session_coordination.SessionCoordinationStore._initialize(connection)
    try:
        with pytest.raises(ProductRecoveryError, match="source_path_mismatch"):
            export_product_state(root, sqlite_sources={"coordination": ProductSqliteSource(
                connection, lambda: None)})
    finally:
        connection.close()


def test_row_and_blob_quotas_are_explicit_with_small_fixtures(tmp_path):
    root = private_profile(tmp_path)
    populate_coordination(root)
    with pytest.raises(ProductRecoveryError, match="row_limit"):
        export_product_state(root, limits=ProductRecoveryLimits(max_rows=1))
    populate_projects(root, tmp_path)
    with pytest.raises((ProductRecoveryError, OSError)):
        export_product_state(root, limits=ProductRecoveryLimits(max_blob_bytes=4))


def test_arbitrary_tables_and_triggers_fail_instead_of_being_executed(tmp_path):
    root = private_profile(tmp_path)
    path = populate_coordination(root)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unexpected(payload TEXT)")
    connection.commit()
    connection.close()
    with pytest.raises(ProductRecoveryError, match="schema_unsupported"):
        export_product_state(root)


def test_inert_policy_cannot_be_changed_into_an_import_permission(tmp_path):
    root = private_profile(tmp_path)
    populate_coordination(root)
    value = decode_product_document(export_product_state(root))
    value["disarms"]["client_ids_active"] = True
    with pytest.raises(ProductRecoveryError, match="document_invalid"):
        validate_product_document(value)


def with_product(bundle):
    populate_coordination(bundle.root)
    populate_interactions(bundle.root)
    data = export_product_state(bundle.root)
    docs = (*bundle.json, JsonSource("product", data, lambda: None))
    present = {source.role for source in (*bundle.sqlite, *docs)}
    all_roles = {"state", "memory", "schedules", "agents", "skills", "telegram", "plugins",
                 "automations", "product"}
    return replace(bundle, json=docs, absent=tuple(sorted(all_roles - present)))


def test_product_role_roundtrip_through_backup_restore_and_prepared_activation(tmp_path, bundle):
    from jarvis_core.activation import review_prepared_profile

    augmented = with_product(bundle)
    receipt = create_backup(augmented, target(tmp_path), ctx())
    assert receipt.status == "completed"
    verified = verify_backup(tmp_path / "backup", ctx())
    restored = restore_new(tmp_path / "backup", verified, target(tmp_path, "restored"), ctx())
    assert restored.status == "completed"
    path = tmp_path / "restored"
    history = decode_product_document((path / SIDECAR).read_bytes())
    assert history["state"] == "restored_history"
    assert not (path / "session-coordination.sqlite3").exists()
    assert not (path / "product-interactions.sqlite3").exists()
    assert not (path / "projects").exists()
    review = review_prepared_profile(path, ctx())
    assert any(row.role == "product" and row.present for row in review.resources)


def test_product_role_stays_inside_existing_global_backup_quota(tmp_path, bundle):
    augmented = with_product(bundle)
    before = (bundle.root / "session-coordination.sqlite3").read_bytes()
    with pytest.raises(RecoveryError):
        create_backup(augmented, target(tmp_path), ctx(),
                      limits=RecoveryLimits(max_total_bytes=1024))
    assert (bundle.root / "session-coordination.sqlite3").read_bytes() == before


@pytest.mark.asyncio
async def test_frozen_host_exports_product_role_and_rejects_new_data(service):
    from jarvis_core.host_profile import HostProfileError, freeze_sources

    path = populate_coordination(service.directory)
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        exported = frozen.bind(cut)
        assert any(source.role == "product" for source in exported.json)
        connection = sqlite3.connect(path)
        connection.execute("UPDATE workspaces SET name='Cambio concurrente'")
        connection.commit()
        connection.close()
        with pytest.raises(HostProfileError, match="sources_changed"):
            frozen.assert_current(cut)


@pytest.mark.asyncio
async def test_passive_github_credential_does_not_block_cut_but_request_does(service):
    from jarvis_core.host_profile import service_blockers

    credentials = GitHubCredentialService()
    credentials.install("jarvis-synthetic-only")
    backend = GitHubProjects(credentials)
    service.product.github["a" * 64] = {"backend": backend}
    try:
        assert "github_request_active" not in service_blockers(service)
        backend._active_requests = 1
        assert "github_request_active" in service_blockers(service)
    finally:
        backend._active_requests = 0
        service.product.github.clear()
        credentials.revoke()


@pytest.mark.asyncio
async def test_stable_unknown_project_journal_can_be_backed_up_without_replaying(service, tmp_path):
    from jarvis_core.host_profile import freeze_sources, service_blockers

    populate_projects(service.directory, tmp_path)
    store = ProjectsStore(service.directory / "projects")
    project = store.list()[0]

    def uncertain(value):
        value["operations"]["8" * 32] = {
            "digest": "7" * 64, "kind": "worktree.create", "phase": "dispatched",
            "receipt": {"project_id": project["project_id"], "effect_status": "unknown"}}
    store._update(uncertain)
    worktrees = tmp_path.resolve() / "worktrees"
    worktrees.mkdir(mode=0o700)
    service.product.projects = ProjectsBackend(store, worktrees, runner=object())
    assert "project_effect_unconfirmed" in service.product.projects.blockers
    assert "project_effect_unconfirmed" not in service_blockers(service)
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        bound = frozen.bind(cut)
        captured = decode_product_document(next(row.data for row in bound.json
                                               if row.role == "product"))
    archived = captured["archives"][0]["source"]["projects"]
    assert archived["operations"]["8" * 32]["receipt"]["effect_status"] == "unknown"
    assert store.operation("8" * 32)["phase"] == "dispatched"


def test_doctor_admits_absent_product_role_without_creating_sources(tmp_path):
    from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report

    root = private_profile(tmp_path)
    info = root.stat()
    selected = DoctorInventory("product_fixture", 1, root, (info.st_dev, info.st_ino),
                               "offline_quiescent", roles=("product",))
    context = DoctorContext(lambda _stage, write=False: None, threading.Event())
    report = inspect_report(selected, context)
    assert report.status == "uninitialized"
    assert report.checks[0].role == "product" and report.checks[0].status == "absent"
    assert not tuple(root.iterdir())


@pytest.mark.asyncio
async def test_doctor_uses_frozen_virtual_product_image_without_reading_live_database(
        service, monkeypatch):
    from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report
    from jarvis_core.host_profile import freeze_sources

    path = populate_coordination(service.directory)
    before = path.read_bytes()
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        context = DoctorContext(lambda _stage, write=False: frozen.assert_current(cut),
                                threading.Event())
        images = frozen.doctor_images(cut, context, RecoveryLimits())
        selected = DoctorInventory(frozen.scope.profile_id, frozen.scope.generation,
            service.directory, (frozen.root_identity.device, frozen.root_identity.inode),
            "coherent_snapshot", roles=tuple(row["role"] for row in frozen.metadata),
            cut_id=cut.cut_id)
        assert images.product is not None and images.product.disposition == "image"
        import os
        original_open = os.open

        def no_source_reads(name, *args, **kwargs):
            assert os.fspath(name) not in {str(path), path.name, SIDECAR}
            return original_open(name, *args, **kwargs)
        monkeypatch.setattr(os, "open", no_source_reads)
        report = inspect_report(selected, context, sqlite_images=images)
    product = next(check for check in report.checks if check.role == "product")
    assert product.status == "ok" and product.inspection_source == "host_ram_snapshot"
    assert dict(product.counts)["product_session_history"] == 1
    assert "/synthetic/external" not in json.dumps(report.to_dict())
    assert "Origen histórico" not in json.dumps(report.to_dict())
    assert path.read_bytes() == before


def test_doctor_offline_does_not_silently_omit_live_product_databases(tmp_path, monkeypatch):
    from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report

    root = private_profile(tmp_path)
    source = populate_coordination(root)
    before = source.read_bytes()
    info = root.stat()
    selected = DoctorInventory("product_fixture", 1, root, (info.st_dev, info.st_ino),
                               "offline_quiescent", roles=("product",))
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail(
        "Doctor no puede abrir ni construir un store productivo."))
    context = DoctorContext(lambda _stage, write=False: None, threading.Event())
    report = inspect_report(selected, context)
    assert report.status == "blocked"
    assert report.checks[0].code == "doctor_needs_quiescent_snapshot"
    assert source.read_bytes() == before


def test_doctor_product_ram_hash_and_global_quota_are_checked_before_validation(tmp_path):
    from jarvis_core.doctor import (
        DoctorContext,
        DoctorInventory,
        DoctorLimits,
        DoctorProductImage,
        DoctorSqliteImages,
        inspect_report,
    )

    root = private_profile(tmp_path)
    populate_coordination(root)
    data = export_product_state(root)
    info = root.stat()
    identity = info.st_dev, info.st_ino
    selected = DoctorInventory("product_fixture", 1, root, identity, "coherent_snapshot",
                               roles=("product",), cut_id="product_cut")
    image = DoctorProductImage("image", data, "0" * 64)
    bundle = DoctorSqliteImages(selected.profile_id, selected.generation, identity,
                                selected.cut_id, (), image)
    context = DoctorContext(lambda _stage, write=False: None, threading.Event())
    report = inspect_report(selected, context, sqlite_images=bundle)
    assert report.status == "corrupt"
    assert report.checks[0].code == "doctor_snapshot_hash_mismatch"
    assert "/synthetic/external" not in json.dumps(report.to_dict())
    image = DoctorProductImage("image", data, hashlib.sha256(data).hexdigest())
    bundle = replace(bundle, product=image)
    context = DoctorContext(lambda _stage, write=False: None, threading.Event(),
                            limits=DoctorLimits(total_bytes=100))
    report = inspect_report(selected, context, sqlite_images=bundle)
    assert report.status == "blocked" and report.checks[0].code == "doctor_byte_budget"


def test_doctor_absent_selected_product_stays_partial_until_valid_history_exists(tmp_path):
    from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report
    from jarvis_core.profiles import ProfilesStore

    root = private_profile(tmp_path)
    ProfilesStore(root / "profiles").create("Agente sintético")
    info = root.stat()
    selected = DoctorInventory("product_fixture", 1, root, (info.st_dev, info.st_ino),
                               "offline_quiescent", roles=("agents", "product"))

    def inspect():
        return inspect_report(selected, DoctorContext(lambda _stage, write=False: None,
                                                      threading.Event()))
    before = inspect()
    assert before.status == "partial"
    assert next(check for check in before.checks if check.role == "product").status == "absent"
    source = private_profile(tmp_path, "source")
    populate_coordination(source)
    import_product_state(export_product_state(source), root, authorize=lambda: None)
    initialized = inspect()
    assert initialized.status == "ok"
    assert next(check for check in initialized.checks if check.role == "product").status == "ok"


@pytest.mark.asyncio
async def test_signed_product_initialization_keeps_cold_conversation_store_and_provider_untouched(
        product_connection, monkeypatch):
    from jarvis_core.host_profile import freeze_sources

    service, client, peer, _, _, _ = product_connection
    path = service.directory / "jarvis.sqlite3"
    assert service.store._disk is None
    with state.StateStore(path) as cold:
        cold.create_session("Conversación fría sintética")
    before, metadata = path.read_bytes(), path.stat()
    monkeypatch.setattr(state.StateStore, "_open_disk", lambda *_args: pytest.fail(
        "Inicializar producto no debe abrir el store de conversaciones frío."))
    initialized = await client.call("product.initialize", {})
    assert initialized["workspaces"] and service.product.enabled
    assert service.store._disk is None and service.product.interactions._state._disk is None
    assert peer.calls == [] and service.product.projects.runner.active_children == 0
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service, cut)
        product = next(row for row in frozen._json if row.role == "product")
        assert product.data is not None
        document = decode_product_document(product.data)
        assert len(document["archives"][0]["source"]["coordination"]["workspaces"]) == 1
    assert path.read_bytes() == before
    after = path.stat()
    assert (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (
        metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns)
    assert service.store._disk is None and peer.calls == []
