import AppKit
import Foundation
import XCTest
@testable import DesktopCore

@MainActor
final class BrowserAutomationTests: XCTestCase {
    private func operation(_ values: [String: RPCValue] = [:]) -> [String: RPCValue] {
        var args = values
        args["operation_id"] = .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())
        return args
    }

    func testPolicyDeniesSchemesCredentialsPrivateAndAmbiguousEndpoints() throws {
        let policy = BrowserAutomationPolicy()
        for raw in ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,hello", "chrome://settings",
                    "http://example.com/", "https://name:pass@example.com/", "https://localhost/",
                    "https://127.0.0.1/", "https://169.254.169.254/latest", "https://10.0.0.1/",
                    "https://example.com:8443/", "https://host.local/", "https://127.1/"] {
            XCTAssertThrowsError(try policy.validate(raw), raw)
        }
        XCTAssertEqual(try policy.validate("https://example.com/a?q=x").host, "example.com")
        XCTAssertEqual(BrowserAutomationPolicy.origin(URL(string: "https://example.com:443/a")!), "https://example.com")
        XCTAssertEqual(BrowserAutomationPolicy.origin(URL(string: "https://example.com/a")!), "https://example.com")
        XCTAssertFalse(BrowserAutomationPolicy.publicAddress("::1"))
        XCTAssertFalse(BrowserAutomationPolicy.publicAddress("::ffff:127.0.0.1"))
        XCTAssertFalse(BrowserAutomationPolicy.publicAddress("100.64.0.1"))
        XCTAssertFalse(BrowserAutomationPolicy.publicAddress("198.18.0.1"))
        XCTAssertFalse(BrowserAutomationPolicy.publicAddress("2001:db8::1"))
        XCTAssertTrue(BrowserAutomationPolicy.publicAddress("8.8.8.8"))
        XCTAssertTrue(BrowserAutomationPolicy.publicAddress("2606:4700:4700::1111"))
    }

    func testFixtureOriginIsExactAndCannotBeAddedByCommand() async throws {
        let executor = BrowserAutomationExecutor(testOrigins: ["http://127.0.0.1:54321"], showWindows: false)
        do {
            _ = try await executor.execute("browser_open", arguments: operation([
                "url": .string("http://127.0.0.1:54322"), "testOrigins": .array([.string("http://127.0.0.1:54322")])]))
            XCTFail("model must not relax the policy")
        } catch { XCTAssertEqual(error as? BrowserAutomationError, .invalidArguments) }
        let policy = BrowserAutomationPolicy(testOrigins: ["http://127.0.0.1:54321", "https://example.com", "http://localhost:54321"])
        XCTAssertEqual(policy.testOrigins, ["http://127.0.0.1:54321"])
        XCTAssertThrowsError(try policy.validate("http://127.0.0.1:54322/"))
        XCTAssertEqual(try policy.validate("http://127.0.0.1:54321/a").path, "/a")
        await executor.reset()
    }

    func testManagedRealDOMActionsLiteralInputAndStaleRefs() async throws {
        let fixture = try BrowserAutomationFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let opened = try await executor.execute("browser_open", arguments: operation(["url": .string(fixture.origin + "/")]))
            XCTAssertEqual(opened["mode"].string, "managed")
            XCTAssertEqual(opened["network_scope"].string, "private_pinned_proxy")
            XCTAssertEqual(opened["external_unverified"].bool, true)
            XCTAssertFalse(try JSONEncoder().encode(opened).containsSubsequence(Data("SENSITIVE-TEST-SECRET".utf8)))
            let tab = try XCTUnwrap(opened["tab_id"].string)
            let field = try find(opened, label: "Nombre de prueba")
            let literal = "PRUEBA JARVIS \"); globalThis.injected=true; //"
            let filled = try await executor.execute("browser_action", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "snapshot_id": opened["snapshot_id"], "ref": field["ref"], "action": .string("fill"), "text": .string(literal)]))
            XCTAssertEqual(filled["verified"].bool, true)
            XCTAssertEqual(try find(filled["observation"], label: "Nombre de prueba")["value"].string, literal)
            do {
                _ = try await executor.execute("browser_action", arguments: operation([
                    "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                    "snapshot_id": opened["snapshot_id"], "ref": field["ref"], "action": .string("fill"), "text": .string("REPLAY")]))
                XCTFail("old snapshot must not act")
            } catch { XCTAssertEqual(error as? BrowserAutomationError, .staleSnapshot) }
            let selection = try find(filled["observation"], label: "Color de prueba")
            let selected = try await executor.execute("browser_action", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "snapshot_id": filled["observation"]["snapshot_id"], "ref": selection["ref"], "action": .string("select"), "value": .string("azul")]))
            XCTAssertEqual(selected["verified"].bool, true)
            let button = try find(selected["observation"], label: "Aplicar prueba")
            let clicked = try await executor.execute("browser_action", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "snapshot_id": selected["observation"]["snapshot_id"], "ref": button["ref"], "action": .string("click")]))
            XCTAssertEqual(clicked["action_dispatched"].bool, true)
            XCTAssertTrue(clicked["observation"]["text"].string?.contains("RESULTADO " + literal + " azul") == true)
            XCTAssertFalse(clicked["observation"]["text"].string?.contains("INJECTION EXECUTED") == true)
            let requests = fixture.requests()
            XCTAssertFalse(requests.contains("/forbidden"), "unadmitted private subresource must not reach the decoy")
            XCTAssertTrue(requests.contains("/"))
            XCTAssertFalse(requests.contains(where: { $0.contains("simple/") }))
            let close = try await executor.execute("browser_close", arguments: operation(["tab_id": .string(tab), "expected_origin": .string(fixture.origin)]))
            XCTAssertEqual(close["closed"].bool, true)
            let status = try await executor.execute("browser_status", arguments: [:])
            XCTAssertEqual(status["tabs"].array, [])
            await executor.reset()
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch {
            await executor.reset(); _ = await fixture.stop(); throw error
        }
    }

    func testOriginPreconditionBeforeReadNavigationAndReusedOperationAreRejected() async throws {
        let fixture = try BrowserAutomationFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let args = operation(["url": .string(fixture.origin + "/")])
            let opened = try await executor.execute("browser_open", arguments: args)
            do { _ = try await executor.execute("browser_open", arguments: args); XCTFail() }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .alreadySubmitted) }
            let tab = try XCTUnwrap(opened["tab_id"].string)
            for command in ["browser_observe", "browser_navigate"] {
                var values: [String: RPCValue] = ["tab_id": .string(tab), "expected_origin": .string("https://example.com")]
                if command == "browser_navigate" { values["url"] = .string(fixture.origin + "/other") }
                do { _ = try await executor.execute(command, arguments: operation(values)); XCTFail("wrong origin") }
                catch { XCTAssertEqual(error as? BrowserAutomationError, .staleSnapshot) }
            }
            XCTAssertFalse(fixture.requests().contains("/other"))
            let second = try await executor.execute("browser_navigate", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin), "url": .string(fixture.origin + "/other")]))
            XCTAssertTrue(second["text"].string?.contains("OTRA PAGINA") == true)
            let back = try await executor.execute("browser_history", arguments: operation([
                "tab_id": .string(tab), "expected_origin": .string(fixture.origin), "direction": .string("back")]))
            XCTAssertTrue(back["text"].string?.contains("BASE NAVEGADOR") == true)
            await executor.reset()
            let status = try await executor.execute("browser_status", arguments: [:])
            XCTAssertEqual(status["tabs"].array, [])
            do { _ = try await executor.execute("browser_open", arguments: args); XCTFail("reset must not permit replay") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .alreadySubmitted) }
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testSearchAndDownloadUseExplicitFixtureAndDoNotWriteFiles() async throws {
        let fixture = try BrowserAutomationFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let query = "JARVIS A&B + 42"
            let found = try await executor.execute("browser_search", arguments: operation([
                "query": .string(query), "search_url_template": .string(fixture.origin + "/search?q={query}")]))
            XCTAssertTrue(found["text"].string?.contains("BUSQUEDA " + query) == true)
            let downloaded = try await executor.execute("browser_download", arguments: operation([
                "tab_id": found["tab_id"], "url": .string(fixture.origin + "/download"), "expected_origin": .string(fixture.origin)]))
            XCTAssertEqual(downloaded["written_to_workspace"].bool, false)
            XCTAssertEqual(downloaded["bytes"].integer, Data("DESCARGA JARVIS SINTETICA 42".utf8).count)
            XCTAssertEqual(Data(base64Encoded: downloaded["bytes_base64"].string ?? ""), Data("DESCARGA JARVIS SINTETICA 42".utf8))
            XCTAssertEqual(downloaded["sha256"].string?.count, 64)
            do {
                _ = try await executor.execute("browser_download", arguments: operation([
                    "tab_id": found["tab_id"], "url": .string(fixture.origin + "/oversized"), "expected_origin": .string(fixture.origin)]))
                XCTFail("oversized download")
            } catch { XCTAssertTrue(error is BrowserAutomationError) }
            await executor.reset()
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testManagedCancellationSettlesAndCannotReuseID() async throws {
        let fixture = try BrowserAutomationFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        let args = operation(["url": .string(fixture.origin + "/slow")])
        let task = Task { try await executor.execute("browser_open", arguments: args) }
        try await Task.sleep(nanoseconds: 200_000_000)
        await executor.cancel(operationID: args["operation_id"]!.string!)
        do { _ = try await task.value; XCTFail("cancelled operation") }
        catch { XCTAssertEqual(error as? BrowserAutomationError, .uncertainEffect) }
        await executor.reset()
        let state = try await executor.execute("browser_status", arguments: [:])
        XCTAssertEqual(state["active"].bool, false)
        XCTAssertEqual(state["tabs"].array, [])
        let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
    }

    func testOwnerClosingManagedWindowRetiresItsTarget() async throws {
        let fixture = try BrowserAutomationFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let opened = try await executor.execute("browser_open", arguments: operation(["url": .string(fixture.origin + "/")]))
            let tab = try XCTUnwrap(opened["tab_id"].string)
            let window = try XCTUnwrap(NSApp.windows.first { $0.identifier?.rawValue == "jarvis.browser." + tab })
            window.close()
            let status = try await executor.execute("browser_status", arguments: [:])
            XCTAssertEqual(status["tabs"].array, [])
            do {
                _ = try await executor.execute("browser_observe", arguments: operation([
                    "tab_id": .string(tab), "expected_origin": .string(fixture.origin)]))
                XCTFail("a closed target must not be read")
            } catch { XCTAssertEqual(error as? BrowserAutomationError, .unknownTab) }
            await executor.reset(); let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testPublicHTTPSUsesAuthenticatedProxyAndCannotFallBackOnDNSFailure() async throws {
        let executor = BrowserAutomationExecutor(showWindows: false)
        do {
            _ = try await executor.execute("browser_open", arguments: operation(["url": .string("https://jarvis-fixture.invalid/")]))
            XCTFail("reserved invalid DNS must not navigate")
        } catch { XCTAssertTrue(error is BrowserAutomationError) }
        let authenticated = await executor.networkStatistics()
        XCTAssertGreaterThan(authenticated, 0, "The HTTPS request must reach the authenticated pinned proxy before DNS denial")
        await executor.reset()
    }

    func testCommittedLeaseIsRecheckedAfterObservationBeforeEffect() async throws {
        let fixture = try BrowserAutomationFixture()
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let opened = try await executor.execute("browser_open", arguments: operation(["url": .string(fixture.origin + "/")]))
            let ref = try find(opened, label: "Nombre de prueba")["ref"]
            let args = operation(["tab_id": opened["tab_id"], "expected_origin": .string(fixture.origin),
                "snapshot_id": opened["snapshot_id"], "ref": ref, "action": .string("fill"), "text": .string("MUST NOT WRITE")])
            var checks: [String] = []
            executor.beforeEffect = { operation in checks.append(operation); throw BrowserAutomationError.cancelled }
            do { _ = try await executor.execute("browser_action", arguments: args); XCTFail("revoked lease") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .cancelled) }
            XCTAssertEqual(checks, [args["operation_id"]!.string!])
            executor.beforeEffect = nil
            let fresh = try await executor.execute("browser_observe", arguments: operation([
                "tab_id": opened["tab_id"], "expected_origin": .string(fixture.origin)]))
            XCTAssertEqual(try find(fresh, label: "Nombre de prueba")["value"].string, "")
            await executor.reset()
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    private func find(_ snapshot: RPCValue, label: String) throws -> RPCValue {
        try XCTUnwrap(snapshot["elements"].array?.first { $0["label"].string == label })
    }
}

extension Data {
    fileprivate func containsSubsequence(_ other: Data) -> Bool { range(of: other) != nil }
}

@MainActor
private final class BrowserAutomationFixture {
    let origin: String
    let directory: URL
    private let fixture: FixtureProcess

    init() throws {
        directory = URL(fileURLWithPath: "/private/tmp/jarvis-browser-http-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        let process = Process(), output = Pipe()
        let configured = ProcessInfo.processInfo.environment["JARVIS_DESKTOP_PYTHON"] ?? "/usr/bin/python3"
        process.executableURL = URL(fileURLWithPath: configured)
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

    func requests() -> [String] {
        (try? String(contentsOf: directory.appendingPathComponent("requests.jsonl"), encoding: .utf8))?
            .split(separator: "\n").compactMap { try? JSONDecoder().decode(String.self, from: Data($0.utf8)) } ?? []
    }

    func stop() async -> FixtureProcess.StopResult { await fixture.stop() }

    static let script = #"""
import http.server,json,pathlib,sys,time,urllib.parse,threading
root=pathlib.Path(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  with (root/'requests.jsonl').open('a') as f:f.write(json.dumps(self.path)+'\n')
  path=urllib.parse.urlsplit(self.path)
  if path.path=='/slow':time.sleep(5)
  if path.path=='/download':payload=b'DESCARGA JARVIS SINTETICA 42';kind='application/octet-stream'
  elif path.path=='/oversized':payload=b'x'*70000;kind='application/octet-stream'
  elif path.path=='/other':payload=b'<html><body>OTRA PAGINA</body></html>';kind='text/html'
  elif path.path=='/search':payload=('<html><body>BUSQUEDA '+urllib.parse.parse_qs(path.query).get('q',[''])[0]+'</body></html>').encode();kind='text/html'
  else:
   payload=b'''<html><body><h1>BASE NAVEGADOR</h1><input aria-label="Nombre de prueba"><select aria-label="Color de prueba"><option value="rojo">Rojo</option><option value="azul">Azul</option></select><input type="password" value="SENSITIVE-TEST-SECRET"><input autocomplete="one-time-code" value="SENSITIVE-TEST-SECRET"><button onclick="document.getElementById('result').textContent='RESULTADO '+document.querySelector('input').value+' '+document.querySelector('select').value+(globalThis.injected?' INJECTION EXECUTED':'')">Aplicar prueba</button><p id="result"></p><a href="/other">Otra pagina</a><img src="http://127.0.0.1:DECOYPORT/forbidden"></body></html>'''.replace(b'DECOYPORT',str(decoy.server_port).encode());kind='text/html'
  self.send_response(200);self.send_header('Content-Type',kind+';charset=utf-8');self.send_header('Content-Length',str(len(payload)))
  if path.path=='/download':self.send_header('Content-Disposition','attachment;filename=prueba-jarvis.txt')
  self.end_headers()
  try:self.wfile.write(payload)
  except (BrokenPipeError,ConnectionResetError):pass
 def log_message(self,*args):pass
decoy=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
decoy.daemon_threads=True
threading.Thread(target=decoy.serve_forever,daemon=True).start()
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
server.daemon_threads=True
print(server.server_port,flush=True)
server.serve_forever()
"""#
}
