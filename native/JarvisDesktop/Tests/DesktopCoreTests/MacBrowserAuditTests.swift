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
            XCTAssertEqual(fixture.reportedDOMBeforeGate(), "JARVIS DOM ANTES",
                "The page read its real DOM mutation before entering the held synchronous request")
            await executor.cancel(operationID: try XCTUnwrap(args["operation_id"]?.string))
            try await Task.sleep(nanoseconds: 100_000_000)
            XCTAssertFalse(settled, "Cancellation must retain custody until WebKit's actual callback")
            let pending = try await executor.execute("browser_status", arguments: [:])
            XCTAssertEqual(pending["active"].bool, true, "The JS evaluation is still owned while its fixture gate is closed")
            try fixture.releaseGate()
            do { _ = try await task.value; XCTFail("A cancelled action must remain uncertain") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .uncertainEffect) }
            XCTAssertTrue(settled)
            let observation = try await executor.execute("browser_observe", arguments: operation([
                "tab_id": opened["tab_id"], "expected_origin": .string(fixture.origin)]))
            let text = try XCTUnwrap(observation["text"].string)
            XCTAssertTrue(text.contains("JARVIS DOM ANTES"))
            XCTAssertTrue(text.contains("JARVIS DOM DESPUES"), "The dispatched handler mutated the DOM after cancellation")
            XCTAssertTrue(text.contains("JARVIS EFECTOS 1"), "The uncertain effect ran once, independently of any aborted fetch")
            XCTAssertFalse(text.contains("JARVIS GATE NO LIBERADO"))
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
            try fixture.releaseGate()
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
        process.arguments = ["-I", "-u", "-c", LoopbackHTTPFixture.script(Self.script), directory.path]
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
    func reportedDOMBeforeGate() -> String? {
        guard let data = try? Data(contentsOf: directory.appendingPathComponent("gate-entered.json")),
              let value = try? JSONDecoder().decode([String: String].self, from: data) else { return nil }
        return value["before"]
    }
    func releaseGate() throws { try Data("release".utf8).write(to: directory.appendingPathComponent("gate.release")) }
    func stop() async -> FixtureProcess.StopResult {
        try? releaseGate()
        let result = await fixture.stop()
        if result == .stopped { try? FileManager.default.removeItem(at: directory) }
        return result
    }

    static let script = #"""
import http.server,json,pathlib,sys,time,urllib.parse
root=pathlib.Path(sys.argv[1])
page=b'''<html><body>
<button onclick="slowEffect(this)">Efecto lento</button>
<p id="before">JARVIS DOM PENDIENTE</p><p id="after"></p><p id="count"></p>
<script>
function slowEffect(button) {
 const before=document.getElementById('before');
 before.textContent='JARVIS DOM ANTES';
 const deadline=performance.now()+5000;
 let released=false;
 // stopLoading may abort the outstanding read. Re-enter only this inert,
 // fixture-owned barrier; the click and DOM effect are never dispatched twice.
 for(let attempt=0;attempt<8 && performance.now()<deadline;attempt++) {
  try {
   const gate=new XMLHttpRequest();
   gate.open('GET','/gate?before='+encodeURIComponent(before.textContent),false);
   gate.send();
   if(gate.status===200 && gate.responseText==='RELEASE') {released=true;break;}
  } catch (_) {}
 }
 document.getElementById('after').textContent=released?'JARVIS DOM DESPUES':'JARVIS GATE NO LIBERADO';
 button.dataset.effects=String(Number(button.dataset.effects||'0')+1);
 document.getElementById('count').textContent='JARVIS EFECTOS '+button.dataset.effects;
}
</script></body></html>'''
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  with (root/'requests.jsonl').open('a') as f:f.write(json.dumps(self.path)+'\n')
  if self.path.startswith('/gate?'):
   before=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('before',[''])[0]
   try:
    with (root/'gate-entered.json').open('x') as f:json.dump({'before':before},f)
   except FileExistsError:pass
   with (root/'requests.jsonl').open('a') as f:f.write(json.dumps('/started')+'\n')
   # This is a deadline, not an artificial wait: the native test opens the gate
   # immediately after checking custody. No asynchronous effect fetch is needed.
   deadline=time.monotonic()+5
   while not (root/'gate.release').exists() and time.monotonic()<deadline:time.sleep(.005)
   payload=b'RELEASE' if (root/'gate.release').exists() else b'CLOSED'
  else:payload=page if self.path=='/' else b'OK'
  self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers()
  try:self.wfile.write(payload)
  except (BrokenPipeError,ConnectionResetError):pass
 def log_message(self,*args):pass
server=LoopbackHTTPServer(('127.0.0.1',0),Handler)
server.daemon_threads=True
print(server.server_port,flush=True)
server.serve_forever()
"""#
}
