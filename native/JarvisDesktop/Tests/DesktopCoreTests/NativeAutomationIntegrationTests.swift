import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import DesktopCore

/// Full native pump -> signed RPC -> Runtime authority -> WebKit -> independent
/// HTTP/file oracle. Model and approval decisions are authored synthetic peers.
final class NativeAutomationIntegrationTests: XCTestCase {
    @MainActor
    private func start(_ root: String, mode: String, resolverProbe: String = "normal") async throws -> FixtureProcess {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para integrar el host nativo con Python.")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-c", LoopbackHTTPFixture.script(Self.script), root, mode, resolverProbe]
        let paths = [source, environment["JARVIS_DESKTOP_DEPENDENCIES"]].compactMap { $0 }.joined(separator: ":")
        process.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": paths, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        process.standardOutput = FileHandle.nullDevice
        let logs = "/private/tmp/jarvis-native-logs-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: logs, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        let path = logs + "/stderr.log"
        let fd = Darwin.open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw RPCFailure.unavailable }
        let log = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        process.standardError = log
        addTeardownBlock { try? log.close() }
        let owner = try startFixtureProcess(process, directory: logs)
        do {
            try await ServiceBootstrap.waitForHealth(stateDirectory: root + "/profile", timeout: 8) { process.isRunning }
        } catch {
            let stopped = await owner.stop()
            // Only this authored synthetic child writes here. Preserve useful
            // setup diagnostics in CI output before teardown retires the file.
            print("Fallo de arranque del fixture nativo; cierre: \(stopped).")
            do {
                let reader = try FileHandle(forReadingFrom: URL(fileURLWithPath: path))
                defer { try? reader.close() }
                let end = try reader.seekToEnd()
                try reader.seek(toOffset: end > 8192 ? end - 8192 : 0)
                let diagnostic = try reader.read(upToCount: 8192) ?? Data()
                print(String(decoding: diagnostic, as: UTF8.self))
            } catch { print("No se pudo leer el diagnóstico sintético de arranque.") }
            throw error
        }
        return owner
    }

    @MainActor
    private func run(_ client: LocalRPCClient, scope: SessionScope) async throws -> RPCValue {
        var args = scope.params; args["message"] = .string("PRUEBA BOMBA NATIVA")
        let started = try await client.call("runs.start", params: args)
        let id = try XCTUnwrap(started["run_id"].string)
        var query = scope.params; query["run_id"] = .string(id)
        let deadline = ContinuousClock.now.advanced(by: .seconds(18))
        while ContinuousClock.now < deadline {
            let pending = try await client.call("approvals.list", params: scope.params)
            for approval in pending["approvals"].array ?? [] {
                var decision = scope.params
                decision["approval_id"] = approval["approval_id"]; decision["digest"] = approval["digest"]
                decision["allow"] = .bool(true)
                _ = try await client.call("approvals.decide", params: decision)
            }
            let result = try await client.call("runs.get", params: query)
            if ["completed", "failed", "cancelled", "interrupted"].contains(result["run"]["status"].string ?? "") { return result["run"] }
            try await Task.sleep(for: .milliseconds(20))
        }
        throw RPCFailure.unavailable
    }

    @MainActor
    func testActualSignedPumpBrowserActionsDownloadAndRestartWithoutReplay() async throws {
        let root = "/private/tmp/jr-native-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: root) }
        let first = try await start(root, mode: "normal")
        let client = LocalRPCClient(stateDirectory: root + "/profile")
        let transport = LocalRPCClient(stateDirectory: root + "/profile")
        let host = NativeAutomationHost()
        do {
            let info = try await client.call("fixture.native.info")
            let origin = try XCTUnwrap(info["origin"].string)
            let scope = SessionScope(sessionID: try XCTUnwrap(info["session_id"].string), temporary: false)
            host.configure(transport, testOrigins: [origin]); await host.start()
            XCTAssertTrue(host.connected, host.errorMessage ?? "host did not attach")
            let result = try await run(client, scope: scope)
            XCTAssertEqual(result["status"].string, "completed", host.errorMessage ?? "run failed")
            XCTAssertEqual(result["outcome"].string, "known")
            let receipts = try XCTUnwrap(result["action_receipts"].array)
            XCTAssertEqual(receipts.count, 6)
            XCTAssertTrue(receipts.allSatisfy { $0["succeeded"].bool == true && $0["effect_status"].string == "known" })
            XCTAssertEqual(receipts.filter { $0["tool_name"].string == "browser_download" }.first?["verified"].bool, true)
            let trace = try await client.call("fixture.native.trace")
            XCTAssertEqual(trace["effects"].integer, 1)
            XCTAssertEqual(trace["effect_text"].string, "JARVIS BROKER 7319")
            XCTAssertEqual(trace["claims"].integer, 6)
            XCTAssertGreaterThan(trace["checks"].integer ?? 0, 6)
            XCTAssertEqual(trace["completions"].integer, 6)
            XCTAssertEqual(trace["unresolved"].integer, 0)
            let bytes = try Data(contentsOf: URL(fileURLWithPath: root + "/profile/workspace/broker-descarga.txt"))
            XCTAssertEqual(bytes, Data("DESCARGA BROKER JARVIS 7319".utf8))
            XCTAssertEqual(trace["download_sha256"].string, SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined())
            let stopped = await host.stop(); XCTAssertTrue(stopped)
            await client.close()
            let closed = await first.stop(); XCTAssertEqual(closed, .stopped)
            let second = try await start(root, mode: "normal")
            let next = LocalRPCClient(stateDirectory: root + "/profile")
            let nextHost = NativeAutomationHost()
            let nextTransport = LocalRPCClient(stateDirectory: root + "/profile")
            let nextInfo = try await next.call("fixture.native.info")
            nextHost.configure(nextTransport, testOrigins: [try XCTUnwrap(nextInfo["origin"].string)])
            await nextHost.start()
            XCTAssertTrue(nextHost.connected)
            try await Task.sleep(for: .milliseconds(250))
            let after = try await next.call("fixture.native.trace")
            XCTAssertEqual(after["model_calls"].integer, 0)
            XCTAssertEqual(after["claims"].integer, 0)
            XCTAssertEqual(after["completions"].integer, 0)
            XCTAssertEqual(after["effects"].integer, 1)
            XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: root + "/profile/workspace/broker-descarga.txt")), bytes)
            let nextStopped = await nextHost.stop(); XCTAssertTrue(nextStopped)
            await next.close(); let secondClosed = await second.stop(); XCTAssertEqual(secondClosed, .stopped)
        } catch {
            _ = await host.stop(); await client.close(); _ = await first.stop(); throw error
        }
    }

    @MainActor
    func testDiscordantCommittedClaimNeverDispatchesNativeWork() async throws {
        let root = "/private/tmp/jr-claim-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: root) }
        let process = try await start(root, mode: "bad_claim")
        let client = LocalRPCClient(stateDirectory: root + "/profile")
        let transport = LocalRPCClient(stateDirectory: root + "/profile")
        let host = NativeAutomationHost()
        do {
            let info = try await client.call("fixture.native.info")
            let scope = SessionScope(sessionID: try XCTUnwrap(info["session_id"].string), temporary: false)
            host.configure(transport, testOrigins: [try XCTUnwrap(info["origin"].string)]); await host.start()
            XCTAssertTrue(host.connected)
            let result = try await run(client, scope: scope)
            XCTAssertEqual(result["outcome"].string, "unknown")
            XCTAssertEqual(result["action_receipts"].array?.count, 1)
            let trace = try await client.call("fixture.native.trace")
            XCTAssertEqual(trace["claims"].integer, 1)
            XCTAssertEqual(trace["checks"].integer, 0)
            XCTAssertEqual(trace["completions"].integer, 1)
            XCTAssertEqual(trace["requests"].integer, 0)
            XCTAssertEqual(trace["effects"].integer, 0)
            XCTAssertEqual(trace["unresolved"].integer, 0)
            let closed = await host.stop(); XCTAssertTrue(closed)
            await client.close(); let stopped = await process.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { _ = await host.stop(); await client.close(); _ = await process.stop(); throw error }
    }

    @MainActor
    private func checkReadinessWithoutReverseDNS(_ probe: String) async throws {
        let root = "/private/tmp/jr-native-dns-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: root) }
        let process = try await start(root, mode: "normal", resolverProbe: probe)
        let client = LocalRPCClient(stateDirectory: root + "/profile")
        do {
            let info = try await client.call("fixture.native.info")
            XCTAssertTrue(info["origin"].string?.hasPrefix("http://127.0.0.1:") == true)
            let trace = try await client.call("fixture.native.trace")
            XCTAssertEqual(trace["model_calls"].integer, 0)
            XCTAssertEqual(trace["claims"].integer, 0)
            XCTAssertEqual(trace["requests"].integer, 0)
            XCTAssertFalse(FileManager.default.fileExists(atPath: root + "/dns-requested"),
                "El servidor HTTP de loopback no debe consultar el DNS del Mac.")
            await client.close()
            let stopped = await process.stop(); XCTAssertEqual(stopped, .stopped)
        } catch { await client.close(); _ = await process.stop(); throw error }
    }

    @MainActor
    func testFixtureReadinessSurvivesUnavailableReverseDNS() async throws {
        try await checkReadinessWithoutReverseDNS("unavailable")
    }

    @MainActor
    func testFixtureReadinessDoesNotWaitForBlockedReverseDNS() async throws {
        try await checkReadinessWithoutReverseDNS("blocked")
    }

    private static let script = #"""
import asyncio,hashlib,http.server,json,pathlib,signal,socket,sys,threading,time
from uuid import uuid4
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef
root=pathlib.Path(sys.argv[1]);mode=sys.argv[2]
resolver_probe=sys.argv[3]
if resolver_probe!='normal':
 def forbidden_reverse_dns(*args,**kwargs):
  (root/'dns-requested').touch()
  if resolver_probe=='blocked':time.sleep(9)
  raise OSError('synthetic_reverse_dns_unavailable')
 socket.getfqdn=forbidden_reverse_dns
class HTTP(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  with (root/'requests.jsonl').open('a') as f:f.write(json.dumps(self.path)+'\n')
  if self.path=='/download':body=b'DESCARGA BROKER JARVIS 7319';kind='application/octet-stream'
  else:body=b'''<html><body><h1>PRUEBA HOST JARVIS</h1><input aria-label="Entrada integracion"><button onclick="fetch('/effect',{method:'POST',body:document.querySelector('input').value})">Aplicar</button></body></html>''';kind='text/html'
  self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_POST(self):
  body=self.rfile.read(int(self.headers.get('Content-Length','0')))
  with (root/'effects.jsonl').open('a') as f:f.write(json.dumps(body.decode())+'\n')
  self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
 def log_message(self,*args):pass
server=LoopbackHTTPServer(('127.0.0.1',0),HTTP);server.daemon_threads=True
threading.Thread(target=server.serve_forever,daemon=True).start();origin='http://127.0.0.1:'+str(server.server_port)
class Peer:
 def __init__(self):self.calls=0
 async def account_status(self):return {'connected':True,'mode':'chatgpt'}
 async def chat(self,message,*,on_tool_call,on_thread,**options):
  self.calls+=1;on_thread(options.get('thread_id') or 'synthetic_'+uuid4().hex)
  async def tool(name,args):
   reply=await on_tool_call(name,args);receipt=json.loads(reply['contentItems'][0]['text'])
   if not receipt['succeeded']:raise RuntimeError('synthetic_tool_failed')
   return json.loads(receipt['output'])
  opened=await tool('browser_open',{'url':origin+'/'})
  tab=opened['tab_id'];base={'tab_id':tab,'expected_origin':origin}
  observed=await tool('browser_observe',base)
  field=next(x for x in observed['elements'] if x['label']=='Entrada integracion')
  filled=await tool('browser_action',{**base,'snapshot_id':observed['snapshot_id'],'ref':field['ref'],'action':'fill','text':'JARVIS BROKER 7319'})
  view=filled['observation'];button=next(x for x in view['elements'] if x['label']=='Aplicar')
  await tool('browser_action',{**base,'snapshot_id':view['snapshot_id'],'ref':button['ref'],'action':'click'})
  await tool('browser_download',{**base,'url':origin+'/download','path':'broker-descarga.txt'})
  await tool('browser_close',{'tab_id':tab})
  return {'answer':'Recorrido de peer propio terminado; no acredita inferencia autenticada.'}
 async def aclose(self):pass
async def main():
 peer=Peer();service=JarvisService(root/'profile',peer)
 ids=root/'session.json'
 if not ids.exists():ids.write_text(json.dumps({'session':service.store.create_session('PRUEBA HOST NATIVO').ref.session_id}))
 session=json.loads(ids.read_text())['session'];counts={'automation.claim':0,'automation.check':0,'automation.complete':0}
 for name in list(counts):
  handler=service.server._handlers[name]
  async def wrapped(value,name=name,handler=handler):
   counts[name]+=1;result=await handler(value)
   if mode=='bad_claim' and name=='automation.claim':result={**result,'digest':'0'*64}
   return result
  service.server._handlers[name]=wrapped
 async def info(value):return {'origin':origin,'session_id':session}
 async def trace(value):
  effects=[json.loads(x) for x in (root/'effects.jsonl').read_text().splitlines()] if (root/'effects.jsonl').exists() else []
  target=root/'profile/workspace/broker-descarga.txt'
  return {'model_calls':peer.calls,'claims':counts['automation.claim'],'checks':counts['automation.check'],'completions':counts['automation.complete'],
   'requests':len((root/'requests.jsonl').read_text().splitlines()) if (root/'requests.jsonl').exists() else 0,
   'effects':len(effects),'effect_text':effects[-1] if effects else None,'unresolved':sum(x.unresolved for x in service.native_broker.work.values()),
   'download_sha256':hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None}
 service.server._handlers.update({'fixture.native.info':info,'fixture.native.trace':trace})
 done=asyncio.Event();asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
 try:await service.start();await done.wait()
 finally:await service.aclose();server.shutdown();server.server_close()
asyncio.run(main())
"""#
}
