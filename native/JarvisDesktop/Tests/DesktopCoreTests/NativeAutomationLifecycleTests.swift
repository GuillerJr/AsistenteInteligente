import Foundation
import XCTest
@testable import DesktopCore

/// Real signed RPC with deliberately delayed registration/retirement replies.
/// No model, browser page, AX input, TCC request or owner data is involved.
@MainActor
final class NativeAutomationLifecycleTests: XCTestCase {
    private struct Fixture {
        let root: String
        let process: FixtureProcess
        let client: LocalRPCClient
        let host: NativeAutomationHost
    }

    private func fixture() async throws -> Fixture {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para probar el RPC firmado.")
        }
        let root = "/private/tmp/jarvis-native-lifecycle-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-c", Self.script, root]
        process.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH":
            [source, environment["JARVIS_DESKTOP_DEPENDENCIES"]].compactMap { $0 }.joined(separator: ":"),
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let owner = try startFixtureProcess(process, directory: root)
        try await ServiceBootstrap.waitForHealth(stateDirectory: root + "/profile", timeout: 8) { process.isRunning }
        let host = NativeAutomationHost()
        host.configure(LocalRPCClient(stateDirectory: root + "/profile"))
        let client = LocalRPCClient(stateDirectory: root + "/profile")
        addTeardownBlock { _ = await host.stop(); await client.close() }
        return Fixture(root: root, process: owner, client: client, host: host)
    }

    private func trace(_ fixture: Fixture) async throws -> RPCValue {
        try await fixture.client.call("fixture.native.lifecycle")
    }

    private func wait(_ fixture: Fixture, field: String, count: Int) async throws {
        let deadline = ContinuousClock.now.advanced(by: .seconds(5))
        while ContinuousClock.now < deadline {
            if try await trace(fixture)[field].integer == count { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.unavailable
    }

    private func finish(_ fixture: Fixture) async throws {
        let closed = await fixture.host.stop(); XCTAssertTrue(closed)
        await fixture.client.close()
        let result = await fixture.process.stop(); XCTAssertEqual(result, .stopped)
    }

    func testConcurrentStartsShareAttachAndConfigureCannotRetargetInFlight() async throws {
        let fixture = try await fixture(), host = fixture.host
        let first = Task { await host.start() }
        try await wait(fixture, field: "attach", count: 1)
        XCTAssertTrue(host.lifecycleBusy)
        host.configure(LocalRPCClient(stateDirectory: fixture.root + "/not-a-service"),
            testOrigins: ["http://127.0.0.1:54321"])
        XCTAssertFalse(host.testMode, "A pending registration cannot be reconfigured into QA mode")
        let second = Task { await host.start() }
        await first.value; await second.value
        XCTAssertTrue(host.connected, host.errorMessage ?? "registration failed")
        XCTAssertFalse(host.lifecycleBusy)
        let value = try await trace(fixture)
        XCTAssertEqual(value["attach"].integer, 1)
        XCTAssertEqual(value["detach"].integer, 0)
        try await finish(fixture)
    }

    func testConcurrentStopsShareDetachAndItsConfirmedResult() async throws {
        let fixture = try await fixture(), host = fixture.host
        await host.start(); XCTAssertTrue(host.connected)
        let first = Task { await host.stop() }
        try await wait(fixture, field: "detach", count: 1)
        XCTAssertTrue(host.lifecycleBusy)
        let second = Task { await host.stop() }
        let one = await first.value, two = await second.value
        XCTAssertTrue(one); XCTAssertTrue(two)
        XCTAssertFalse(host.connected); XCTAssertFalse(host.lifecycleBusy)
        let value = try await trace(fixture)
        XCTAssertEqual(value["detach"].integer, 1)
        XCTAssertEqual(value["worker_attached"].bool, false)
        await host.start(); XCTAssertTrue(host.connected, "A duplicate stop must not orphan the broker")
        try await finish(fixture)
    }

    func testConcurrentReconnectsShareOneRetirementAndNewRegistration() async throws {
        let fixture = try await fixture(), host = fixture.host
        await host.start(); XCTAssertTrue(host.connected)
        let first = Task { await host.reconnect() }
        try await wait(fixture, field: "detach", count: 1)
        let second = Task { await host.reconnect() }
        await first.value; await second.value
        XCTAssertTrue(host.connected, host.errorMessage ?? "reconnect failed")
        XCTAssertFalse(host.lifecycleBusy)
        let value = try await trace(fixture)
        XCTAssertEqual(value["attach"].integer, 2)
        XCTAssertEqual(value["detach"].integer, 1)
        try await finish(fixture)
    }

    func testStopDuringAttachRetainsTheAcknowledgementAndRetiresThatHost() async throws {
        let fixture = try await fixture(), host = fixture.host
        let start = Task { await host.start() }
        try await wait(fixture, field: "attach", count: 1)
        let stop = Task { await host.stop() }
        await start.value
        let closed = await stop.value; XCTAssertTrue(closed)
        XCTAssertFalse(host.connected); XCTAssertFalse(host.lifecycleBusy)
        let value = try await trace(fixture)
        XCTAssertEqual(value["attach"].integer, 1)
        XCTAssertEqual(value["detach"].integer, 1)
        XCTAssertEqual(value["worker_attached"].bool, false)
        await host.start(); XCTAssertTrue(host.connected)
        try await finish(fixture)
    }

    func testStopDuringReconnectPreventsTheReplacementRegistration() async throws {
        let fixture = try await fixture(), host = fixture.host
        await host.start(); XCTAssertTrue(host.connected)
        let reconnect = Task { await host.reconnect() }
        try await wait(fixture, field: "detach", count: 1)
        let stop = Task { await host.stop() }
        await reconnect.value
        let closed = await stop.value; XCTAssertTrue(closed)
        XCTAssertFalse(host.connected); XCTAssertFalse(host.lifecycleBusy)
        let value = try await trace(fixture)
        XCTAssertEqual(value["attach"].integer, 1)
        XCTAssertEqual(value["detach"].integer, 1)
        await host.start(); XCTAssertTrue(host.connected)
        try await finish(fixture)
    }

    private static let script = #"""
import asyncio,pathlib,signal,sys
from jarvis_core.service import JarvisService
class Peer:
 async def account_status(self):return {'connected':False,'mode':'chatgpt'}
 async def chat(self,*args,**kwargs):raise RuntimeError('No model is used in this lifecycle fixture')
 async def aclose(self):pass
async def main():
 root=pathlib.Path(sys.argv[1]);service=JarvisService(root/'profile',Peer())
 counts={'attach':0,'detach':0}
 for name in counts:
  method='automation.'+name;handler=service.server._handlers[method]
  async def delayed(value,name=name,handler=handler):
   counts[name]+=1
   await asyncio.sleep(.3)
   return await handler(value)
  service.server._handlers[method]=delayed
 async def trace(value):return {**counts,'worker_attached':service.native_broker.host_id is not None}
 service.server._handlers['fixture.native.lifecycle']=trace
 done=asyncio.Event();asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
 try:await service.start();await done.wait()
 finally:await service.aclose()
asyncio.run(main())
"""#
}
