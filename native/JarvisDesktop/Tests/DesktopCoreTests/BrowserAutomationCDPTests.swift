import Foundation
import XCTest
@testable import DesktopCore

@MainActor
final class BrowserAutomationCDPTests: XCTestCase {
    private func op(_ args: [String: RPCValue]) -> [String: RPCValue] {
        var result = args
        result["operation_id"] = .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased())
        return result
    }

    func testAttachmentUsesOneExactTargetAndResetDoesNotCloseOwnerTab() async throws {
        let fixture = try BrowserCDPFixture(mode: "normal")
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let result = try await executor.execute("browser_attach", arguments: op([
                "endpoint": .string(fixture.origin), "target_id": .string("fixture-target")]))
            XCTAssertEqual(result["mode"].string, "attached")
            XCTAssertEqual(result["network_scope"].string, "existing_browser_owner_network")
            XCTAssertEqual(result["text"].string, "CDP PRUEBA SINTETICA")
            let tab = try XCTUnwrap(result["tab_id"].string)
            let actionArgs = op(["tab_id": .string(tab), "expected_origin": .string(fixture.origin),
                "snapshot_id": result["snapshot_id"], "ref": .string("r1"), "action": .string("click")])
            let acted = try await executor.execute("browser_action", arguments: actionArgs)
            XCTAssertEqual(acted["action_dispatched"].bool, true)
            do { _ = try await executor.execute("browser_action", arguments: actionArgs); XCTFail("no resend") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .alreadySubmitted) }
            let close = try await executor.execute("browser_close", arguments: op(["tab_id": .string(tab), "expected_origin": .string(fixture.origin)]))
            XCTAssertEqual(close["closed"].bool, false)
            XCTAssertEqual(close["detached"].bool, true)
            await executor.reset()
            let records = fixture.records()
            let methods = records.compactMap { $0["method"].string }
            XCTAssertEqual(methods.filter { $0 == "Target.closeTarget" }.count, 0)
            XCTAssertEqual(methods.filter { $0 == "Target.getTargets" }.count, 0)
            XCTAssertEqual(records.filter { $0["path"].string == "/devtools/page/fixture-target" }.count, 1)
            XCTAssertEqual(records.filter { $0["effect"].bool == true }.count, 1)
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testWrongTargetRejectedBeforeDOMOrAction() async throws {
        let fixture = try BrowserCDPFixture(mode: "wrong")
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            do {
                _ = try await executor.execute("browser_attach", arguments: op([
                    "endpoint": .string(fixture.origin), "target_id": .string("fixture-target")]))
                XCTFail("wrong target")
            } catch { XCTAssertEqual(error as? BrowserAutomationError, .unknownTab) }
            let methods = fixture.records().compactMap { $0["method"].string }
            XCTAssertEqual(methods, ["Target.getTargetInfo"])
            await executor.reset()
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testLostActionReplyIsUncertainAndNeverResent() async throws {
        let fixture = try BrowserCDPFixture(mode: "drop")
        let executor = BrowserAutomationExecutor(testOrigins: [fixture.origin], showWindows: false)
        do {
            let opened = try await executor.execute("browser_attach", arguments: op([
                "endpoint": .string(fixture.origin), "target_id": .string("fixture-target")]))
            let args = op(["tab_id": opened["tab_id"], "expected_origin": .string(fixture.origin),
                "snapshot_id": opened["snapshot_id"], "ref": .string("r1"), "action": .string("click")])
            do { _ = try await executor.execute("browser_action", arguments: args); XCTFail("lost reply") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .uncertainEffect) }
            do { _ = try await executor.execute("browser_action", arguments: args); XCTFail("no replay") }
            catch { XCTAssertEqual(error as? BrowserAutomationError, .alreadySubmitted) }
            XCTAssertEqual(fixture.records().filter { $0["effect"].bool == true }.count, 1)
            await executor.reset()
            let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await executor.reset(); _ = await fixture.stop(); throw error }
    }

    func testCDPRejectsExternalDiscoveryAndHasFiniteReplyTimeout() async throws {
        for endpoint in ["http://example.com:9222", "ws://127.0.0.1:9222/devtools/browser/a", "http://user:pass@127.0.0.1:9222", "http://localhost:9222"] {
            XCTAssertThrowsError(try BrowserAutomationCDP(endpoint: URL(string: endpoint)!, targetID: "fixture-target"))
        }
        let fixture = try BrowserCDPFixture(mode: "hold")
        let connection = try BrowserAutomationCDP(endpoint: URL(string: fixture.origin)!, targetID: "fixture-target")
        let started = ProcessInfo.processInfo.systemUptime
        do { _ = try await connection.call("Target.getTargetInfo", params: ["targetId": .string("fixture-target")]); XCTFail("timeout") }
        catch { XCTAssertLessThan(ProcessInfo.processInfo.systemUptime - started, 9) }
        connection.close()
        XCTAssertEqual(fixture.records().filter { $0["method"].string == "Target.getTargetInfo" }.count, 1)
        let stopped = await fixture.stop(); XCTAssertEqual(stopped, .stopped)
    }
}

@MainActor
private final class BrowserCDPFixture {
    let origin: String
    let directory: URL
    private let fixture: FixtureProcess

    init(mode: String) throws {
        directory = URL(fileURLWithPath: "/private/tmp/jarvis-browser-cdp-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        let process = Process(), output = Pipe()
        process.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["JARVIS_DESKTOP_PYTHON"] ?? "/usr/bin/python3")
        process.arguments = ["-I", "-u", "-c", LoopbackHTTPFixture.script(Self.script), directory.path, mode]
        process.environment = ["PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"]
        process.standardOutput = output; process.standardError = FileHandle.nullDevice
        fixture = FixtureProcess(process); try fixture.run()
        var line = Data()
        while !line.contains(10), line.count < 128 {
            let next = output.fileHandleForReading.readData(ofLength: 1)
            if next.isEmpty { throw BrowserAutomationError.unavailable }; line.append(next)
        }
        guard let port = Int(String(decoding: line, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)) else { throw BrowserAutomationError.unavailable }
        origin = "http://127.0.0.1:\(port)"
    }
    func records() -> [RPCValue] {
        (try? String(contentsOf: directory.appendingPathComponent("wire.jsonl"), encoding: .utf8))?
            .split(separator: "\n").compactMap { try? JSONDecoder().decode(RPCValue.self, from: Data($0.utf8)) } ?? []
    }
    func stop() async -> FixtureProcess.StopResult { await fixture.stop() }

    static let script = #"""
import base64,hashlib,http.server,json,pathlib,struct,sys,time
root=pathlib.Path(sys.argv[1]);mode=sys.argv[2]
def log(value):
 with (root/'wire.jsonl').open('a') as f:f.write(json.dumps(value)+'\n')
class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def do_GET(self):
  log({'path':self.path})
  if self.path!='/devtools/page/fixture-target' or self.headers.get('Upgrade','').lower()!='websocket':self.send_error(404);return
  self.send_response(101);self.send_header('Upgrade','websocket');self.send_header('Connection','Upgrade')
  self.send_header('Sec-WebSocket-Accept',base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key']+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode());self.end_headers()
  origin='http://127.0.0.1:'+str(self.server.server_port);url=origin+'/page'
  try:
   while True:
    head=self.rfile.read(2)
    if len(head)<2:return
    if head[0]&15==8:return
    size=head[1]&127
    if size==126:size=struct.unpack('!H',self.rfile.read(2))[0]
    elif size==127:size=struct.unpack('!Q',self.rfile.read(8))[0]
    if size>131072:return
    mask=self.rfile.read(4) if head[1]&128 else b''
    raw=self.rfile.read(size)
    if mask:raw=bytes(v^mask[i%4] for i,v in enumerate(raw))
    value=json.loads(raw);method=value['method'];params=value.get('params',{});log({'method':method,'params':params})
    if mode=='hold':time.sleep(20);return
    result={}
    if method=='Target.getTargetInfo':result={'targetInfo':{'targetId':'wrong-target' if mode=='wrong' else 'fixture-target','type':'page','url':url}}
    elif method=='Page.getFrameTree':result={'frameTree':{'frame':{'id':'root','url':url}}}
    elif method=='Page.createIsolatedWorld':result={'executionContextId':7}
    elif method=='Page.navigate':url=params['url'];result={'frameId':'root'}
    elif method=='Runtime.evaluate':
     args=json.loads(params['expression'].rsplit('})(',1)[1][:-1])
     if 'snapshot' in args:answer={'snapshot_id':args['snapshot'],'url':url,'origin':origin,'title':'CDP','text':'CDP PRUEBA SINTETICA','elements':[{'ref':'r1','tag':'button','role':'button','label':'Accion CDP','value':None,'type':'','href':None,'disabled':False}],'truncated':False,'frame_scope':'main','external_unverified':True}
     elif 'const row=state.describe(e)' in params['expression']:answer={'element':{'href':None},'form_action':None}
     else:
      log({'effect':True});answer={'dispatched':True,'verified':False}
      if mode=='drop':return
     result={'result':{'type':'string','value':json.dumps(answer)}}
    payload=json.dumps({'id':value['id'],'result':result}).encode()
    frame=bytes([0x81,len(payload)]) if len(payload)<126 else b'\x81\x7e'+struct.pack('!H',len(payload))
    self.connection.sendall(frame+payload)
  except (ConnectionError,OSError,json.JSONDecodeError):return
 def log_message(self,*args):pass
server=LoopbackHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
print(server.server_port,flush=True);server.serve_forever()
"""#
}
