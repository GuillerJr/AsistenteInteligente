"""Actual restored memory, native-host RPC and SQL receipts; no owner account."""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from test_activation import prepared as prepared
from test_recovery import bundle as bundle
from test_recovery import ctx

from jarvis_core.activation import activate_prepared_profile, review_prepared_profile
from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.protocol import LocalRpcClient
from jarvis_core.service import JarvisService


class NoSdk:
    def __init__(self):
        self.calls = 0
    async def account_status(self):
        self.calls += 1
        raise AssertionError('provider must stay unused')
    login = account_status
    chat = account_status
    async def aclose(self):
        pass


def activate_and_seed(root):
    activate_prepared_profile(review_prepared_profile(root, ctx()), ctx())
    scope = MemoryScope('jarvis', 'fixture_workspace')
    with MemoryStore(root/'memory.sqlite3') as store:
        one = store.upsert(scope, 'nota', 'PRUEBA MEMORIA UNO áé', source_uri='file:///DO-NOT-OPEN')
        two = store.upsert(scope, 'two', 'PRUEBA MEMORIA DOS', source_uri='jarvis://fixture')
        store.upsert(MemoryScope('jarvis', 'fixture_workspace', 'a'*32), 'session',
                     'SESSION-BOUND-NOT-TEMPORARY', source_uri='jarvis://fixture')
    assert one.revision == 2 and two.revision == 1
    return scope


@pytest.fixture
async def host(prepared):
    source = activate_and_seed(prepared)
    provider = NoSdk()
    service = JarvisService(prepared, provider)
    try:
        yield service, service.maintenance_coordinator, source, provider
    finally:
        await service.aclose()


async def finish(coordinator, operation):
    async with asyncio.timeout(10):
        while True:
            result = await coordinator.status({'operation_id':operation['operation_id']})
            if not result['worker_pending']:
                return result
            await asyncio.sleep(.001)


async def inventory(coordinator):
    result = await finish(coordinator, await coordinator.memory_inventory({'agent_id':'jarvis'}))
    assert result['phase'] == 'complete', result
    return result['result']['inventory']


async def review(coordinator):
    listing = await inventory(coordinator)
    assert listing['candidate_count'] == 1, listing
    result = await finish(coordinator, await coordinator.memory_review({
        'inventory_id':listing['inventory_id'],'source_handle':listing['sources'][0]['source_handle']}))
    assert result['phase'] == 'complete', result
    return listing, result['result']['review']


async def decide(coordinator, allow=True):
    async with asyncio.timeout(2):
        while not (rows := (await coordinator.pending({}))['pending']):
            await asyncio.sleep(.001)
    assert len(rows) == 1
    row = rows[0]
    result = await coordinator.decide({'approval_id':row['approval_id'],'digest':row['digest'],
                                      'allow':allow,**row['scope']})
    assert result['accepted']
    return row


async def copy(coordinator):
    listing, prepared = await review(coordinator)
    operation = await coordinator.memory_copy({key:prepared[key]
                                               for key in ('review_id','review_digest')})
    card = await decide(coordinator)
    result = await finish(coordinator, operation)
    return listing, prepared, card, result


async def test_inventory_review_is_readonly_opaque_same_scope_and_without_sdk(host):
    service, coordinator, source, provider = host
    path = service.directory/'memory.sqlite3'
    before = path.read_bytes(), path.stat(), set(service.directory.iterdir())
    initial_scope = service.profile_lease.scope
    listing, prepared = await review(coordinator)
    assert service.profile_lease.scope == initial_scope
    assert service.memory._disk is None
    assert listing['source_kind'] == 'cold_delete_snapshot'
    assert listing['session_bound_scopes_excluded'] == 1
    assert listing['agent_revision'] == '1'
    assert listing['destination']['scope_present'] is False
    assert listing['destination']['scope_generation'] is None
    assert prepared['metadata']['destination_generation'] is None
    assert prepared['metadata']['source_documents'] == 2
    assert prepared['metadata']['schema_before'] == 1
    assert prepared['metadata']['schema_after'] == 2
    assert prepared['metadata']['migration_required'] is True
    assert len(prepared['metadata']['limits']) == 13
    payload = json.dumps([listing,prepared])
    for hidden in (source.workspace_id,'PRUEBA MEMORIA UNO','PRUEBA MEMORIA DOS',
                   'SESSION-BOUND','file:///','source_uri','scope_key','"document_id":'):
        assert hidden not in payload
    assert path.read_bytes() == before[0]
    after = path.stat()
    assert (after.st_dev,after.st_ino,after.st_mode,after.st_mtime_ns,after.st_ctime_ns) == (
        before[1].st_dev,before[1].st_ino,before[1].st_mode,before[1].st_mtime_ns,before[1].st_ctime_ns)
    assert set(service.directory.iterdir()) == before[2]
    assert not (await coordinator.pending({}))['pending'] and provider.calls == 0


async def test_copy_receipt_one_transaction_and_observation_never_replays(host):
    service, coordinator, source, provider = host
    listing, prepared, card, result = await copy(coordinator)
    assert result['phase'] == 'complete', result
    assert card['purpose'] == 'memory.restore.copy_scope'
    assert json.loads(card['arguments_json'])['review'] == prepared['metadata']
    assert card['scope'] == result['scope'] == listing['scope']
    receipt = result['receipt']
    assert receipt['status'] == 'completed' and receipt['effect_status'] == 'scope_copy_committed'
    assert receipt['request_digest'] == prepared['review_digest']
    assert receipt['core_review_digest'] == prepared['metadata']['core_review_digest']
    assert receipt['document_count'] == 2 and receipt['destination_generation_after'] == '1'
    assert result['receipt_durable'] and not result['cleanup_owner_pending']
    assert service.profile_lease.scope.generation == result['scope']['generation']+1
    previous = await coordinator.memory_copy({key:prepared[key]
                                               for key in ('review_id','review_digest')})
    assert previous['operation_id'] == result['operation_id']
    observation = await finish(coordinator, await coordinator.memory_receipt({
        'operation_id':result['operation_id'],'review_digest':prepared['review_digest']}))
    observed = observation['result']['observation']
    assert observed['state'] == 'local_record_present'
    assert observed['prior_acknowledgement'] == 'not_established'
    assert observed['current_documents_match'] and observed['current_scope_generation_matches']
    assert observation['result']['permits_replay'] is False
    assert coordinator._operations[result['operation_id']].receipt == receipt
    destination = MemoryScope('jarvis', service._workspace_id)
    assert len(service.memory.list_documents(source)) == 2
    assert len(service.memory.list_documents(destination)) == 2
    assert not (await inventory(coordinator))['destination']['empty']
    assert provider.calls == 0


async def test_denied_copy_keeps_v1_without_opening_source_or_creating_sql_receipt(host):
    service, coordinator, _, provider = host
    _, prepared = await review(coordinator)
    path = service.directory/'memory.sqlite3'
    before = path.read_bytes()
    operation = await coordinator.memory_copy({key:prepared[key]
                                               for key in ('review_id','review_digest')})
    await decide(coordinator, False)
    result = await finish(coordinator, operation)
    assert result['phase'] == 'denied' and result['receipt'] is None
    assert service.memory._disk is None and path.read_bytes() == before
    assert int.from_bytes(before[60:64], 'big') == 1
    assert provider.calls == 0


async def test_real_unix_rpc_copy_and_lookup_are_bounded_and_no_provider(prepared):
    directory = Path(tempfile.mkdtemp(prefix='jvs-mm-', dir=tempfile.gettempdir())).resolve()
    root = directory/'profile'
    shutil.copytree(prepared,root)
    activate_and_seed(root)
    peer = NoSdk()
    service = JarvisService(root,peer)
    client = LocalRpcClient(root,max_frame_bytes=1024*1024)
    try:
        await service.start()
        await client.connect()
        async def job(method, params):
            value = await client.call(method,params)
            async with asyncio.timeout(10):
                while True:
                    value = await client.call('maintenance.status',
                                              {'operation_id':value['operation_id']})
                    if not value['worker_pending']:
                        assert value['phase'] == 'complete', value
                        return value
                    await asyncio.sleep(.001)
        value = await job('memory.restore.inventory',{'agent_id':'jarvis'})
        listing = value['result']['inventory']
        value = await job('memory.restore.review',{'inventory_id':listing['inventory_id'],
                                                   'source_handle':listing['sources'][0]['source_handle']})
        prepared = value['result']['review']
        operation = await client.call('memory.restore.copy_scope',
                                     {key:prepared[key] for key in ('review_id','review_digest')})
        async with asyncio.timeout(2):
            while not (cards := (await client.call('maintenance.pending'))['pending']):
                await asyncio.sleep(.001)
        card = cards[0]
        assert (await client.call('maintenance.decide',{
            'approval_id':card['approval_id'],'digest':card['digest'],'allow':True,**card['scope']}))['accepted']
        async with asyncio.timeout(10):
            while (value := await client.call('maintenance.status',{
                    'operation_id':operation['operation_id']}))['worker_pending']:
                await asyncio.sleep(.001)
        assert value['phase'] == 'complete', value
        observed = await job('memory.restore.receipt',{'operation_id':value['operation_id'],
                                                       'review_digest':prepared['review_digest']})
        assert observed['result']['observation']['current_documents_match']
        assert peer.calls == 0
    finally:
        await client.aclose()
        await service.aclose()
        shutil.rmtree(directory)


async def test_owner_ticket_expiry_uses_original_review_budget_and_keeps_v1(host):
    from dataclasses import replace
    service, coordinator, _, provider = host
    _, prepared = await review(coordinator)
    row = coordinator._reviews[prepared['review_id']]
    coordinator._reviews[prepared['review_id']] = replace(row, expires=coordinator.clock()+.25)
    before = (service.directory/'memory.sqlite3').read_bytes()
    operation = await coordinator.memory_copy({key:prepared[key]
                                               for key in ('review_id','review_digest')})
    result = await finish(coordinator,operation)
    assert result['phase'] == 'failed' and result['error_code'] == 'memory_restore_review_expired'
    assert not (await coordinator.pending({}))['pending']
    assert service.memory._disk is None
    assert (service.directory/'memory.sqlite3').read_bytes() == before and provider.calls == 0


async def test_source_changed_while_owner_decides_has_no_destination_commit(host):
    service, coordinator, source, provider = host
    _, prepared = await review(coordinator)
    operation = await coordinator.memory_copy({key:prepared[key]
                                               for key in ('review_id','review_digest')})
    async with asyncio.timeout(2):
        while not (await coordinator.pending({}))['pending']:
            await asyncio.sleep(.001)
    service.memory.upsert(source,'nota','SYNTHETIC CHANGE AFTER REVIEW',source_uri='jarvis://fixture')
    await decide(coordinator)
    result = await finish(coordinator,operation)
    assert result['phase'] == 'failed' and result['receipt'] is None
    assert not service.memory.list_documents(MemoryScope('jarvis',service._workspace_id))
    assert service.memory._disk.execute('PRAGMA user_version').fetchone()[0] == 1
    assert provider.calls == 0


async def test_destination_occupied_is_disabled_and_review_never_requests_permission(host):
    service, coordinator, _, provider = host
    destination = MemoryScope('jarvis',service._workspace_id)
    service.memory.upsert(destination,'occupied','CURRENT MEMORY',source_uri='jarvis://fixture')
    listing = await inventory(coordinator)
    assert not listing['destination']['empty']
    assert not listing['sources'][0]['eligible']
    job = await coordinator.memory_review({'inventory_id':listing['inventory_id'],
                                           'source_handle':listing['sources'][0]['source_handle']})
    result = await finish(coordinator,job)
    assert result['phase'] == 'failed'
    assert not (await coordinator.pending({}))['pending']
    assert len(service.memory.list_documents(destination)) == 1 and provider.calls == 0


async def test_empty_destination_tombstone_binds_generation_without_assuming_absence(host):
    service, coordinator, _, _ = host
    destination = MemoryScope('jarvis',service._workspace_id)
    value = service.memory.upsert(destination,'removed','WILL BE REMOVED',source_uri='jarvis://fixture')
    service.memory.delete(destination,value.key,expected_revision=value.revision)
    listing, prepared, _, result = await copy(coordinator)
    assert listing['destination']['scope_present']
    assert listing['destination']['scope_generation'] == '2'
    assert prepared['metadata']['destination_generation'] == '2'
    assert result['phase'] == 'complete'
    assert result['receipt']['destination_generation_after'] == '3'


async def test_lost_commit_ack_query_is_observation_and_legitimate_edit_is_not_corruption(
        host,monkeypatch):
    from jarvis_core.memory import MemoryError
    from jarvis_core.memory_host_cut import MemoryMutationCut
    service, coordinator, _, provider = host
    original_guard = MemoryMutationCut.guard
    def lost_ack(mutation,stage,write=False):
        original_guard(mutation,stage,write)
        if stage == 'memory_copy_committed':
            raise MemoryError('memory_copy_fixture_lost_ack')
    monkeypatch.setattr(MemoryMutationCut,'guard',lost_ack)
    _, prepared, _, result = await copy(coordinator)
    assert result['phase'] == 'publication_unknown', result
    original = dict(result['receipt'])
    assert original['status'] == 'unknown' and not result['cleanup_owner_pending']
    observed = await finish(coordinator,await coordinator.memory_receipt({
        'operation_id':result['operation_id'],'review_digest':prepared['review_digest']}))
    assert observed['result']['observation']['current_documents_match']
    previous = await coordinator.memory_copy({key:prepared[key]
                                               for key in ('review_id','review_digest')})
    assert previous['operation_id'] == result['operation_id']
    count = service.memory._disk.execute('SELECT count(*) FROM memory_scope_copies').fetchone()[0]
    assert count == 1
    destination = MemoryScope('jarvis',service._workspace_id)
    service.memory.upsert(destination,'nota','LEGITIMATE LATER EDIT',source_uri='jarvis://fixture')
    observed = await finish(coordinator,await coordinator.memory_receipt({
        'operation_id':result['operation_id'],'review_digest':prepared['review_digest']}))
    value = observed['result']['observation']
    assert value['state'] == 'local_record_present'
    assert value['current_documents_match'] is False
    assert value['current_scope_generation_matches'] is False
    assert coordinator._operations[result['operation_id']].receipt == original
    assert provider.calls == 0


@pytest.mark.parametrize('corruption',['inventory_id','source_handle','expired'])
async def test_opaque_selector_forgery_or_expiry_cannot_reach_a_copy(host,corruption):
    from uuid import uuid4

    from jarvis_core.host_profile import HostProfileError
    service, coordinator, _, _ = host
    listing = await inventory(coordinator)
    params = {'inventory_id':listing['inventory_id'],
              'source_handle':listing['sources'][0]['source_handle']}
    if corruption == 'expired':
        coordinator._memory_inventory.expires = coordinator.clock()-1
    else:
        params[corruption] = uuid4().hex
    with pytest.raises(HostProfileError):
        await coordinator.memory_review(params)
    assert not (await coordinator.pending({}))['pending'] and service.memory._disk is None


async def test_valid_v1_historical_small_chunks_near_snapshot_row_cap_fail_before_card(host):
    from dataclasses import replace

    from jarvis_core.memory import MemoryLimits
    service, coordinator, source, provider = host
    assert service.memory._disk is None
    historical_limits = MemoryLimits(chunk_bytes=512,max_document_bytes=16000,excerpt_bytes=512)
    with MemoryStore(service.directory/'memory.sqlite3',limits=historical_limits) as seed:
        seed.upsert(source,'nota','X'*1300,source_uri='jarvis://fixture')
        count = seed._disk.execute('SELECT count(*) FROM chunks').fetchone()[0]
        assert count == 6
    coordinator._memory_recovery_limits = replace(coordinator._memory_recovery_limits,max_rows=6)
    before = (service.directory/'memory.sqlite3').read_bytes()
    listing = await inventory(coordinator)
    job = await coordinator.memory_review({'inventory_id':listing['inventory_id'],
                                           'source_handle':listing['sources'][0]['source_handle']})
    result = await finish(coordinator,job)
    assert result['phase'] == 'failed' and result['error_code'] == 'memory_copy_snapshot_row_limit'
    assert not (await coordinator.pending({}))['pending'] and service.memory._disk is None
    assert (service.directory/'memory.sqlite3').read_bytes() == before and provider.calls == 0


async def test_aggregate_snapshot_budget_too_small_for_current_memory_fails_before_permission(host):
    from dataclasses import replace
    from uuid import uuid4

    from jarvis_core.host_profile import freeze_sources
    from jarvis_core.memory_host_read import read_memory_image
    from jarvis_core.recovery import RecoveryContext
    from jarvis_core.tools import HostCancellation
    service, coordinator, _, provider = host
    with service.profile_gate.exclusive() as cut:
        frozen = freeze_sources(service,cut)
        context = RecoveryContext(uuid4().hex,cut.scope.profile_id,cut.scope.generation,
                                  lambda stage,write=False:frozen.assert_current(cut),
                                  HostCancellation(),coordinator.clock()+20)
        image = read_memory_image(frozen,cut,context)
    required = image.sibling_bytes+len(image.data)
    coordinator._memory_recovery_limits = replace(coordinator._memory_recovery_limits,
                                                  max_total_bytes=required-1)
    before = (service.directory/'memory.sqlite3').read_bytes()
    result = await finish(coordinator,await coordinator.memory_inventory({'agent_id':'jarvis'}))
    assert result['phase'] == 'failed', result
    assert not (await coordinator.pending({}))['pending'] and service.memory._disk is None
    assert (service.directory/'memory.sqlite3').read_bytes() == before and provider.calls == 0
