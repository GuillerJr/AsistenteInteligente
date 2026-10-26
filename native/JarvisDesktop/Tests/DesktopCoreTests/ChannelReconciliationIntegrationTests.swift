import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Native DTOs and Store -> authenticated Unix RPC -> own registry/authority.
/// The admission is synthetic setup; every route effect still has its real gate.
/// This fixture has no account/token and rejects every attempted IP socket.
final class ChannelReconciliationIntegrationTests: XCTestCase {
    private static let script = """
    import asyncio, hashlib, importlib.util, json, signal, socket, sqlite3, sys
    from pathlib import Path
    from jarvis_core.activation import activate_prepared_profile,review_prepared_profile
    from jarvis_core.recovery import canonical
    from jarvis_core.service import JarvisService
    root=Path(sys.argv[1]); source=Path(sys.argv[2]); mode=sys.argv[3]
    spec=importlib.util.spec_from_file_location('jarvis_activation_fixture',source.parent/'script'/'activation_reconciliation_fixture.py')
    corpus=importlib.util.module_from_spec(spec); spec.loader.exec_module(corpus)
    class NoSdkPeer:
      calls=0
      async def start(self): self.calls+=1; raise AssertionError('unselected SDK start')
      async def account_status(self): self.calls+=1; raise AssertionError('unselected account read')
      async def login(self): self.calls+=1; raise AssertionError('unselected login')
      async def chat(self,*args,**kwargs): self.calls+=1; raise AssertionError('unexpected inference')
      async def aclose(self): pass
    async def main():
      if mode=='new':
        corpus.prepare(root)
        prepared=root/'memory_channels'
        # This admits only our generated setup; it does not reconcile a route.
        receipt=activate_prepared_profile(review_prepared_profile(prepared,corpus._context()),corpus._context())
        assert receipt.status=='completed'
      prepared=root/'memory_channels'; peer=NoSdkPeer()
      network_calls=0; original_socket=socket.socket
      def no_ip_socket(family=socket.AF_INET,*args,**kwargs):
        nonlocal network_calls
        if family in {socket.AF_INET,socket.AF_INET6}:
          network_calls+=1; raise AssertionError('local reconciliation attempted IP transport')
        return original_socket(family,*args,**kwargs)
      socket.socket=no_ip_socket
      service=JarvisService(prepared,peer)
      submissions=0; accepted=0
      real_submit=service.server._handlers['telegram.restore.reconcile']
      real_decide=service.server._handlers['maintenance.decide']
      async def submit(params):
        nonlocal submissions
        submissions+=1; return await real_submit(params)
      async def decide(params):
        nonlocal accepted
        result=await real_decide(params); accepted+=int(result['accepted']); return result
      def history_hash():
        with sqlite3.connect(':memory:') as database:
          database.deserialize((prepared/'jarvis.sqlite3').read_bytes())
          rows={table:database.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()
            for table in ('sessions','messages','runs','events')}
          return hashlib.sha256(canonical(rows)).hexdigest()
      async def trace(params):
        journal=service._telegram_journal.document.read()
        historical={key:value for key,value in journal.items() if key!='routes'}
        return {'history_sha256':history_hash(),
          'telegram_history_sha256':hashlib.sha256(canonical(historical)).hexdigest(),
          'telegram_file_sha256':hashlib.sha256((prepared/'telegram'/'telegram.json').read_bytes()).hexdigest(),
          'historical_events':len(journal['events']),'control_messages':len(journal['control_messages']),
          'other_route':journal['routes']['84'],'provider_calls':peer.calls,'network_calls':network_calls,
          'connected':service._telegram_channel is not None,'submissions':submissions,'accepted':accepted}
      async def revoke(params):
        service.authority.revoke_host(service.profile_lease.scope)
        return {'revoked':True}
      service.server._handlers.update({'telegram.restore.reconcile':submit,'maintenance.decide':decide,
        'fixture.channels.trace':trace,'fixture.channels.revoke':revoke})
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: await service.aclose()
    asyncio.run(main())
    """

    @MainActor
    private struct RunningFixture {
        let root: String
        let owner: FixtureProcess
        let transport: LocalRPCClient
        let client: ChannelReconciliationClient
    }

    @MainActor
    private func launch(root: String? = nil, restart: Bool = false) async throws -> RunningFixture {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"],
              let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa las rutas explícitas de script/test_desktop.sh.")
        }
        let parent = root ?? "/private/tmp/jarvis-ar-swift-" + UUID().uuidString.prefix(8)
        let logs = "/private/tmp/jvs-channels-log-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", Self.script, parent, source, restart ? "restart" : "new"]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice
        let descriptor = Darwin.open(logs + "/stderr.log", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw RPCFailure.configuration }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        child.standardError = handle
        addTeardownBlock { try? handle.close() }
        let owner = try startFixtureProcess(child, directory: logs)
        let transport = LocalRPCClient(stateDirectory: parent + "/memory_channels")
        let client = ChannelReconciliationClient(transport: transport)
        addTeardownBlock { @MainActor in
            await client.close()
            let closed = await owner.stop()
            guard closed == .stopped else {
                XCTFail("El hijo propio no confirmó el cierre; se conserva el perfil \(parent).")
                return
            }
            if FileManager.default.fileExists(atPath: parent) {
                try FileManager.default.removeItem(atPath: parent)
            }
        }
        try await ServiceBootstrap.waitForHealth(stateDirectory: parent + "/memory_channels", timeout: 8) {
            child.isRunning
        }
        return RunningFixture(root: parent, owner: owner, transport: transport, client: client)
    }

    @MainActor
    private func settle(_ fixture: RunningFixture, _ initial: MaintenanceOperation) async throws -> MaintenanceOperation {
        let client = MaintenanceClient(transport: fixture.transport)
        let deadline = ContinuousClock.now.advanced(by: .seconds(10))
        var row = initial
        while !row.settled, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(15))
            row = try await client.operation(initial.id)
        }
        guard row.settled else { throw RPCFailure.unavailable }
        return row
    }

    @MainActor
    private func ticket(_ fixture: RunningFixture, review: ChannelReconciliationReview,
                        operation: MaintenanceOperation) async throws -> MaintenanceApproval {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let pending = try await fixture.client.pending()
            if let row = pending.approvals.first(where: { review.matches($0, operation: operation) }) {
                return row
            }
            try await Task.sleep(for: .milliseconds(15))
        }
        throw RPCFailure.unavailable
    }

    @MainActor
    private func assertNoReplay(_ before: RPCValue, _ after: RPCValue,
                               file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(after["history_sha256"], before["history_sha256"], file: file, line: line)
        XCTAssertEqual(after["telegram_history_sha256"], before["telegram_history_sha256"], file: file, line: line)
        XCTAssertEqual(after["other_route"], before["other_route"], file: file, line: line)
        XCTAssertEqual(after["historical_events"].integer, 1, file: file, line: line)
        XCTAssertEqual(after["control_messages"].integer, 1, file: file, line: line)
        XCTAssertEqual(after["provider_calls"].integer, 0, file: file, line: line)
        XCTAssertEqual(after["network_calls"].integer, 0, file: file, line: line)
        XCTAssertEqual(after["connected"], .bool(false), file: file, line: line)
    }

    @MainActor
    func testNonemptyRestoredProfilePassesNativeBootstrapCatalogsBeforeReconciliation() async throws {
        let fixture = try await launch()
        let local = try await RestoreProfileClient(transport: fixture.transport).localStatus()
        XCTAssertFalse(local.providerSelected)
        XCTAssertFalse(local.providerQueryAllowed)
        let status = try await fixture.transport.call("service.status")
        XCTAssertEqual(status["ready"], .bool(true))
        XCTAssertEqual(status["provider"]["connected"], .bool(false))
        let management = ManagementClient(transport: fixture.transport)
        let agents = try await management.agents()
        XCTAssertEqual(agents.map(\.id), ["jarvis"])
        let skills = try await management.skills(agentID: "jarvis")
        XCTAssertTrue(skills.isEmpty)
        let wireSchedules = try await fixture.transport.call("schedules.list")
        let firstSchedule = try XCTUnwrap(wireSchedules["schedules"].array?.first)
        XCTAssertTrue(ManagementValidation.hash(firstSchedule["workspace_id"].string ?? ""),
                      "The synthetic schedule must use the same workspace identity format as native production scheduling")
        let schedules = try await management.schedules()
        XCTAssertEqual(schedules.schedules.count, 1)
        XCTAssertFalse(try XCTUnwrap(schedules.schedules.first).enabled)
        let sessions = try await fixture.transport.call("sessions.list", params: ["include_archived": .bool(true)])
        let rows = try XCTUnwrap(sessions["sessions"].array)
        XCTAssertEqual(rows.count, 2)
        for row in rows {
            let identity = try XCTUnwrap(row["session_id"].string)
            let conversation = try await ConversationReader(transport: fixture.transport).read(
                SessionScope(sessionID: identity, temporary: false))
            XCTAssertEqual(conversation.messages.count, 1)
            XCTAssertNil(conversation.latestRun)
        }
        let toolRows = try await fixture.transport.call("tools.list")
        let tools = try ManagementValidation.rows(toolRows["tools"], limit: 256, parse: ToolDescriptor.init)
        XCTAssertFalse(tools.isEmpty)
        let workspace = try await fixture.transport.call("workspace.get")
        XCTAssertEqual(workspace["workspace"]["available"], .bool(true))
        XCTAssertEqual(workspace["workspace"]["private_default"], .bool(true))
        let mcp = try await MCPManagementClient(transport: fixture.transport).list()
        XCTAssertTrue(mcp.servers.isEmpty)
        let telegram = try await TelegramClient(transport: fixture.transport).status()
        XCTAssertFalse(telegram.connected)
        XCTAssertFalse(telegram.running)
        let trace = try await fixture.transport.call("fixture.channels.trace")
        XCTAssertEqual(trace["provider_calls"].integer, 0)
        XCTAssertEqual(trace["network_calls"].integer, 0)
    }

    @MainActor
    func testTypedEnableSeparateRevocationAndRestartPreserveHistoryWithoutConnections() async throws {
        let fixture = try await launch()
        let before = try await fixture.transport.call("fixture.channels.trace")
        let inspected = try await fixture.client.inspect(chatID: 42)
        XCTAssertFalse(inspected.route.enabled)
        let review = try await fixture.client.review(chatID: 42, revision: 1, enabled: true)
        XCTAssertEqual(review.sessionID, String(repeating: "a", count: 32))
        XCTAssertEqual(review.botID, 424242)
        let readonly = try await fixture.transport.call("fixture.channels.trace")
        XCTAssertEqual(before["telegram_file_sha256"], readonly["telegram_file_sha256"])
        let initial = try await fixture.client.submit(review)
        let approval = try await ticket(fixture, review: review, operation: initial)
        let accepted = try await fixture.client.decide(approval, review: review, operation: initial, allow: true)
        XCTAssertTrue(accepted) // Synthetic decision through the actual central authority.
        let completed = try await settle(fixture, initial)
        XCTAssertTrue(completed.success)
        XCTAssertTrue(completed.receiptDurable)
        XCTAssertEqual(completed.channelReceipt?.revisionAfter, 2)
        let enabled = try await fixture.client.inspect(chatID: 42)
        XCTAssertTrue(enabled.route.enabled)
        XCTAssertEqual(enabled.route.revision, 2)
        let retry = try await fixture.client.submit(review)
        XCTAssertEqual(retry.id, completed.id)
        let revoke = try await fixture.client.review(chatID: 42, revision: 2, enabled: false)
        let pendingRevoke = try await fixture.client.submit(revoke)
        let revokeTicket = try await ticket(fixture, review: revoke, operation: pendingRevoke)
        let revoked = try await fixture.client.decide(revokeTicket, review: revoke,
                                                     operation: pendingRevoke, allow: true)
        XCTAssertTrue(revoked)
        let revokedOperation = try await settle(fixture, pendingRevoke)
        XCTAssertTrue(revokedOperation.success)
        let disabled = try await fixture.client.inspect(chatID: 42)
        XCTAssertFalse(disabled.route.enabled)
        XCTAssertEqual(disabled.route.revision, 3)
        let after = try await fixture.transport.call("fixture.channels.trace")
        assertNoReplay(before, after)
        XCTAssertEqual(after["submissions"].integer, 3) // One exact duplicate, no second effect.
        XCTAssertEqual(after["accepted"].integer, 2)
        await fixture.client.close()
        let stopped = await fixture.owner.stop()
        XCTAssertEqual(stopped, .stopped)
        let restarted = try await launch(root: fixture.root, restart: true)
        let persisted = try await restarted.client.inspect(chatID: 42)
        XCTAssertFalse(persisted.route.enabled)
        XCTAssertEqual(persisted.route.revision, 3)
        let restartedTrace = try await restarted.transport.call("fixture.channels.trace")
        assertNoReplay(before, restartedTrace)
        XCTAssertEqual(restartedTrace["submissions"].integer, 0)
        XCTAssertEqual(restartedTrace["accepted"].integer, 0)
    }

    @MainActor
    func testNativeStoreDenialAndCancellationLeaveRouteBytesUnchanged() async throws {
        let fixture = try await launch()
        let before = try await fixture.transport.call("fixture.channels.trace")
        let store = ChannelReconciliationStore(maxPolls: 0)
        store.configure(fixture.client)
        await store.prepare(chatID: 42, revision: 1, enabled: true)
        XCTAssertNotNil(store.review)
        await store.submit()
        let ticketDeadline = ContinuousClock.now.advanced(by: .seconds(5))
        while store.approval == nil, ContinuousClock.now < ticketDeadline {
            await store.refresh()
            try await Task.sleep(for: .milliseconds(15))
        }
        XCTAssertTrue(store.canDecide)
        await store.decide(allow: false)
        let denied = try await settle(fixture, XCTUnwrap(store.operation))
        XCTAssertEqual(denied.phase, "denied")
        await store.refresh()
        XCTAssertNil(store.approval)
        XCTAssertFalse(store.canDecide)
        await store.prepare(chatID: 42, revision: 1, enabled: true)
        await store.submit()
        await store.cancel()
        let cancelled = try await settle(fixture, XCTUnwrap(store.operation))
        XCTAssertEqual(cancelled.phase, "cancelled")
        await store.refresh()
        XCTAssertNil(store.approval)
        let after = try await fixture.transport.call("fixture.channels.trace")
        XCTAssertEqual(after["telegram_file_sha256"], before["telegram_file_sha256"])
        assertNoReplay(before, after)
        XCTAssertEqual(after["accepted"].integer, 1) // Only the explicit synthetic denial.
        await store.close()
    }

    @MainActor
    func testRevokedCentralTicketCannotEnableTheRestoredRoute() async throws {
        let fixture = try await launch()
        let before = try await fixture.transport.call("fixture.channels.trace")
        let review = try await fixture.client.review(chatID: 42, revision: 1, enabled: true)
        let operation = try await fixture.client.submit(review)
        let approval = try await ticket(fixture, review: review, operation: operation)
        _ = try await fixture.transport.call("fixture.channels.revoke")
        let staleAccepted = try await fixture.client.decide(approval, review: review,
                                                          operation: operation, allow: true)
        XCTAssertFalse(staleAccepted)
        let result = try await settle(fixture, operation)
        XCTAssertTrue(["denied", "cancelled"].contains(result.phase))
        let after = try await fixture.transport.call("fixture.channels.trace")
        XCTAssertEqual(after["telegram_file_sha256"], before["telegram_file_sha256"])
        assertNoReplay(before, after)
        XCTAssertEqual(after["accepted"].integer, 0)
    }
}
