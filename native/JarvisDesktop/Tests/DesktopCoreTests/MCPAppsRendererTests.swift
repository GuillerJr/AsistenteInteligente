import AppKit
import Foundation
import XCTest
@testable import DesktopCore

final class MCPAppsRendererTests: XCTestCase {
    private func document(_ script: String) throws -> MCPAppDocument {
        let html = "<!doctype html><p>App propia de prueba</p><script>" + script + "</script>"
        return try MCPAppDocument(id: String(repeating: "1", count: 32), serverID: "fixture", resourceURI: "ui://fixture/test",
            html: html, sha256: ManagementValidation.digest(html), policy: MCPAppContentPolicy())
    }
    @MainActor func testOwnOpaqueIframeHandshakesBeforeForwardingAndReturnsToolAndResourceResults() async throws {
        _ = NSApplication.shared
        let source = try document("""
        const send=(id,method,params={})=>parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
        window.addEventListener('message',event=>{if(event.source!==parent)return;const m=event.data;
          if(m.id===1&&m.error)send(2,'ui/initialize',{protocolVersion:'2026-01-26',appInfo:{name:'Prueba propia',version:'1'},appCapabilities:{}});
          if(m.id===2&&m.result){parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized'},'*');send(3,'tools/call',{name:'echo',arguments:{marker:'JARVIS APP'}});}
          if(m.id===3&&m.result)send(4,'tools/list',{});
          if(m.id===4&&m.result)send(5,'resources/list',{});
          if(m.id===5&&m.result)send(6,'resources/templates/list',{});
          if(m.id===6&&m.result)send(7,'resources/read',{uri:'fixture://own/note'});
        });
        send(1,'tools/call',{name:'must_not_dispatch',arguments:{}});
        """)
        var calls: [String] = [], failures: [String] = []
        let renderer = MCPAppRenderer(document: source, isCurrent: { true }, request: { message in
            let method = try XCTUnwrap(message["method"].string)
            calls.append(method)
            if method == "tools/call" {
                XCTAssertEqual(message["params"]["name"].string, "echo")
                return .object(["content": .array([.object(["type": .string("text"), "text": .string("JARVIS APP")])])])
            }
            if method == "tools/list" { return .object(["tools": .array([])]) }
            if method == "resources/list" { return .object(["resources": .array([])]) }
            if method == "resources/templates/list" { return .object(["resourceTemplates": .array([])]) }
            XCTAssertEqual(message["params"]["uri"].string, "fixture://own/note")
            return .object(["contents": .array([.object(["uri": .string("fixture://own/note"), "text": .string("Dato propio")])])])
        })
        renderer.onError = { failures.append($0) }
        try await renderer.start()
        for _ in 0..<160 {
            if calls.count >= 5 || !failures.isEmpty { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        XCTAssertEqual(calls, ["tools/call", "tools/list", "resources/list", "resources/templates/list", "resources/read"], "An uninitialized call must not cross the native bridge")
        XCTAssertTrue(failures.isEmpty)
        await renderer.close()
        XCTAssertNil(renderer.webView)
    }
    @MainActor func testRevokingOwnFixtureContextPreventsASecondBridgeDispatch() async throws {
        _ = NSApplication.shared
        let source = try document("""
        const send=(id,method,params={})=>parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
        window.addEventListener('message',event=>{if(event.source!==parent)return;const m=event.data;
          if(m.id===1&&m.result){parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized'},'*');
            send(2,'tools/call',{name:'first',arguments:{}});setTimeout(()=>send(3,'tools/call',{name:'second',arguments:{}}),150);}
        });
        send(1,'ui/initialize',{protocolVersion:'2026-01-26',appInfo:{name:'Prueba revocada',version:'1'},appCapabilities:{}});
        """)
        var current = true, count = 0
        let renderer = MCPAppRenderer(document: source, isCurrent: { current }, request: { _ in
            count += 1; current = false
            return .object(["content": .array([])])
        })
        try await renderer.start()
        for _ in 0..<160 {
            if count > 0 { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        try await Task.sleep(for: .milliseconds(350))
        XCTAssertEqual(count, 1)
        await renderer.close()
        do { try await renderer.start(); XCTFail("A closed renderer must never resume the same app lease") }
        catch { XCTAssertEqual(error as? MCPAppFailure, .closed) }
        XCTAssertEqual(count, 1)
    }
}
