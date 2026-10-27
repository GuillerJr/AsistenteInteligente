import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// The reader, queue and approvals use signed RPC and real own SQLite. The
/// provider, held settlement, decisions and workspace are synthetic fixtures.
final class ConversationQueueIntegrationTests: XCTestCase {
    private static let script = """
    import asyncio, signal, sys
    from pathlib import Path
    from uuid import uuid4
    from jarvis_core.service import JarvisService
    class Peer:
      def __init__(self):
        self.calls=[]; self.first=asyncio.Event(); self.third=asyncio.Event()
      async def account_status(self): return {'connected':True,'mode':'chatgpt'}
      async def chat(self,message,*,on_thread,on_tool_call,**options):
        self.calls.append(message)
        on_thread(options.get('thread_id') or 'synthetic_'+uuid4().hex)
        if message=='PRIMERO_ESPERA': await self.first.wait()
        elif message in ('SEGUNDO_APROBACION','INCIERTO'):
          await on_tool_call('workspace_write',{'path':'cola.txt','text':'EFECTO UNA VEZ'})
        elif message=='TERCERO_ESPERA': await self.third.wait()
        return {'answer':'Reporte propio '+message}
      async def aclose(self): self.first.set(); self.third.set()
    async def main():
      root=Path(sys.argv[1]); peer=Peer(); service=JarvisService(root,peer)
      sql_release=asyncio.Event(); waiting={'sql':False}
      original_settle=service._settle
      async def hold_sql(ref,handle):
        result=await handle.await_settled()
        rows=service.store.get_run_messages(ref,handle.run_id)
        if result.status=='completed' and any(row.role=='user' and row.text=='SEGUNDO_APROBACION' for row in rows):
          waiting['sql']=True
          await sql_release.wait()
        await original_settle(ref,handle)
      service._settle=hold_sql
      original_execute=service.workspace_tools._execute
      async def uncertain(name,arguments,cancellation):
        result=await original_execute(name,arguments,cancellation)
        current=service._active_ref.get()
        messages=service.store.list_recent_messages(current)
        if any(row.role=='user' and row.text=='INCIERTO' for row in messages):
          raise ConnectionError('synthetic lost ACK after the own write')
        return result
      service.workspace_tools._execute=uncertain
      async def trace(params): return {'calls':peer.calls,'waiting_sql':waiting['sql']}
      async def release_first(params): peer.first.set(); return {'ok':True}
      async def release_sql(params): sql_release.set(); return {'ok':True}
      async def release_third(params): peer.third.set(); return {'ok':True}
      service.server._handlers.update({'fixture.queue.trace':trace,
        'fixture.queue.first':release_first,'fixture.queue.sql':release_sql,
        'fixture.queue.third':release_third})
      done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
      try: await service.start(); await done.wait()
      finally: peer.first.set(); peer.third.set(); sql_release.set(); await service.aclose()
    asyncio.run(main())
    """

    private func identity() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }

    @MainActor
    private func directory() throws -> String {
        let parent = "/private/tmp/jarvis-conversation-queue-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: parent, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: parent) }
        return parent + "/profile"
    }

    @MainActor
    private func startHost(_ profile: String) async throws -> FixtureProcess {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para integrar Swift con Python.")
        }
        let logs = "/private/tmp/jarvis-conversation-queue-child-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let process = Process(); process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-c", Self.script, profile]
        var paths = source
        if let dependencies = environment["JARVIS_DESKTOP_DEPENDENCIES"] { paths += ":" + dependencies }
        process.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": paths,
                               "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        process.standardOutput = FileHandle.nullDevice
        let fd = Darwin.open(logs + "/stderr.log", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw RPCFailure.configuration }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        process.standardError = handle; addTeardownBlock { try? handle.close() }
        let owner = try startFixtureProcess(process, directory: logs)
        try await ServiceBootstrap.waitForHealth(stateDirectory: profile, timeout: 8) { process.isRunning }
        return owner
    }

    @MainActor
    private func createSession(_ transport: LocalRPCClient) async throws -> SessionScope {
        let value = try await transport.call("sessions.create", params: ["title": .string("Cola propia")])
        return SessionScope(sessionID: try XCTUnwrap(value["session"]["session_id"].string), temporary: false)
    }

    private func runParams(_ scope: SessionScope, runID: String) -> [String: RPCValue] {
        var value = scope.params; value["run_id"] = .string(runID); return value
    }

    @MainActor
    private func submit(_ transport: LocalRPCClient, _ scope: SessionScope, _ message: String,
                        queued: Bool) async throws -> String {
        let request = identity(); var params = scope.params
        params["request_id"] = .string(request); params["message"] = .string(message)
        let value = try await transport.call(queued ? "runs.enqueue" : "runs.start", params: params)
        _ = try RunInputReply(queued: value, requestID: request)
        return request
    }

    @MainActor
    private func operation(_ transport: LocalRPCClient, _ scope: SessionScope,
                           runID: String) async throws -> ApprovalOperation {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let value = try await transport.call("approvals.list", params: runParams(scope, runID: runID))
            if let row = value["approvals"].array?.first {
                return try ApprovalOperation(row, expectedScope: scope, expectedRunID: runID)
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor
    private func terminal(_ transport: LocalRPCClient, _ scope: SessionScope,
                          runID: String) async throws -> ConversationRun {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            let value = try await transport.call("runs.get", params: runParams(scope, runID: runID))
            let run = try ConversationRun(value["run"], expectedID: runID)
            if !run.active { return run }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    @MainActor
    func testReaderFollowsEarliestActiveAndApprovalInsteadOfNewestQueued() async throws {
        let profile = try directory(), host = try await startHost(profile)
        let transport = LocalRPCClient(stateDirectory: profile), scope = try await createSession(transport)
        let reader = ConversationReader(transport: transport)
        let first = try await submit(transport, scope, "PRIMERO_ESPERA", queued: false)
        let second = try await submit(transport, scope, "SEGUNDO_APROBACION", queued: true)
        let third = try await submit(transport, scope, "TERCERO_ESPERA", queued: true)
        let before = try await reader.read(scope)
        XCTAssertEqual(before.latestRun?.id, first); XCTAssertTrue(before.latestRun?.active == true)
        _ = try await transport.call("fixture.queue.first")
        let approval = try await operation(transport, scope, runID: second)
        XCTAssertEqual(approval.runID, second)
        let waiting = try await reader.read(scope)
        XCTAssertEqual(waiting.latestRun?.id, second)
        XCTAssertEqual(waiting.latestRun?.status, "waiting_approval")
        XCTAssertFalse(FileManager.default.fileExists(atPath: profile + "/workspace/cola.txt"))
        let allowed = try await transport.call("approvals.decide", params: approval.decision(allow: true))
        XCTAssertEqual(allowed["accepted"].bool, true)
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        var trace: RPCValue = .null
        while ContinuousClock.now < deadline {
            trace = try await transport.call("fixture.queue.trace")
            if trace["waiting_sql"].bool == true { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertEqual(trace["waiting_sql"].bool, true)
        XCTAssertEqual(trace["calls"].array?.count, 2)
        let held = try await reader.read(scope)
        XCTAssertEqual(held.latestRun?.id, second); XCTAssertTrue(held.latestRun?.active == true)
        let thirdState = try await transport.call("runs.get", params: runParams(scope, runID: third))
        XCTAssertEqual(thirdState["run"]["status"].string, "queued")
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: profile + "/workspace/cola.txt")),
                       Data("EFECTO UNA VEZ".utf8))
        _ = try await transport.call("fixture.queue.sql")
        let secondFinished = try await terminal(transport, scope, runID: second)
        XCTAssertEqual(secondFinished.status, "completed"); XCTAssertEqual(secondFinished.outcome, "known")
        let nowThird = try await reader.read(scope)
        XCTAssertEqual(nowThird.latestRun?.id, third); XCTAssertTrue(nowThird.latestRun?.active == true)
        _ = try await transport.call("fixture.queue.third")
        let last = try await terminal(transport, scope, runID: third)
        XCTAssertEqual(last.status, "completed")
        let completed = try await reader.read(scope)
        XCTAssertEqual(completed.latestRun?.id, third); XCTAssertFalse(completed.latestRun?.active == true)
        await transport.close()
        let stopped = await host.stop(); XCTAssertEqual(stopped, .stopped)
    }

    @MainActor
    func testCanceledOrUnknownPredecessorNeverDispatchesItsQueuedSuccessors() async throws {
        for firstMessage in ["PRIMERO_ESPERA", "INCIERTO"] {
            let profile = try directory(), host = try await startHost(profile)
            let transport = LocalRPCClient(stateDirectory: profile), scope = try await createSession(transport)
            let first = try await submit(transport, scope, firstMessage, queued: false)
            let second = try await submit(transport, scope, "SEGUNDO_APROBACION", queued: true)
            let third = try await submit(transport, scope, "TERCERO_ESPERA", queued: true)
            let startedDeadline = ContinuousClock.now.advanced(by: .seconds(5))
            while ContinuousClock.now < startedDeadline {
                let trace = try await transport.call("fixture.queue.trace")
                if trace["calls"].array?.count == 1 { break }
                try await Task.sleep(for: .milliseconds(10))
            }
            if firstMessage == "INCIERTO" {
                let approval = try await operation(transport, scope, runID: first)
                _ = try await transport.call("approvals.decide", params: approval.decision(allow: true))
            } else {
                _ = try await transport.call("runs.cancel", params: runParams(scope, runID: first))
            }
            let stoppedFirst = try await terminal(transport, scope, runID: first)
            XCTAssertEqual(stoppedFirst.outcome, "unknown")
            for identity in [second, third] {
                let blocked = try await terminal(transport, scope, runID: identity)
                XCTAssertEqual(blocked.status, "failed"); XCTAssertEqual(blocked.errorCode, "run_preparation_failed")
                let events = try await transport.call("events.poll", params: runParams(scope, runID: identity))
                XCTAssertTrue(events["events"].array?.contains { $0["kind"].string == "queue.blocked" } == true)
            }
            let trace = try await transport.call("fixture.queue.trace")
            XCTAssertEqual(trace["calls"].array?.count, 1)
            let snapshot = try await ConversationReader(transport: transport).read(scope)
            XCTAssertEqual(snapshot.latestRun?.id, third); XCTAssertFalse(snapshot.latestRun?.active == true)
            if firstMessage == "INCIERTO" {
                XCTAssertTrue(stoppedFirst.receipts.contains(where: \.unknown))
                XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: profile + "/workspace/cola.txt")),
                               Data("EFECTO UNA VEZ".utf8))
            } else { XCTAssertFalse(FileManager.default.fileExists(atPath: profile + "/workspace/cola.txt")) }
            await transport.close()
            let stopped = await host.stop(); XCTAssertEqual(stopped, .stopped)
        }
    }

    func testQueuedReplyRequiresExactRequestIdentityAndAnActiveSubmissionState() throws {
        let request = identity()
        for status in ["queued", "running"] {
            let value: RPCValue = .object(["run_id": .string(request), "status": .string(status)])
            let parsed = try RunInputReply(queued: value, requestID: request)
            XCTAssertEqual(parsed.runID, request); XCTAssertEqual(parsed.status, status)
        }
        for status in ["completed", "failed", "cancelled", "interrupted", "unknown"] {
            XCTAssertThrowsError(try RunInputReply(queued: .object([
                "run_id": .string(request), "status": .string(status)]), requestID: request))
        }
        XCTAssertThrowsError(try RunInputReply(queued: .object([
            "run_id": .string(identity()), "status": .string("queued")]), requestID: request))
    }

    func testSteeringReplyRequiresExactRunRequestAndNoReplay() throws {
        let run = identity(), request = identity()
        for status in ["accepted", "unknown"] {
            let value: RPCValue = .object(["run_id": .string(run), "request_id": .string(request),
                                           "status": .string(status), "permits_replay": .bool(false)])
            let parsed = try RunInputReply(steering: value, runID: run, requestID: request)
            XCTAssertEqual(parsed.status, status)
        }
        let wrongValues: [RPCValue] = [
            .object(["run_id": .string(identity()), "request_id": .string(request),
                     "status": .string("accepted"), "permits_replay": .bool(false)]),
            .object(["run_id": .string(run), "request_id": .string(identity()),
                     "status": .string("accepted"), "permits_replay": .bool(false)]),
            .object(["run_id": .string(run), "request_id": .string(request),
                     "status": .string("accepted"), "permits_replay": .bool(true)]),
            .object(["run_id": .string(run), "request_id": .string(request),
                     "status": .string("completed"), "permits_replay": .bool(false)])
        ]
        for value in wrongValues {
            XCTAssertThrowsError(try RunInputReply(steering: value, runID: run, requestID: request))
        }
    }
}
