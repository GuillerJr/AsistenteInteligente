import AppKit
import DesktopCore
import SwiftUI
import XCTest
@testable import JarvisApp

@MainActor private final class NotificationPanelWire {
    static let sessionA = String(repeating: "a", count: 32)
    static let sessionB = String(repeating: "b", count: 32)
    private var holdA = false
    private var heldRead: CheckedContinuation<Void, Never>?
    private(set) var readHeld = false
    private(set) var mutationCount = 0

    func holdNextARead() { holdA = true; readHeld = false }
    func release() { heldRead?.resume(); heldRead = nil }

    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        guard method == "product.snapshot" else {
            mutationCount += 1
            throw RPCFailure.invalidResponse
        }
        let session = params["session_id"]?.string ?? ""
        guard [Self.sessionA, Self.sessionB].contains(session), params["run_id"]?.string == "host" else {
            throw RPCFailure.invalidResponse
        }
        let isA = session == Self.sessionA
        let result = RPCValue.object([
            "scope": .object(["session_id": .string(session), "temporary": .bool(false),
                "run_id": .string("host"), "agent_id": .string("jarvis"),
                "workspace_id": .string("synthetic_workspace"), "generation": .number(0)]),
            "interactions": .array(isA ? [.object([
                "id": .string("accepted_notice_a"), "kind": .string("notification"),
                "revision": .number(3), "status": .string("delivered"),
                "scope_run_id": .string("host"), "expires_ns": .number(0),
                "payload": .object(["attention_kind": .string("run_completed"),
                    "correlation_id": .string("host")])])] : []),
            "notification_preferences": .object(["revision": .number(1),
                "enabled": .bool(isA), "silenced": .bool(false)])])
        if isA, holdA {
            holdA = false; readHeld = true
            await withCheckedContinuation { heldRead = $0 }
        }
        return result
    }
}

@MainActor private final class NotificationPanelRenderingFixture {
    let wire: NotificationPanelWire
    let store: ProductInteractionStore
    let hosting: NSHostingView<AnyView>
    let window: NSWindow
    private let previousPolicy: NSApplication.ActivationPolicy

    init() {
        _ = NSApplication.shared
        previousPolicy = NSApp.activationPolicy()
        let currentWire = NotificationPanelWire(), currentStore = ProductInteractionStore()
        let currentHosting = NSHostingView(rootView: AnyView(ProductInteractionPanel(store: currentStore)
            .environment(\.accessibilityEnabled, true)))
        let currentWindow = NSWindow(contentRect: NSRect(x: 40, y: 40, width: 560, height: 760),
                                     styleMask: [.titled, .closable], backing: .buffered, defer: true)
        wire = currentWire; store = currentStore; hosting = currentHosting; window = currentWindow
        window.isReleasedWhenClosed = false
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.product.notification-rendering-fixture")
        window.title = "Jarvis · avisos sintéticos"
        window.contentView = hosting
        let transport = wire
        store.configure(request: { try await transport.request($0, $1) })
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func close() {
        wire.release()
        store.close()
        hosting.rootView = AnyView(EmptyView())
        window.orderOut(nil); window.contentView = nil; window.close()
        NSApp.setActivationPolicy(previousPolicy)
    }

    func select(_ session: String) async {
        store.setScope(SessionScope(sessionID: session, temporary: false), runID: "host")
        await store.refresh()
    }

    /// Public, in-process AppKit accessibility getters on this fixture's own
    /// view tree. No cross-process AX client, private UI API or TCC request.
    private func read(_ node: NSObject, _ getter: String) -> Any? {
        let selector = NSSelectorFromString(getter)
        return node.responds(to: selector) ? node.perform(selector)?.takeUnretainedValue() : nil
    }

    private func accessibleState() throws -> (values: Set<Int>, text: Set<String>) {
        var pending: [NSObject] = [hosting], seen = Set<ObjectIdentifier>()
        var values = Set<Int>(), text = Set<String>(), index = 0
        while index < pending.count {
            guard index < 4_000 else { throw RPCFailure.oversized }
            let node = pending[index]; index += 1
            guard seen.insert(ObjectIdentifier(node)).inserted else { continue }
            if let view = node as? NSView { pending.append(contentsOf: view.subviews) }
            if let children = read(node, "accessibilityChildren") as? [NSObject] {
                pending.append(contentsOf: children)
            }
            if read(node, "accessibilityIdentifier") as? String == "jarvis.product.notifications.enabled",
               let value = read(node, "accessibilityValue") as? NSNumber {
                values.insert(value.intValue)
            }
            for getter in ["accessibilityLabel", "accessibilityValue", "accessibilityTitle"] {
                if let value = read(node, getter) as? String { text.insert(value) }
            }
        }
        return (values, text)
    }

    func waitForToggle(_ value: Int, acceptedText: Bool) async throws {
        for _ in 0..<100 {
            hosting.needsLayout = true; hosting.layoutSubtreeIfNeeded()
            window.contentView?.layoutSubtreeIfNeeded()
            let state = try accessibleState()
            let hasTitle = state.text.contains { $0.contains("Aceptado por macOS") }
            let hasExplanation = state.text.contains {
                $0.contains("macOS aceptó la solicitud; su visualización depende de los ajustes del sistema.")
            }
            if state.values == Set([value]), hasTitle == acceptedText, hasExplanation == acceptedText { return }
            try await Task.sleep(for: .milliseconds(20))
        }
        XCTFail("The production panel's own AX toggle/text did not reflect the selected synthetic chat")
        throw RPCFailure.invalidResponse
    }

    func waitForHeldRead() async throws {
        for _ in 0..<100 {
            if wire.readHeld { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        throw RPCFailure.invalidResponse
    }
}

final class ProductNotificationPanelRenderingTests: XCTestCase {
    @MainActor func testRenderedNotificationToggleTracksAThenBThenAAndIgnoresLateAResponse() async throws {
        let fixture = NotificationPanelRenderingFixture()
        var lateRead: Task<Void, Never>?
        defer { lateRead?.cancel(); fixture.close() }
        await fixture.select(NotificationPanelWire.sessionA)
        try await fixture.waitForToggle(1, acceptedText: true)
        fixture.wire.holdNextARead()
        lateRead = Task { await fixture.store.refresh() }
        try await fixture.waitForHeldRead()
        await fixture.select(NotificationPanelWire.sessionB)
        try await fixture.waitForToggle(0, acceptedText: false)
        fixture.wire.release(); await lateRead?.value
        try await fixture.waitForToggle(0, acceptedText: false)
        await fixture.select(NotificationPanelWire.sessionA)
        try await fixture.waitForToggle(1, acceptedText: true)
        XCTAssertEqual(fixture.wire.mutationCount, 0)
        XCTAssertEqual(fixture.store.scope?.sessionID, NotificationPanelWire.sessionA)
    }
}
