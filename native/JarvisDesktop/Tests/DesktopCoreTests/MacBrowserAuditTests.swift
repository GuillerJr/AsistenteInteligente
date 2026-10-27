import AppKit
import Foundation
import XCTest
@testable import DesktopCore

@MainActor
final class MacBrowserAuditTests: XCTestCase {
    private func operation(_ args: [String: RPCValue]) -> [String: RPCValue] {
        var value = args
        value["operation_id"] = .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())
        return value
    }

    func testOwnAppApprovalSettingsAndChatWindowsAreNeverAutomationTargets() throws {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 200, height: 120),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        window.title = "Navegador privado de Jarvis"
        let production = MacAutomationAXDriver()
        let synthetic = MacAutomationAXDriver(allowOwnTestWindow: true)
        for id in ["jarvis.main", "jarvis.approval", "jarvis.settings", "jarvis.browser.", ""] {
            window.identifier = NSUserInterfaceItemIdentifier(id)
            XCTAssertFalse(production.isOwnWindowAdmitted(window), id)
            XCTAssertFalse(synthetic.isOwnWindowAdmitted(window), id)
        }
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.automation.fixture")
        XCTAssertFalse(production.isOwnWindowAdmitted(window))
        XCTAssertTrue(synthetic.isOwnWindowAdmitted(window))
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.browser.0123456789abcdef")
        XCTAssertTrue(production.isOwnWindowAdmitted(window))
        XCTAssertTrue(synthetic.isOwnWindowAdmitted(window))
        XCTAssertFalse(MacAutomationAXDriver.allowsOwnWindow(identifier: window.identifier?.rawValue,
            allowTestWindow: true, hasModalWindow: true, hasSheet: false))
        XCTAssertFalse(MacAutomationAXDriver.allowsOwnWindow(identifier: window.identifier?.rawValue,
            allowTestWindow: true, hasModalWindow: false, hasSheet: true))
    }

    func testCancelDoesNotAcknowledgeCleanupWhileDispatchedJavaScriptStillRuns() async throws {
        let fixture = try MacBrowserAuditFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let opened = try await executor.execute("browser_open", arguments: operation(["url": .string(fixture.origin + "/")]))
            let button = try XCTUnwrap(opened["elements"].array?.first { $0["label"].string == "Efecto lento" })
            let args = operation(["tab_id": opened["tab_id"], "expected_origin": .string(fixture.origin),
                "snapshot_id": opened["snapshot_id"], "ref": button["ref"], "action": .string("click")])
            var settled = false
            let task = Task { @MainActor in
                defer { settled = true }
                return try await executor.execute("browser_action", arguments: args)
            }
            let deadline = Date().addingTimeInterval(5)
            while !fixture.hasRequest("/started"), Date() < deadline {
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            XCTAssertTrue(fixture.hasRequest("/started"), "The approved own fixture handler must have started")
            await executor.cancel(operationID: try XCTUnwrap(args["operation_id"]?.string))
            try await Task.sleep(nanoseconds: 100_000_000)
            XCTAssertFalse(settled, "Cancellation must retain custody until WebKit's actual callback")
            do { _ = try await task.value; XCTFail("A cancelled action must remain uncertain") }
            catch { XCTAssertTrue(error is BrowserAutomationError) }
            XCTAssertTrue(settled)
            try await Task.sleep(nanoseconds: 200_000_000)
            XCTAssertTrue(fixture.hasRequest("/effect"), "The already-dispatched site handler produced its uncertain effect")
            do { _ = try await executor.execute("browser_action", arguments: args); XCTFail("The uncertain operation is consumed") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .alreadySubmitted) }
            await executor.reset()
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testResetWaitsForOwnedEvaluationAndKeepsOperationConsumed() async throws {
        let fixture = try MacBrowserAuditFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let opened = try await executor.execute("browser_open", arguments: operation(["url": .string(fixture.origin + "/")]))
            let button = try XCTUnwrap(opened["elements"].array?.first { $0["label"].string == "Efecto lento" })
            let args = operation(["tab_id": opened["tab_id"], "expected_origin": .string(fixture.origin),
                "snapshot_id": opened["snapshot_id"], "ref": button["ref"], "action": .string("click")])
            let task = Task { try await executor.execute("browser_action", arguments: args) }
            let deadline = Date().addingTimeInterval(5)
            while !fixture.hasRequest("/started"), Date() < deadline {
                try await Task.sleep(nanoseconds: 10_000_000)
            }
            XCTAssertTrue(fixture.hasRequest("/started"))
            var resetSettled = false
            let reset = Task { await executor.reset(); resetSettled = true }
            try await Task.sleep(nanoseconds: 100_000_000)
            XCTAssertFalse(resetSettled, "Closing the native window cannot fabricate a JS completion")
            do { _ = try await task.value; XCTFail("Reset invalidates the running operation") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .uncertainEffect) }
            await reset.value
            let status = try await executor.execute("browser_status", arguments: [:])
            XCTAssertEqual(status["tabs"].array, [])
            XCTAssertEqual(status["active"].bool, false)
            do { _ = try await executor.execute("browser_action", arguments: args); XCTFail("No replay after reset") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .alreadySubmitted) }
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }
}

@MainActor
private final class MacBrowserAuditFixture {
    let origin: String
    private let directory: URL
    private let fixture: FixtureProcess

    init() throws {
        directory = URL(fileURLWithPath: "/private/tmp/jarvis-browser-audit-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        let process = Process(), output = Pipe()
        process.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["JARVIS_DESKTOP_PYTHON"] ?? "/usr/bin/python3")
        process.arguments = ["-I", "-u", "-c", Self.script, directory.path]
        process.environment = ["PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"]
        process.standardOutput = output; process.standardError = FileHandle.nullDevice
        fixture = FixtureProcess(process)
        try fixture.run()
        var line = Data()
        while !line.contains(10), line.count < 128 {
            let next = output.fileHandleForReading.readData(ofLength: 1)
            if next.isEmpty { throw BrowserAutomationError.unavailable }; line.append(next)
        }
        guard let port = Int(String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw BrowserAutomationError.unavailable
        }
        origin = "http://127.0.0.1:\(port)"
    }

    func hasRequest(_ path: String) -> Bool {
        let text = (try? String(contentsOf: directory.appendingPathComponent("requests.jsonl"), encoding: .utf8)) ?? ""
        return text.split(separator: "\n").contains { line in
            (try? JSONDecoder().decode(String.self, from: Data(line.utf8))) == path
        }
    }
    func stop() async -> FixtureProcess.StopResult { await fixture.stop() }

    static let script = #"""
import http.server,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  with (root/'requests.jsonl').open('a') as f:f.write(json.dumps(self.path)+'\n')
  payload=b'''<html><body><button onclick="fetch('/started');const until=Date.now()+1500;while(Date.now()<until){}fetch('/effect')">Efecto lento</button></body></html>''' if self.path=='/' else b'OK'
  self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers()
  try:self.wfile.write(payload)
  except (BrokenPipeError,ConnectionResetError):pass
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
server.daemon_threads=True
print(server.server_port,flush=True)
server.serve_forever()
"""#
}
