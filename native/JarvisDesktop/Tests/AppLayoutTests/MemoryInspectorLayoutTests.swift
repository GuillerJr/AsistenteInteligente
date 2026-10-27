import AppKit
import DesktopCore
import SwiftUI
import XCTest
@testable import JarvisApp

private actor LayoutMetadataWire {
    private var documentCount = 0
    private var calls = 0
    func update(documents: Int) { documentCount = documents }
    func count() -> Int { calls }
    func request(_ method: String, _ params: [String: RPCValue]) throws -> RPCValue {
        guard method == "memory.intelligence.status", let session = params["session_id"], let temporary = params["temporary"] else { throw RPCFailure.invalidResponse }
        calls += 1
        let identity = RPCValue.object(["client_id": .string(String(repeating: "a", count: 64)),
            "agent_id": .string("jarvis"), "workspace_id": .string(String(repeating: "b", count: 64)),
            "temporary": temporary, "session_id": temporary == .bool(true) ? session : .null])
        let unsigned = RPCValue.object(["profile_binding": .string(String(repeating: "c", count: 64)),
            "scope": identity, "revision": .number(1), "settings": MemoryIntelligenceSettings().value,
            "permissions_portable": .bool(false)])
        let digest = ManagementValidation.digest(try MaintenanceValidation.canonical(unsigned))
        let policy = RPCValue.object(unsigned.object!.merging(["digest": .string(digest)], uniquingKeysWith: { _, next in next }))
        return .object(["intelligence": .object(["scope": .object(["session_id": session, "temporary": temporary]),
            "policy": policy, "model": .object(["id": .string("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
                "state": .string("ready"), "available": .bool(true), "runtime_available": .bool(true),
                "bytes": .number(127_509_162), "fingerprint": .string(String(repeating: "d", count: 64)),
                "cpu_only": .bool(true), "network_for_inference": .bool(false), "previous_copy": .null]),
            "index": .object(["state": .string(documentCount == 0 ? "empty" : "ready"),
                "source_generation": .number(Double(documentCount + 1)), "generation": .number(Double(documentCount + 1)),
                "document_count": .number(Double(documentCount)), "chunk_count": .number(Double(documentCount * 2)),
                "bytes": .number(Double(documentCount * 8192)), "model_fingerprint": .string(String(repeating: "d", count: 64)),
                "error": .null]), "current_job": .null, "last_recall": .null])])
    }
}

@MainActor
private final class InspectorLayoutFixture {
    let store: ChatStore
    let presentation: InspectorPresentation
    let wire: LayoutMetadataWire
    let hosting: NSHostingView<ContentView>
    let window: NSWindow
    init(width: CGFloat = 1040, height: CGFloat = 720) throws {
        _ = NSApplication.shared
        let currentStore = ChatStore()
        let currentPresentation = InspectorPresentation(selection: "memory")
        let currentWire = LayoutMetadataWire()
        let row = RPCValue.object(["session_id": .string(String(repeating: "1", count: 32)),
            "temporary": .bool(true), "title": .string("Conversación sintética de layout"),
            "archived": .bool(false), "agent_id": .string("jarvis"), "parent_session_id": .null])
        let session = try XCTUnwrap(ChatSession(row))
        currentStore.sessions = [session]; currentStore.selectedID = session.id; currentStore.ready = true
        currentStore.access.workspacePath = "/synthetic/layout-workspace" // A label, never opened.
        currentStore.access.setContext(session.scope); currentStore.access.memoryEnabled = true
        currentStore.memoryIntelligence.configure(MemoryIntelligenceClient(runtime: currentStore.runtime,
            request: { try await currentWire.request($0, $1) }))
        currentStore.synchronizeMemoryIntelligenceContext()
        let currentHosting = NSHostingView(rootView: ContentView(store: currentStore, inspector: currentPresentation))
        let currentWindow = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: width, height: height),
            styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: true)
        store = currentStore; presentation = currentPresentation; wire = currentWire
        hosting = currentHosting; window = currentWindow
        window.isReleasedWhenClosed = false
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.layout.fixture")
        window.title = "Jarvis · layout sintético"
        window.contentView = hosting
        // Offscreen and never key: the native inspector is actually presented,
        // while no owner window, screenshot, accessibility data or account is used.
        window.orderBack(nil)
    }
    func close() async {
        closeWindow()
        await store.memoryIntelligence.close()
    }
    func closeWindow() {
        presentation.isPresented = false
        window.orderOut(nil); window.contentView = nil; window.close()
        store.runtime.retire()
    }
    func notes(_ count: Int) throws {
        store.access.documents = try (0..<count).map { index in
            try MemoryDocument(.object(["document_id": .string("own-\(index)"), "key": .string("own-key-\(index)"),
                "title": .string(String(repeating: "Nota sintética larga \(index) · ", count: index + 2)),
                "source_uri": .string("jarvis://synthetic/\(index)"), "revision": .number(1),
                "text_sha256": .string(String(repeating: "e", count: 64))]))
        }
    }
    func settle() async throws -> NSSize {
        for _ in 0..<5 {
            hosting.needsLayout = true
            hosting.layoutSubtreeIfNeeded()
            window.contentView?.layoutSubtreeIfNeeded()
            try await Task.sleep(for: .milliseconds(20))
        }
        let first = hosting.fittingSize
        hosting.needsLayout = true; hosting.layoutSubtreeIfNeeded()
        let second = hosting.fittingSize
        XCTAssertTrue(first.width.isFinite && first.height.isFinite)
        XCTAssertTrue(second.width.isFinite && second.height.isFinite)
        XCTAssertEqual(first.width, second.width, accuracy: 1, "Unchanged layout must reach a size fixed point")
        XCTAssertEqual(first.height, second.height, accuracy: 1)
        XCTAssertGreaterThan(hosting.bounds.width, 0); XCTAssertGreaterThan(hosting.bounds.height, 0)
        XCTAssertFalse(window.isKeyWindow)
        return second
    }
}

final class MemoryInspectorLayoutTests: XCTestCase {
    @MainActor func testOpeningActualMemoryInspectorAndRefreshingNotesHasStableBounds() async throws {
        let fixture = try InspectorLayoutFixture()
        defer { fixture.closeWindow() }
        _ = try await fixture.settle()
        fixture.presentation.isPresented = true
        _ = try await fixture.settle()
        let before = fixture.window.frame.size
        try fixture.notes(6); await fixture.wire.update(documents: 6)
        await fixture.store.memoryIntelligence.refresh()
        _ = try await fixture.settle()
        XCTAssertEqual(fixture.window.frame.width, before.width, accuracy: 1)
        XCTAssertEqual(fixture.window.frame.height, before.height, accuracy: 1)
        XCTAssertEqual(fixture.store.access.documents.count, 6)
        XCTAssertEqual(fixture.store.memoryIntelligence.status?.index.documentCount, 6)
        let requests = await fixture.wire.count(); XCTAssertGreaterThan(requests, 0)
        await fixture.close()
    }
    @MainActor func testActualNotesAndAutomationRemainStableAcrossResizeAndReopening() async throws {
        let fixture = try InspectorLayoutFixture()
        defer { fixture.closeWindow() }
        fixture.presentation.isPresented = true
        for size in [NSSize(width: 1040, height: 720), NSSize(width: 920, height: 540), NSSize(width: 1400, height: 860)] {
            fixture.window.setContentSize(size)
            for page in [MemoryPanelPage.notes, .intelligence, .notes] {
                fixture.presentation.memoryPage = page
                _ = try await fixture.settle()
            }
            fixture.presentation.isPresented = false; _ = try await fixture.settle()
            fixture.presentation.isPresented = true; _ = try await fixture.settle()
        }
        XCTAssertTrue(fixture.presentation.isPresented)
        await fixture.close()
    }
    @MainActor func testViewportContainsChildIntrinsicMinimumChangesWithoutWindowGrowth() async throws {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: -20_000, y: -20_000, width: 420, height: 600),
            styleMask: [.titled, .resizable], backing: .buffered, defer: true)
        window.isReleasedWhenClosed = false
        let hosting = NSHostingView(rootView: InspectorViewport { Text("Propio").frame(minWidth: 200, idealWidth: 240) })
        window.contentView = hosting
        defer { window.contentView = nil; window.close() }
        hosting.layoutSubtreeIfNeeded()
        let before = window.frame.size
        hosting.rootView = InspectorViewport { Text(String(repeating: "Texto sintético ", count: 200)).frame(minWidth: 1200, idealWidth: 1600) }
        hosting.needsLayout = true; hosting.layoutSubtreeIfNeeded()
        try await Task.sleep(for: .milliseconds(20))
        hosting.layoutSubtreeIfNeeded()
        XCTAssertEqual(window.frame.width, before.width, accuracy: 1)
        XCTAssertEqual(window.frame.height, before.height, accuracy: 1)
        XCTAssertLessThanOrEqual(hosting.fittingSize.width, hosting.bounds.width + 1)
    }
}
