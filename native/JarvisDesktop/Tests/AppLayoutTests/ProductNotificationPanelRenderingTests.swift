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
    private var onReadHeld: (() -> Void)?
    private var onReadReturned: (() -> Void)?
    private(set) var readHeld = false
    private(set) var mutationCount = 0

    func holdNextARead(onHeld: @escaping () -> Void, onReturned: @escaping () -> Void) {
        holdA = true; readHeld = false; onReadHeld = onHeld; onReadReturned = onReturned
    }
    func release(notifyReturn: Bool = true) {
        holdA = false; readHeld = false; onReadHeld = nil
        if !notifyReturn { onReadReturned = nil }
        heldRead?.resume(); heldRead = nil
    }

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
            holdA = false
            await withCheckedContinuation { continuation in
                heldRead = continuation; readHeld = true
                let signal = onReadHeld; onReadHeld = nil; signal?()
            }
            let returned = onReadReturned; onReadReturned = nil; returned?()
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
        wire.release(notifyReturn: false)
        store.close()
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

    private struct AXState {
        var values = Set<Int>()
        var invalidValues = Set<String>()
        var controlCount = 0
        var nodeCount = 0
        var hasTitle = false
        var hasExplanation = false
        var truncated = false
        var samples: [String] = []
    }
    private struct Visit: Hashable {
        let object: ObjectIdentifier
        let enabledControl: Bool
    }
    private func toggleValue(_ raw: Any?) -> Int? {
        if let number = raw as? NSNumber, number.doubleValue == 0 || number.doubleValue == 1 {
            return number.intValue
        }
        if let string = raw as? String {
            if string == "0" { return 0 }
            if string == "1" { return 1 }
        }
        return nil
    }
    private func summary(_ raw: Any?) -> String {
        guard let raw else { return "nil" }
        if let number = raw as? NSNumber { return "NSNumber(\(number.stringValue.prefix(32)))" }
        if let string = raw as? String { return "NSString(\(String(reflecting: String(string.prefix(120)))))" }
        return "other"
    }
    private func accessibleState() -> AXState {
        // SwiftUI's virtual accessibility descendants may attach to the window
        // rather than NSHostingView's native subviews. All roots are owned here.
        var pending: [(NSObject, Bool)] = [(window, false), (hosting, false)]
        if let content = window.contentView { pending.append((content, false)) }
        var seen = Set<Visit>(), controlObjects = Set<ObjectIdentifier>()
        var state = AXState(), index = 0
        while index < pending.count {
            guard index < 4_000 else { state.truncated = true; break }
            let (node, inheritedControl) = pending[index]; index += 1
            guard seen.insert(Visit(object: ObjectIdentifier(node), enabledControl: inheritedControl)).inserted else { continue }
            state.nodeCount += 1
            let identifier = read(node, "accessibilityIdentifier") as? String
            let exactID = identifier == "jarvis.product.notifications.enabled"
            let enabledControl = inheritedControl || exactID
            let role = read(node, "accessibilityRole") as? String ?? ""
            let rawValue = read(node, "accessibilityValue")
            let isToggleRole = ["AXCheckBox", "AXSwitch"].contains(role)
            // The identifier can belong to SwiftUI's wrapper. A descendant must
            // still expose a real toggle role; unrelated labels cannot pass.
            if enabledControl, isToggleRole || (exactID && toggleValue(rawValue) != nil),
               controlObjects.insert(ObjectIdentifier(node)).inserted {
                state.controlCount += 1
                if let value = toggleValue(rawValue) { state.values.insert(value) }
                else if state.invalidValues.count < 4 { state.invalidValues.insert(summary(rawValue)) }
            }
            if state.samples.count < 8, exactID || isToggleRole {
                let label = read(node, "accessibilityLabel")
                state.samples.append("id=\(summary(identifier)),role=\(role.prefix(32)),label=\(summary(label)),value=\(summary(rawValue))")
            }
            if let view = node as? NSView, view.window == nil || view.window === window {
                pending.append(contentsOf: view.subviews.map { ($0, enabledControl) })
            }
            for getter in ["accessibilityChildren", "accessibilityChildrenInNavigationOrder", "accessibilityVisibleChildren"] {
                if let children = read(node, getter) as? [NSObject] {
                    pending.append(contentsOf: children.map { ($0, enabledControl) })
                }
            }
            for getter in ["accessibilityLabel", "accessibilityValue", "accessibilityTitle"] {
                if let text = read(node, getter) as? String {
                    state.hasTitle = state.hasTitle || text.contains("Aceptado por macOS")
                    state.hasExplanation = state.hasExplanation || text.contains(
                        "macOS aceptó la solicitud; su visualización depende de los ajustes del sistema.")
                }
            }
        }
        return state
    }
    private func diagnostic(stage: String, state: AXState) -> String {
        let scope = store.scope?.sessionID == NotificationPanelWire.sessionA ? "A"
            : store.scope?.sessionID == NotificationPanelWire.sessionB ? "B" : "none"
        let preferences = store.snapshot?.notificationPreferences
        return "stage=\(stage);scope=\(scope);enabled=\(String(describing: preferences?.enabled));"
            + "nodes=\(state.nodeCount),truncated=\(state.truncated);controls=\(state.controlCount);"
            + "values=\(state.values.sorted()),invalid=\(state.invalidValues.sorted());"
            + "title=\(state.hasTitle),explanation=\(state.hasExplanation);"
            + "windowVisible=\(window.isVisible),key=\(window.isKeyWindow),hosting=\(hosting.bounds.size);"
            + "heldRead=\(wire.readHeld),mutations=\(wire.mutationCount);samples=\(state.samples)"
    }
    func waitForToggle(_ value: Int, acceptedText: Bool, stage: String,
                       file: StaticString = #filePath, line: UInt = #line) async -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(4))
        var state = AXState()
        repeat {
            hosting.needsLayout = true; hosting.layoutSubtreeIfNeeded()
            window.contentView?.layoutSubtreeIfNeeded()
            state = accessibleState()
            if !state.truncated, state.controlCount > 0, state.invalidValues.isEmpty,
               state.values == Set([value]), state.hasTitle == acceptedText,
               state.hasExplanation == acceptedText {
                return true
            }
            do { try await Task.sleep(for: .milliseconds(20)) }
            catch { break }
        } while ContinuousClock.now < deadline
        XCTFail("The production panel's own AX toggle/text did not reflect the selected synthetic chat: "
            + diagnostic(stage: stage, state: state), file: file, line: line)
        return false
    }
}

final class ProductNotificationPanelRenderingTests: XCTestCase {
    @MainActor func testRenderedNotificationToggleTracksAThenBThenAAndIgnoresLateAResponse() async {
        let fixture = NotificationPanelRenderingFixture()
        var lateRead: Task<Void, Never>?
        defer { lateRead?.cancel(); fixture.close() }
        await fixture.select(NotificationPanelWire.sessionA)
        guard await fixture.waitForToggle(1, acceptedText: true, stage: "Ainitial") else { return }
        let held = expectation(description: "Alate: an actual A snapshot is retained before switching to B")
        let returned = expectation(description: "BafterLate: the retained A response returns while B is selected")
        fixture.wire.holdNextARead(onHeld: { held.fulfill() }, onReturned: { returned.fulfill() })
        lateRead = Task { await fixture.store.refresh() }
        // Signal only after the continuation is installed. The visible panel's
        // own poll may win the read; either request must reach the same barrier.
        await fulfillment(of: [held], timeout: 3)
        guard fixture.wire.readHeld else { return }
        await fixture.select(NotificationPanelWire.sessionB)
        guard await fixture.waitForToggle(0, acceptedText: false, stage: "B") else { return }
        fixture.wire.release()
        await fulfillment(of: [returned], timeout: 3)
        await lateRead?.value
        await fixture.store.refresh()
        guard await fixture.waitForToggle(0, acceptedText: false, stage: "BafterLate") else { return }
        await fixture.select(NotificationPanelWire.sessionA)
        guard await fixture.waitForToggle(1, acceptedText: true, stage: "Afinal") else { return }
        XCTAssertEqual(fixture.wire.mutationCount, 0)
        XCTAssertEqual(fixture.store.scope?.sessionID, NotificationPanelWire.sessionA)
    }
}
