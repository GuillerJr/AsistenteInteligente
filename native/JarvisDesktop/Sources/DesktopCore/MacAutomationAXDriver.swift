import AppKit
import ApplicationServices
import CoreGraphics
import CryptoKit
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

/// Public macOS AX and ScreenCaptureKit APIs only. No AppleScript, shell input
/// synthesis, global desktop screenshot, pasteboard read, or implicit app choice.
@MainActor public final class MacAutomationAXDriver: MacAutomationDriver {
    static let ownerOnlyBundles: Set<String> = ["com.apple.systempreferences",
        "com.apple.keychainaccess", "com.apple.SecurityAgent", "com.apple.authorizationhost",
        "com.apple.loginwindow"]
    struct WindowRecord {
        let number: CGWindowID
        let ownerPID: Int32
        let bounds: CGRect
        let title: String?
    }
    private struct Binding {
        let target: MacAutomationTarget
        let element: AXUIElement
        let windowNumber: CGWindowID
        var created: TimeInterval
        var elements: [(String, AXUIElement)] = []
    }
    private var bindings: [String: Binding] = [:]
    private let allowOwnTestWindow: Bool
    public init(allowOwnTestWindow: Bool = false) { self.allowOwnTestWindow = allowOwnTestWindow }

    static func allowsOwnWindow(identifier: String?, allowTestWindow: Bool,
                                hasModalWindow: Bool, hasSheet: Bool) -> Bool {
        guard !hasModalWindow, !hasSheet, let identifier else { return false }
        if identifier == "jarvis.automation.fixture" { return allowTestWindow }
        return identifier.hasPrefix("jarvis.browser.") && identifier.count > "jarvis.browser.".count
    }

    /// Native identifiers are assigned by this host, never by page/model text.
    /// Only its private browser windows (or an explicitly enabled QA fixture)
    /// can be observed. Approval, settings and chat windows stay owner-only.
    private func allowedOwnWindowNumbers(_ target: MacAutomationTarget) -> Set<CGWindowID>? {
        guard target.pid == getpid() else { return nil }
        return Set(NSApp.windows.compactMap { window in
            guard window.windowNumber > 0, isOwnWindowAdmitted(window)
            else { return nil }
            return CGWindowID(window.windowNumber)
        })
    }

    func isOwnWindowAdmitted(_ window: NSWindow) -> Bool {
        Self.allowsOwnWindow(identifier: window.identifier?.rawValue,
            allowTestWindow: allowOwnTestWindow, hasModalWindow: NSApp.modalWindow != nil,
            hasSheet: window.attachedSheet != nil || window.sheetParent != nil)
    }

    private func validateOwnWindow(_ target: MacAutomationTarget, number: CGWindowID) throws {
        if let allowed = allowedOwnWindowNumbers(target), !allowed.contains(number) {
            throw MacAutomationFailure("owner_only_window")
        }
    }
    public func reset() { bindings.removeAll() }
    public func status() -> RPCValue {
        .object(["schema": .string("jarvis.desktop.v1"),
            "accessibility": .bool(AXIsProcessTrusted()),
            "screen_recording": .bool(CGPreflightScreenCaptureAccess()),
            "automatic_permission_prompt": .bool(false),
            "frame_storage": .string("memory_only"), "supported": .bool(true)])
    }

    public func resolve(bundleID: String) throws -> [MacAutomationTarget] {
        guard !Self.ownerOnlyBundles.contains(bundleID) else { throw MacAutomationFailure("owner_only_target") }
        guard !bundleID.isEmpty, bundleID.utf8.count <= 256 else { throw MacAutomationFailure("invalid_target") }
        return try NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .filter { !$0.isTerminated }.map { try target($0) }.sorted { $0.pid < $1.pid }
    }
    public func validate(_ target: MacAutomationTarget) throws {
        guard !Self.ownerOnlyBundles.contains(target.bundleID) else { throw MacAutomationFailure("owner_only_target") }
        guard let app = NSRunningApplication(processIdentifier: target.pid),
              !app.isTerminated, try self.target(app) == target
        else { throw MacAutomationFailure("target_changed") }
    }
    private func target(_ app: NSRunningApplication) throws -> MacAutomationTarget {
        guard let bundle = app.bundleIdentifier, let launch = app.launchDate,
              let executable = app.executableURL, app.processIdentifier > 0
        else { throw MacAutomationFailure("target_changed") }
        let source = "\(bundle)\n\(app.processIdentifier)\n\(launch.timeIntervalSince1970.bitPattern)\n\(executable.path)"
        let digest = SHA256.hash(data: Data(source.utf8)).map { String(format: "%02x", $0) }.joined()
        return MacAutomationTarget(bundleID: bundle, pid: app.processIdentifier, launchID: digest)
    }
    private func application(_ target: MacAutomationTarget) throws -> AXUIElement {
        try validate(target)
        guard AXIsProcessTrusted() else { throw MacAutomationFailure("accessibility_permission_required") }
        let app = AXUIElementCreateApplication(target.pid)
        AXUIElementSetMessagingTimeout(app, 0.1)
        return app
    }

    public func windows(_ target: MacAutomationTarget) throws -> [MacAutomationWindow] {
        let app = try application(target)
        guard let all = copy(app, kAXWindowsAttribute) as? [AXUIElement], all.count <= 32
        else { throw MacAutomationFailure("windows_unavailable") }
        let time = ProcessInfo.processInfo.systemUptime
        bindings = bindings.filter { entry in entry.value.target != target
            || all.contains(where: { CFEqual($0, entry.value.element) }) }
        var result: [MacAutomationWindow] = []
        var numbers: Set<CGWindowID> = []
        let records = windowRecords(target)
        let ownAllowed = allowedOwnWindowNumbers(target)
        for window in all {
            AXUIElementSetMessagingTimeout(window, 0.1)
            // For our PID, geometry may select one allowed native window before
            // any AX title/label is read from other Jarvis windows.
            if target.pid == getpid() {
                let rect = try rectangle(window)
                let matches = records.filter { Self.sameBounds($0.bounds, rect) }
                let axMatches = all.filter { other in
                    (try? rectangle(other)).map { Self.sameBounds($0, rect) } == true
                }
                guard matches.count == 1, axMatches.count == 1,
                      ownAllowed?.contains(matches[0].number) == true else { continue }
            }
            let title = string(window, kAXTitleAttribute) ?? ""
            let number = try Self.resolveWindowNumber(target: target, bounds: rectangle(window), title: title, records: records)
            guard numbers.insert(number).inserted else { throw MacAutomationFailure("window_ambiguous") }
            let existing = bindings.first { $0.value.target == target && CFEqual($0.value.element, window) }?.key
            guard existing != nil || bindings.count < 128 else { throw MacAutomationFailure("window_capacity") }
            let id = stableWindowReference(target: target, number: number)
            if existing != id { bindings.removeValue(forKey: existing ?? "") }
            if bindings[id] == nil { bindings[id] = Binding(target: target, element: window, windowNumber: number, created: time) }
            result.append(MacAutomationWindow(id: id, title: bounded(title, 256)))
        }
        return result
    }
    private func binding(_ target: MacAutomationTarget, windowID: String) throws -> Binding {
        let app = try application(target)
        guard let binding = bindings[windowID], binding.target == target,
              let windows = copy(app, kAXWindowsAttribute) as? [AXUIElement],
              windows.contains(where: { CFEqual($0, binding.element) })
        else { throw MacAutomationFailure("target_changed") }
        try validateOwnWindow(target, number: binding.windowNumber)
        let records = windowRecords(target)
        if target.pid == getpid() {
            let rect = try rectangle(binding.element)
            guard records.filter({ Self.sameBounds($0.bounds, rect) }).count == 1,
                  windows.filter({ other in
                    (try? rectangle(other)).map { Self.sameBounds($0, rect) } == true
                  }).count == 1 else { throw MacAutomationFailure("window_ambiguous") }
        }
        let number = try Self.resolveWindowNumber(target: target, bounds: rectangle(binding.element),
            title: string(binding.element, kAXTitleAttribute) ?? "", records: records)
        guard number == binding.windowNumber, windowID == stableWindowReference(target: target, number: number)
        else { throw MacAutomationFailure("target_changed") }
        return binding
    }

    func stableWindowReference(target: MacAutomationTarget, number: CGWindowID) -> String {
        let source = "jarvis.desktop.window.v1\n\(target.bundleID)\n\(target.pid)\n\(target.launchID)\n\(number)"
        return SHA256.hash(data: Data(source.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    static func resolveWindowNumber(target: MacAutomationTarget, bounds: CGRect,
                                    title: String, records: [WindowRecord]) throws -> CGWindowID {
        let matching = records.filter { record in
            guard record.ownerPID == target.pid, record.number != kCGNullWindowID else { return false }
            return sameBounds(record.bounds, bounds)
                && (record.title == nil || record.title == title)
        }
        guard matching.count == 1 else { throw MacAutomationFailure("window_ambiguous") }
        return matching[0].number
    }

    private static func sameBounds(_ first: CGRect, _ second: CGRect) -> Bool {
        abs(first.minX - second.minX) < 1 && abs(first.minY - second.minY) < 1
            && abs(first.width - second.width) < 1 && abs(first.height - second.height) < 1
    }

    private func windowRecords(_ target: MacAutomationTarget) -> [WindowRecord] {
        let allowed = allowedOwnWindowNumbers(target)
        guard let rows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]
        else { return [] }
        return rows.compactMap { row in
            // Filter the explicit owner PID before accessing any title or geometry.
            guard let owner = row[kCGWindowOwnerPID as String] as? NSNumber, owner.int32Value == target.pid,
                  let number = row[kCGWindowNumber as String] as? NSNumber,
                  let dictionary = row[kCGWindowBounds as String] as? [String: Any],
                  let bounds = CGRect(dictionaryRepresentation: dictionary as CFDictionary)
            else { return nil }
            return WindowRecord(number: number.uint32Value, ownerPID: owner.int32Value, bounds: bounds,
                title: allowed == nil || allowed!.contains(number.uint32Value)
                    ? row[kCGWindowName as String] as? String : nil)
        }
    }

    public func read(_ target: MacAutomationTarget, windowID: String) throws -> MacAutomationTree {
        var selected = try binding(target, windowID: windowID)
        let bounds = try rectangle(selected.element)
        let deadline = ProcessInfo.processInfo.systemUptime + 2
        let previous = selected.elements
        var found: [(String, AXUIElement)] = [], visited: [AXUIElement] = []
        var nodes: [MacAutomationNode] = [], truncated = false
        var queue: [(AXUIElement, String?, Int)] = [(selected.element, nil, 0)]
        while !queue.isEmpty {
            guard nodes.count < 160, ProcessInfo.processInfo.systemUptime < deadline else { truncated = true; break }
            let (element, parent, depth) = queue.removeFirst()
            if visited.contains(where: { CFEqual($0, element) }) { continue }
            visited.append(element); AXUIElementSetMessagingTimeout(element, 0.1)
            let id = previous.first(where: { CFEqual($0.1, element) })?.0
                ?? UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            found.append((id, element))
            let role = bounded(string(element, kAXRoleAttribute) ?? "AXUnknown", 128)
            let secure = role == "AXUnknown" || isSecure(element, role: role)
            let label = secure ? "Campo protegido" : bounded(
                string(element, kAXTitleAttribute) ?? string(element, kAXDescriptionAttribute) ?? "", 512)
            let rawValue = secure ? nil : string(element, kAXValueAttribute)
            let value = rawValue.map { bounded($0, 12_000) }
            let oversized = rawValue.map { $0.utf8.count > 12_000 } ?? false
            var actions: [String] = []
            if !secure {
                var native: CFArray?
                if AXUIElementCopyActionNames(element, &native) == .success, let names = native as? [String] {
                    for (name, tool) in [(kAXPressAction, "press"), ("AXScrollUpByPage", "scroll_up"),
                        ("AXScrollDownByPage", "scroll_down"), ("AXScrollLeftByPage", "scroll_left"),
                        ("AXScrollRightByPage", "scroll_right")] where names.contains(name) { actions.append(tool) }
                }
                if settable(element, kAXFocusedAttribute) { actions.append("focus") }
                if !oversized, settable(element, kAXValueAttribute), rawValue != nil {
                    actions.append("set_value")
                    if selection(element) != nil { actions.append("type_text") }
                }
                if CFEqual(element, selected.element) { actions += ["click_at", "drag", "key"] }
            }
            nodes.append(MacAutomationNode(id: id, parentID: parent, role: role, label: label,
                value: value, secure: secure, enabled: (copy(element, kAXEnabledAttribute) as? Bool) ?? true,
                valueTruncated: oversized, actions: actions.sorted(), selectedRange: secure ? nil : selection(element).map { [$0.location, $0.length] }))
            if !secure {
                if let children = copy(element, kAXChildrenAttribute) as? [AXUIElement] {
                    if depth < 16 { queue += children.prefix(160).map { ($0, id, depth + 1) } }
                    if children.count > 160 || (depth >= 16 && !children.isEmpty) { truncated = true }
                }
            }
        }
        guard !nodes.isEmpty, nodes[0].role != "AXUnknown" else { throw MacAutomationFailure("observation_unavailable") }
        selected.elements = found; selected.created = ProcessInfo.processInfo.systemUptime; bindings[windowID] = selected
        return MacAutomationTree(nodes: nodes, truncated: truncated,
            bounds: [bounds.origin.x, bounds.origin.y, bounds.width, bounds.height])
    }

    public func perform(_ target: MacAutomationTarget, windowID: String, nodeID: String,
                        action: MacAutomationAction, beforeEffect: @MainActor () async throws -> Void) async throws -> MacAutomationEffect {
        let selected = try binding(target, windowID: windowID)
        func checkedBeforeEffect() async throws {
            try await beforeEffect()
            try validateOwnWindow(target, number: selected.windowNumber)
        }
        guard let element = selected.elements.first(where: { $0.0 == nodeID })?.1
        else { throw MacAutomationFailure("stale_snapshot") }
        guard !isSecure(element) else { throw MacAutomationFailure("secure_element") }
        if ["click_at", "drag", "key"].contains(action.name) {
            return try await input(target, binding: selected, element: element, action: action, beforeEffect: checkedBeforeEffect)
        }
        switch action.name {
        case "set_value", "type_text":
            guard var text = action.value, text.utf8.count <= 8000,
                  settable(element, kAXValueAttribute), let expected = action.expectedValue,
                  string(element, kAXValueAttribute) == expected
            else { throw MacAutomationFailure("unsupported_action") }
            if action.name == "type_text" {
                guard let current = string(element, kAXValueAttribute), current.utf8.count <= 12_000,
                      current == expected,
                      let selected = selection(element), selected.location >= 0, selected.length >= 0,
                      action.expectedSelection == [selected.location, selected.length],
                      selected.location <= (current as NSString).length,
                      selected.length <= (current as NSString).length - selected.location
                else { throw MacAutomationFailure("selection_unavailable") }
                text = (current as NSString).replacingCharacters(in:
                    NSRange(location: selected.location, length: selected.length), with: text)
            }
            guard text.utf8.count <= 12_000 else { throw MacAutomationFailure("native_observation_oversized") }
            try await checkedBeforeEffect(); try validate(target)
            guard string(element, kAXValueAttribute) == expected,
                  action.name != "type_text" || selection(element).map({ [$0.location, $0.length] }) == action.expectedSelection
            else { throw MacAutomationFailure("stale_snapshot") }
            guard AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFString) == .success
            else { throw MacAutomationFailure("native_action_unconfirmed", dispatched: true) }
            let verified = string(element, kAXValueAttribute) == text
            return MacAutomationEffect(verified: verified,
                verification: verified ? "valor_releido_coincide" : "valor_no_confirmado",
                effectStatus: verified ? "known" : "unknown")
        case "focus":
            guard settable(element, kAXFocusedAttribute) else { throw MacAutomationFailure("unsupported_action") }
            try await checkedBeforeEffect(); try validate(target)
            guard AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue) == .success
            else { throw MacAutomationFailure("native_action_unconfirmed", dispatched: true) }
            let verified = (copy(element, kAXFocusedAttribute) as? Bool) == true
            return MacAutomationEffect(verified: verified, verification: "foco_releido",
                effectStatus: verified ? "known" : "unknown")
        default:
            let names = ["press": kAXPressAction, "scroll_up": "AXScrollUpByPage",
                "scroll_down": "AXScrollDownByPage", "scroll_left": "AXScrollLeftByPage", "scroll_right": "AXScrollRightByPage"]
            guard let native = names[action.name] else { throw MacAutomationFailure("unsupported_action") }
            var available: CFArray?
            guard AXUIElementCopyActionNames(element, &available) == .success,
                  (available as? [String])?.contains(native) == true else { throw MacAutomationFailure("unsupported_action") }
            let verification = action.verificationNodeID.flatMap { id in
                selected.elements.first(where: { $0.0 == id })?.1
            }
            let expectsVerification = action.verificationNodeID != nil || action.expectedResult != nil
            if expectsVerification {
                guard verification != nil, action.expectedResult != nil else {
                    throw MacAutomationFailure("invalid_verification")
                }
            }
            try await checkedBeforeEffect(); try validate(target)
            return try await Self.performAXActionOnce(verifyRequested: expectsVerification, dispatch: {
                AXUIElementPerformAction(element, native as CFString) == .success
            }, readback: {
                guard let verification, let expected = action.expectedResult else {
                    throw MacAutomationFailure("invalid_verification")
                }
                return try await Self.confirmPostcondition(expected: expected, read: {
                    guard let role = self.string(verification, kAXRoleAttribute), role != "AXUnknown",
                          !self.isSecure(verification, role: role) else { throw MacAutomationFailure("secure_element") }
                    if !CFEqual(verification, selected.element) {
                        guard let window = self.copy(verification, kAXWindowAttribute),
                              CFGetTypeID(window) == AXUIElementGetTypeID(), CFEqual(window, selected.element)
                        else { throw MacAutomationFailure("target_changed") }
                    }
                    return self.string(verification, kAXValueAttribute)
                }, beforeRead: {
                    try await checkedBeforeEffect()
                    let current = try self.binding(target, windowID: windowID)
                    guard current.windowNumber == selected.windowNumber, CFEqual(current.element, selected.element)
                    else { throw MacAutomationFailure("target_changed") }
                })
            })
        }
    }

    /// Native ACK and the requested postcondition are separate evidence. Send
    /// exactly once; a missing ACK never causes a retry. A later exact readback
    /// verifies only that state, not causality or all future app activity.
    static func performAXActionOnce(verifyRequested: Bool,
                                    dispatch: @MainActor () -> Bool,
                                    readback: @MainActor () async throws -> Bool) async throws -> MacAutomationEffect {
        let acknowledged = dispatch()
        if verifyRequested {
            let matched: Bool
            do { matched = try await readback() }
            catch { throw MacAutomationFailure("result_unconfirmed", dispatched: true) }
            return MacAutomationEffect(verified: matched,
                verification: matched ? "estado_postcondicion_AX_coincide" : "estado_postcondicion_AX_no_confirmado",
                effectStatus: matched ? "known" : "unknown", nativeAcknowledged: acknowledged)
        }
        guard acknowledged else { throw MacAutomationFailure("native_action_unconfirmed", dispatched: true) }
        return MacAutomationEffect(verified: false, verification: "accion_AX_aceptada_sin_verificacion_semantica",
            nativeAcknowledged: true)
    }

    private func input(_ target: MacAutomationTarget, binding: Binding, element: AXUIElement,
                       action: MacAutomationAction, beforeEffect: @MainActor () async throws -> Void) async throws -> MacAutomationEffect {
        guard CFEqual(element, binding.element),
              let app = NSRunningApplication(processIdentifier: target.pid),
              let foregroundPID = NSWorkspace.shared.frontmostApplication?.processIdentifier,
              foregroundPID == target.pid || foregroundPID == getpid()
        else { throw MacAutomationFailure("target_not_foreground") }
        let application = try self.application(target)
        var hasDispatched = false
        do {
        try await beforeEffect()
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == foregroundPID
        else { throw MacAutomationFailure("target_not_foreground") }
        if foregroundPID == getpid() {
            // The explicit owner approval is in this host. Raise only the exact
            // admitted window, never whichever app the user subsequently chose.
            hasDispatched = true
            guard AXUIElementPerformAction(binding.element, kAXRaiseAction as CFString) == .success,
                  app.activate(options: [])
            else { throw MacAutomationFailure("target_not_foreground", dispatched: true) }
            do {
                for _ in 0..<8 {
                    try await beforeEffect()
                    if (try? foreground(target, application: application, window: binding.element)) != nil { break }
                    try await Task.sleep(nanoseconds: 50_000_000)
                }
                try await beforeEffect(); try foreground(target, application: application, window: binding.element)
            } catch { throw MacAutomationFailure("target_changed", dispatched: true) }
        } else { try foreground(target, application: application, window: binding.element) }
        let originalRect = try rectangle(binding.element)
        guard action.expectedBounds == [originalRect.minX, originalRect.minY, originalRect.width, originalRect.height]
        else { throw MacAutomationFailure("stale_snapshot", dispatched: foregroundPID == getpid()) }
        try physicalIdle()
        let source = CGEventSource(stateID: .privateState)
        if action.name == "key" {
            guard let key = action.key, let code = keyCode(key) else { throw MacAutomationFailure("unsupported_action") }
            guard let down = CGEvent(keyboardEventSource: source, virtualKey: code.0, keyDown: true),
                  let up = CGEvent(keyboardEventSource: source, virtualKey: code.0, keyDown: false)
            else { throw MacAutomationFailure("input_unavailable") }
            down.flags = code.1; up.flags = code.1
            try await beforeEffect(); try foreground(target, application: application, window: binding.element)
            try physicalIdle()
            hasDispatched = true
            down.postToPid(target.pid); up.postToPid(target.pid)
        } else {
            guard let normalized = action.point, normalized.count == 2 else { throw MacAutomationFailure("invalid_arguments") }
            let rect = try rectangle(binding.element)
            guard rect == originalRect else { throw MacAutomationFailure("stale_snapshot", dispatched: foregroundPID == getpid()) }
            let point = CGPoint(x: rect.minX + normalized[0] * (rect.width - 1),
                                y: rect.minY + normalized[1] * (rect.height - 1))
            guard let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left),
                  let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
            else { throw MacAutomationFailure("input_unavailable") }
            try await beforeEffect(); try foreground(target, application: application, window: binding.element)
            try physicalIdle()
            hasDispatched = true
            down.postToPid(target.pid)
            defer { up.postToPid(target.pid) }
            if action.name == "drag" {
                guard let end = action.endPoint, end.count == 2 else { throw MacAutomationFailure("invalid_arguments", dispatched: true) }
                let destination = CGPoint(x: rect.minX + end[0] * (rect.width - 1), y: rect.minY + end[1] * (rect.height - 1))
                up.location = destination
                for step in 1...8 {
                    do { try await beforeEffect(); try foreground(target, application: application, window: binding.element) }
                    catch { throw MacAutomationFailure("target_changed", dispatched: true) }
                    let fraction = Double(step) / 8
                    let location = CGPoint(x: point.x + (destination.x - point.x) * fraction,
                                           y: point.y + (destination.y - point.y) * fraction)
                    guard let drag = CGEvent(mouseEventSource: source, mouseType: .leftMouseDragged,
                        mouseCursorPosition: location, mouseButton: .left)
                    else { throw MacAutomationFailure("input_unavailable", dispatched: true) }
                    drag.postToPid(target.pid)
                }
            }
        }
        if let verificationID = action.verificationNodeID, let expected = action.expectedResult,
           let verification = binding.elements.first(where: { $0.0 == verificationID })?.1 {
            do {
                let verified = try await Self.confirmPostcondition(expected: expected, read: {
                    guard !self.isSecure(verification) else { throw MacAutomationFailure("secure_element") }
                    return self.string(verification, kAXValueAttribute)
                }, beforeRead: {
                    try await beforeEffect(); try self.foreground(target, application: application, window: binding.element)
                })
                if verified {
                    return MacAutomationEffect(verified: true, verification: "postcondicion_AX_releida_coincide")
                }
            } catch { throw MacAutomationFailure("result_unconfirmed", dispatched: true) }
        }
        return MacAutomationEffect(verified: false, verification: "eventos_dirigidos_al_PID_sin_ACK_del_efecto", effectStatus: "unknown")
        } catch let error as MacAutomationFailure {
            throw MacAutomationFailure(error.code, dispatched: hasDispatched || error.dispatched)
        } catch {
            throw MacAutomationFailure("native_input_unconfirmed", dispatched: hasDispatched)
        }
    }

    static func confirmPostcondition(expected: String, read: @MainActor () throws -> String?,
                                     beforeRead: @MainActor () async throws -> Void,
                                     pause: @MainActor () async throws -> Void = {
                                         try await Task.sleep(nanoseconds: 100_000_000)
                                     }) async throws -> Bool {
        for _ in 0..<8 {
            try await beforeRead()
            if try read() == expected { return true }
            try await pause()
        }
        return false
    }

    private func physicalIdle() throws {
        guard CGEventSource.flagsState(.combinedSessionState).intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).isEmpty,
              !CGEventSource.buttonState(.combinedSessionState, button: .left),
              !CGEventSource.buttonState(.combinedSessionState, button: .right)
        else { throw MacAutomationFailure("physical_input_busy") }
    }

    private func foreground(_ target: MacAutomationTarget, application: AXUIElement, window: AXUIElement) throws {
        try validate(target)
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == target.pid,
              let focusedWindow = copy(application, kAXFocusedWindowAttribute),
              CFGetTypeID(focusedWindow) == AXUIElementGetTypeID(), CFEqual(focusedWindow, window),
              let focused = copy(application, kAXFocusedUIElementAttribute),
              CFGetTypeID(focused) == AXUIElementGetTypeID()
        else { throw MacAutomationFailure("target_not_foreground") }
        let focusedElement = focused as! AXUIElement
        guard let role = string(focusedElement, kAXRoleAttribute), role != "AXUnknown",
              !isSecure(focusedElement, role: role)
        else { throw MacAutomationFailure("secure_element") }
        if !CFEqual(focusedElement, window) {
            guard let focusedOwnerWindow = copy(focusedElement, kAXWindowAttribute),
                  CFGetTypeID(focusedOwnerWindow) == AXUIElementGetTypeID(), CFEqual(focusedOwnerWindow, window)
            else { throw MacAutomationFailure("target_not_foreground") }
        }
    }

    private func keyCode(_ key: String) -> (CGKeyCode, CGEventFlags)? {
        let basic: [String: CGKeyCode] = ["return": 36, "tab": 48, "escape": 53, "space": 49,
            "backspace": 51, "delete": 117, "left": 123, "right": 124, "up": 126, "down": 125,
            "home": 115, "end": 119, "page_up": 116, "page_down": 121]
        if let code = basic[key] { return (code, []) }
        let modified: [String: (CGKeyCode, CGEventFlags)] = ["cmd+a": (0, .maskCommand),
            "cmd+z": (6, .maskCommand), "cmd+shift+z": (6, [.maskCommand, .maskShift]),
            "cmd+s": (1, .maskCommand), "cmd+f": (3, .maskCommand)]
        return modified[key]
    }

    public func frame(_ target: MacAutomationTarget, windowID: String) async throws -> RPCValue {
        var selected = try binding(target, windowID: windowID)
        guard CGPreflightScreenCaptureAccess() else { throw MacAutomationFailure("screen_permission_required") }
        let rect = try rectangle(selected.element)
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        try validate(target)
        // Only owner PID is inspected until the record belongs to the explicit app.
        // Other window names/content are never read, serialized, or selected.
        let matching = content.windows.filter { window in
            guard window.owningApplication?.processID == target.pid else { return false }
            return window.windowID == selected.windowNumber && abs(window.frame.minX - rect.minX) < 1
                && abs(window.frame.minY - rect.minY) < 1 && abs(window.frame.width - rect.width) < 1
                && abs(window.frame.height - rect.height) < 1
        }
        guard matching.count == 1 else { throw MacAutomationFailure("frame_ambiguous") }
        let before = try read(target, windowID: windowID)
        guard !before.truncated else { throw MacAutomationFailure("frame_requires_complete_accessibility") }
        selected = try binding(target, windowID: windowID)
        var redactions: [CGRect] = []
        for (_, element) in selected.elements {
            let role = string(element, kAXRoleAttribute) ?? "AXUnknown"
            if role == "AXUnknown" || isSecure(element, role: role) {
                guard let protected = try? rectangle(element) else { throw MacAutomationFailure("protected_geometry_unavailable") }
                let clipped = protected.insetBy(dx: -4, dy: -4).intersection(rect)
                if !clipped.isNull { redactions.append(clipped) }
            }
        }
        let filter = SCContentFilter(desktopIndependentWindow: matching[0])
        let config = SCStreamConfiguration()
        let ratio = min(1, min(960 / rect.width, 720 / rect.height))
        config.width = max(1, Int(rect.width * ratio)); config.height = max(1, Int(rect.height * ratio))
        config.showsCursor = false; config.capturesAudio = false; config.ignoreShadowsSingleWindow = true
        config.scalesToFit = true; config.preservesAspectRatio = true
        if #available(macOS 14.2, *) { config.includeChildWindows = false }
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        try validate(target)
        guard try rectangle(try binding(target, windowID: windowID).element) == rect else { throw MacAutomationFailure("target_changed") }
        let after = try read(target, windowID: windowID)
        guard !after.truncated, before.digest == after.digest else { throw MacAutomationFailure("frame_changed") }
        let safeImage = try Self.redactFrame(image, window: rect, protectedRects: redactions)
        let bytes = try Self.encodeFrame(safeImage)
        let hash = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        return .object(["mime_type": .string("image/jpeg"), "data_url": .string("data:image/jpeg;base64," + bytes.base64EncodedString()),
            "bytes": .number(Double(bytes.count)), "sha256": .string(hash),
            "storage": .string("memory_only"), "source": .string("screen_capture_kit_window"),
            "window_id": .number(Double(matching[0].windowID)), "atomic_with_ax": .bool(false),
            "protected_regions_redacted": .number(Double(redactions.count))])
    }
    static func redactFrame(_ image: CGImage, window: CGRect, protectedRects: [CGRect]) throws -> CGImage {
        guard let context = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
        else { throw MacAutomationFailure("frame_unavailable") }
        context.draw(image, in: CGRect(x: 0, y: 0, width: image.width, height: image.height))
        let xScale = Double(image.width) / window.width, yScale = Double(image.height) / window.height
        context.setFillColor(CGColor(gray: 0, alpha: 1))
        for rect in protectedRects {
            context.fill(CGRect(x: floor((rect.minX - window.minX) * xScale),
                y: floor(Double(image.height) - (rect.maxY - window.minY) * yScale),
                width: ceil(rect.width * xScale), height: ceil(rect.height * yScale)))
        }
        guard let result = context.makeImage() else { throw MacAutomationFailure("frame_unavailable") }
        return result
    }
    static func encodeFrame(_ image: CGImage) throws -> Data {
        for scale in [1.0, 0.65, 0.4, 0.25] {
            let width = max(1, Int(Double(image.width) * scale)), height = max(1, Int(Double(image.height) * scale))
            guard let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { throw MacAutomationFailure("frame_unavailable") }
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            guard let resized = context.makeImage() else { throw MacAutomationFailure("frame_unavailable") }
            let bytes = NSMutableData()
            guard let destination = CGImageDestinationCreateWithData(bytes, UTType.jpeg.identifier as CFString, 1, nil)
            else { throw MacAutomationFailure("frame_unavailable") }
            CGImageDestinationAddImage(destination, resized,
                [kCGImageDestinationLossyCompressionQuality: 0.6] as CFDictionary)
            guard CGImageDestinationFinalize(destination) else { throw MacAutomationFailure("frame_unavailable") }
            if bytes.length <= 48 * 1024 { return bytes as Data }
        }
        throw MacAutomationFailure("frame_oversized")
    }

    private func isSecure(_ element: AXUIElement, role: String? = nil) -> Bool {
        (role ?? string(element, kAXRoleAttribute) ?? "").contains("Secure")
            || (string(element, kAXSubroleAttribute) ?? "").contains("Secure")
            || (copy(element, "AXProtectedContent") as? Bool) == true
    }
    private func copy(_ element: AXUIElement, _ attribute: String) -> CFTypeRef? {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(element, attribute as CFString, &value) == .success ? value : nil
    }
    private func string(_ element: AXUIElement, _ attribute: String) -> String? { copy(element, attribute) as? String }
    private func settable(_ element: AXUIElement, _ attribute: String) -> Bool {
        var result = DarwinBoolean(false)
        return AXUIElementIsAttributeSettable(element, attribute as CFString, &result) == .success && result.boolValue
    }
    private func selection(_ element: AXUIElement) -> CFRange? {
        guard let raw = copy(element, kAXSelectedTextRangeAttribute), CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
        let value = raw as! AXValue; var range = CFRange()
        return AXValueGetType(value) == .cfRange && AXValueGetValue(value, .cfRange, &range) ? range : nil
    }
    private func rectangle(_ element: AXUIElement) throws -> CGRect {
        guard let rawPosition = copy(element, kAXPositionAttribute), CFGetTypeID(rawPosition) == AXValueGetTypeID(),
              let rawSize = copy(element, kAXSizeAttribute), CFGetTypeID(rawSize) == AXValueGetTypeID()
        else { throw MacAutomationFailure("window_geometry_unavailable") }
        var point = CGPoint.zero, size = CGSize.zero
        guard AXValueGetValue(rawPosition as! AXValue, .cgPoint, &point),
              AXValueGetValue(rawSize as! AXValue, .cgSize, &size),
              [point.x, point.y, size.width, size.height].allSatisfy({ $0.isFinite && abs($0) <= 65_536 }),
              size.width > 0, size.height > 0 else { throw MacAutomationFailure("window_geometry_unavailable") }
        return CGRect(origin: point, size: size)
    }
    private func bounded(_ string: String, _ maximum: Int) -> String {
        var value = String(decoding: string.utf8.prefix(maximum), as: UTF8.self)
        while value.utf8.count > maximum { value.removeLast() }; return value
    }
}
