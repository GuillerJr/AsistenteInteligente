import Foundation
import XCTest
@testable import DesktopCore

/// Opt-in qualification against an actual Chrome process created by this test.
/// The owner browser and its profiles/targets are never enumerated or attached.
@MainActor
final class BrowserAutomationChromeTests: XCTestCase {
    private func operation(_ arguments: [String: RPCValue]) -> [String: RPCValue] {
        var value = arguments
        value["operation_id"] = .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())
        return value
    }

    func testRealChromeExactTargetActionsStaleReferencesAndDetach() async throws {
        guard ProcessInfo.processInfo.environment["JARVIS_REAL_CHROME"] == "1" else {
            throw XCTSkip("Chrome real es opt-in: JARVIS_REAL_CHROME=1, siempre con perfil sintético nuevo")
        }
        let executable = ProcessInfo.processInfo.environment["JARVIS_REAL_CHROME_PATH"] ??
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        guard FileManager.default.isExecutableFile(atPath: executable) else {
            throw XCTSkip("No hay Chrome ejecutable en la ruta de calificación explícita")
        }
        let fixture = try BrowserRealChromeFixture(chromePath: executable)
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            try await fixture.startChrome()
            let created = try await fixture.createTarget(path: "/page")
            let attached = try await executor.execute("browser_attach", arguments: operation([
                "endpoint": .string(fixture.endpoint), "target_id": .string(created)]))
            XCTAssertEqual(attached["mode"].string, "attached")
            XCTAssertEqual(attached["origin"].string, fixture.origin)
            XCTAssertEqual(attached["network_scope"].string, "existing_browser_owner_network")
            XCTAssertTrue(attached["text"].string?.contains("CHROME REAL JARVIS") == true)
            let tab = try XCTUnwrap(attached["tab_id"].string)
            let input = try XCTUnwrap(attached["elements"].array?.first { $0["label"].string == "Entrada Chrome propia" })
            let filled = try await executor.execute("browser_action", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "snapshot_id": attached["snapshot_id"], "ref": input["ref"],
                "action": .string("fill"), "text": .string("JARVIS CHROME REAL 5284")]))
            XCTAssertEqual(filled["verified"].bool, true)
            let refreshed = filled["observation"]
            let button = try XCTUnwrap(refreshed["elements"].array?.first { $0["label"].string == "Aplicar Chrome" })
            do {
                _ = try await executor.execute("browser_action", arguments: operation([
                    "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                    "snapshot_id": attached["snapshot_id"], "ref": input["ref"],
                    "action": .string("fill"), "text": .string("NO DEBE ESCRIBIR")]))
                XCTFail("Una captura anterior no debe reutilizarse en Chrome")
            } catch { XCTAssertEqual(error as? BrowserAutomationError, .staleSnapshot) }
            let clicked = try await executor.execute("browser_action", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "snapshot_id": refreshed["snapshot_id"], "ref": button["ref"], "action": .string("click")]))
            XCTAssertEqual(clicked["action_dispatched"].bool, true)
            XCTAssertEqual(clicked["verified"].bool, false)
            let deadline = Date().addingTimeInterval(3)
            while fixture.effects().isEmpty, Date() < deadline { try await Task.sleep(for: .milliseconds(20)) }
            XCTAssertEqual(fixture.effects(), ["JARVIS CHROME REAL 5284"])
            do {
                _ = try await executor.execute("browser_observe", arguments: operation([
                    "tab_id": .string(tab), "expected_origin": .string("https://example.com")]))
                XCTFail("El origen ajeno debe rechazarse antes de entregar DOM")
            } catch { XCTAssertEqual(error as? BrowserAutomationError, .staleSnapshot) }
            let navigated = try await executor.execute("browser_navigate", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "url": .string(fixture.origin + "/next")]))
            XCTAssertEqual(navigated["url"].string, fixture.origin + "/next")
            XCTAssertTrue(navigated["text"].string?.contains("SEGUNDA PAGINA PROPIA") == true)
            let closed = try await executor.execute("browser_close", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin)]))
            XCTAssertEqual(closed["closed"].bool, false)
            XCTAssertEqual(closed["detached"].bool, true)
            await executor.reset()
            // Independent second CDP connection checks exactly the same known target.
            // There is no /json/list, Target.getTargets or owner profile discovery.
            let check = try BrowserAutomationCDP(endpoint: URL(string: fixture.endpoint)!, targetID: created)
            let info = try await check.call("Target.getTargetInfo", params: ["targetId": .string(created)])
            check.close()
            XCTAssertEqual(info["targetInfo"]["targetId"].string, created)
            XCTAssertEqual(info["targetInfo"]["url"].string, fixture.origin + "/next")
            XCTAssertEqual(fixture.effects().count, 1)
            let stopped = await fixture.stop()
            XCTAssertEqual(stopped.chrome, .stopped)
            XCTAssertEqual(stopped.http, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }
}

@MainActor
private final class BrowserRealChromeFixture {
    let origin: String
    private(set) var endpoint = ""
    private let directory: URL
    private let http: FixtureProcess
    private let chrome: FixtureProcess
    private let process: Process
    private var browserSocketPath: String?

    init(chromePath: String) throws {
        directory = URL(fileURLWithPath: "/private/tmp/jarvis-real-chrome-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        let server = Process(), output = Pipe()
        server.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["JARVIS_DESKTOP_PYTHON"] ?? "/usr/bin/python3")
        server.arguments = ["-I", "-u", "-c", LoopbackHTTPFixture.script(Self.serverScript), directory.path]
        server.environment = ["PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"]
        server.standardOutput = output; server.standardError = FileHandle.nullDevice
        http = FixtureProcess(server); try http.run()
        var line = Data()
        while !line.contains(10), line.count < 128 {
            let next = output.fileHandleForReading.readData(ofLength: 1)
            if next.isEmpty { throw BrowserAutomationError.unavailable }; line.append(next)
        }
        guard let port = Int(String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)) else {
            throw BrowserAutomationError.unavailable
        }
        origin = "http://127.0.0.1:\(port)"
        process = Process()
        process.executableURL = URL(fileURLWithPath: chromePath)
        process.arguments = ["--headless=new", "--user-data-dir=" + directory.appendingPathComponent("profile").path,
            "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
            "--disable-extensions", "--disable-component-update", "--metrics-recording-only",
            "--disable-crashpad-for-testing", "--disable-updater-scheduler",
            "--disable-features=MediaRouter,OptimizationHints", "--password-store=basic", "--use-mock-keychain",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", "about:blank"]
        process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        chrome = FixtureProcess(process)
    }

    func startChrome() async throws {
        try chrome.run()
        let activePort = directory.appendingPathComponent("profile/DevToolsActivePort")
        let deadline = Date().addingTimeInterval(10)
        while Date() < deadline {
            if let text = try? String(contentsOf: activePort, encoding: .utf8) {
                let lines = text.split(separator: "\n")
                if lines.count == 2, let port = Int(lines[0]), (1...65535).contains(port),
                   lines[1].hasPrefix("/devtools/browser/") {
                    endpoint = "http://127.0.0.1:\(port)"; browserSocketPath = String(lines[1]); return
                }
            }
            try await Task.sleep(for: .milliseconds(40))
        }
        throw BrowserAutomationError.unavailable
    }

    func createTarget(path: String) async throws -> String {
        let encoded = (origin + path).addingPercentEncoding(withAllowedCharacters: .alphanumerics)!
        var request = URLRequest(url: URL(string: endpoint + "/json/new?" + encoded)!)
        request.httpMethod = "PUT"; request.timeoutInterval = 5
        let session = URLSession(configuration: .ephemeral); defer { session.invalidateAndCancel() }
        let (data, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200, data.count < 16_384,
              let value = try? JSONDecoder().decode(RPCValue.self, from: data),
              let id = value["id"].string, value["type"].string == "page",
              id.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil else {
            throw BrowserAutomationError.unavailable
        }
        // /json/new returned this explicitly-created target. Allow its own page to finish loading.
        let deadline = Date().addingTimeInterval(3)
        while !FileManager.default.fileExists(atPath: directory.appendingPathComponent("page-loaded").path), Date() < deadline {
            try await Task.sleep(for: .milliseconds(20))
        }
        return id
    }

    func effects() -> [String] {
        let text = (try? String(contentsOf: directory.appendingPathComponent("effects.jsonl"), encoding: .utf8)) ?? ""
        return text.split(separator: "\n").compactMap { try? JSONDecoder().decode(String.self, from: Data($0.utf8)) }
    }

    func stop() async -> (chrome: FixtureProcess.StopResult, http: FixtureProcess.StopResult) {
        if let browserSocketPath, !endpoint.isEmpty {
            let url = URL(string: endpoint.replacingOccurrences(of: "http:", with: "ws:") + browserSocketPath)!
            let session = URLSession(configuration: .ephemeral), socket = session.webSocketTask(with: url)
            socket.resume()
            // Wait for the owned browser's acknowledgement/connection close.
            // Cancelling immediately after send can discard the close command.
            await withTaskGroup(of: Void.self) { group in
                group.addTask {
                    do {
                        try await socket.send(.string(#"{"id":1,"method":"Browser.close"}"#))
                        _ = try await socket.receive()
                    } catch { /* Closing Chrome can close the socket before its ACK. */ }
                }
                group.addTask {
                    do { try await Task.sleep(for: .seconds(2)) }
                    catch { return }
                    socket.cancel(with: .goingAway, reason: nil)
                }
                _ = await group.next()
                group.cancelAll()
                socket.cancel(with: .goingAway, reason: nil)
            }
            session.invalidateAndCancel()
            // Chrome acknowledges before finishing its asynchronous shutdown.
            // Do not interrupt that close with the fallback SIGTERM. The fixture's
            // final waitpid check, rather than this grace period, proves exit.
            try? await Task.sleep(for: .seconds(1))
            self.browserSocketPath = nil
        }
        let chromeResult = await chrome.stop(), httpResult = await http.stop()
        if chromeResult == .stopped, httpResult == .stopped { try? FileManager.default.removeItem(at: directory) }
        return (chromeResult, httpResult)
    }

    static let serverScript = #"""
import http.server,json,pathlib,sys
root=pathlib.Path(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=='/page':
   (root/'page-loaded').write_text('owned')
   payload=b'''<html><body><h1>CHROME REAL JARVIS</h1><input aria-label="Entrada Chrome propia"><button onclick="fetch('/effect',{method:'POST',body:document.querySelector('input').value})">Aplicar Chrome</button></body></html>'''
  elif self.path=='/next':payload=b'<html><body>SEGUNDA PAGINA PROPIA</body></html>'
  else:payload=b'OWNED FIXTURE'
  self.send_response(200);self.send_header('Content-Type','text/html;charset=utf-8');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def do_POST(self):
  size=int(self.headers.get('Content-Length','0'))
  if self.path!='/effect' or size>8192:self.send_error(400);return
  text=self.rfile.read(size).decode('utf-8')
  with (root/'effects.jsonl').open('a') as f:f.write(json.dumps(text)+'\n')
  payload=b'OK';self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def log_message(self,*args):pass
server=LoopbackHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
print(server.server_port,flush=True);server.serve_forever()
"""#
}
