import AegisAudioCore
import AppKit
import ApplicationServices
import Carbon.HIToolbox
import CoreGraphics
import Darwin
import Foundation
import ScreenCaptureKit

private enum HelperFailure: String, Error {
    case accessibilityPermissionRequired = "accessibility_permission_required"
    case applicationUnavailable = "application_unavailable"
    case captureFailed = "capture_failed"
    case frontmostApplicationMismatch = "frontmost_application_mismatch"
    case invalidCommand = "invalid_command"
    case screenCapturePermissionRequired = "screen_capture_permission_required"
    case sensitiveTargetBlocked = "sensitive_target_blocked"
    case unsafeTarget = "unsafe_target"
}

@main
private enum JarvisComputerHelper {
    static func main() async {
        if CommandLine.arguments.dropFirst().contains("--request-permissions") {
            requestPermissions()
            return
        }
        do {
            let command = try ComputerControlCommand.decode(
                FileHandle.standardInput.readDataToEndOfFile()
            )
            let result = try await execute(command)
            write(result)
        } catch let failure as HelperFailure {
            write(["status": "error", "reason": failure.rawValue])
            exit(1)
        } catch {
            write(["status": "error", "reason": HelperFailure.invalidCommand.rawValue])
            exit(1)
        }
    }

    private static func execute(_ command: ComputerControlCommand) async throws -> [String: Any] {
        switch command.command {
        case "status":
            return statusPayload()
        case "activate":
            guard let bundleIdentifier = command.bundleIdentifier else {
                throw HelperFailure.invalidCommand
            }
            try await activate(bundleIdentifier)
            return successPayload()
        case "capture":
            guard let expected = command.expectedBundleIdentifier else {
                throw HelperFailure.invalidCommand
            }
            return try await capture(expectedBundleIdentifier: expected)
        case "act":
            guard let expected = command.expectedBundleIdentifier else {
                throw HelperFailure.invalidCommand
            }
            try act(command, expectedBundleIdentifier: expected)
            return successPayload()
        default:
            throw HelperFailure.invalidCommand
        }
    }

    private static func requestPermissions() {
        _ = CGRequestScreenCaptureAccess()
        _ = AXIsProcessTrustedWithOptions(
            ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        )
        write(statusPayload())
    }

    private static func statusPayload() -> [String: Any] {
        [
            "status": "ok",
            "screen_capture": CGPreflightScreenCaptureAccess(),
            "accessibility": AXIsProcessTrusted(),
            "frontmost_bundle_identifier": frontmostBundleIdentifier() ?? NSNull(),
        ]
    }

    private static func successPayload() -> [String: Any] {
        [
            "status": "ok",
            "frontmost_bundle_identifier": frontmostBundleIdentifier() ?? NSNull(),
        ]
    }

    private static func activate(_ bundleIdentifier: String) async throws {
        guard !ComputerControlSafety.isRestrictedBundleIdentifier(bundleIdentifier) else {
            throw HelperFailure.unsafeTarget
        }
        let application: NSRunningApplication
        if let running = NSRunningApplication.runningApplications(
            withBundleIdentifier: bundleIdentifier
        ).first {
            application = running
        } else {
            guard let applicationURL = NSWorkspace.shared.urlForApplication(
                withBundleIdentifier: bundleIdentifier
            ) else {
                throw HelperFailure.applicationUnavailable
            }
            let configuration = NSWorkspace.OpenConfiguration()
            configuration.activates = true
            configuration.addsToRecentItems = false
            application = try await withCheckedThrowingContinuation {
                (continuation: CheckedContinuation<NSRunningApplication, any Error>) in
                NSWorkspace.shared.openApplication(
                    at: applicationURL,
                    configuration: configuration
                ) { application, error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else if let application {
                        continuation.resume(returning: application)
                    } else {
                        continuation.resume(throwing: HelperFailure.applicationUnavailable)
                    }
                }
            }
        }
        for attempt in 0 ..< 75 {
            if frontmostBundleIdentifier() == bundleIdentifier {
                return
            }
            if attempt.isMultiple(of: 5) {
                _ = application.activate(options: [.activateAllWindows])
            }
            try await Task.sleep(for: .milliseconds(200))
        }
        throw HelperFailure.frontmostApplicationMismatch
    }

    private static func capture(expectedBundleIdentifier: String) async throws -> [String: Any] {
        try requireFrontmost(expectedBundleIdentifier)
        guard CGPreflightScreenCaptureAccess() else {
            throw HelperFailure.screenCapturePermissionRequired
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false,
                onScreenWindowsOnly: true
            )
            guard
                let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() })
                    ?? content.displays.first,
                display.width > 0,
                display.height > 0
            else {
                throw HelperFailure.captureFailed
            }
            let excludedIdentifiers: Set<String> = [
                "ai.aegis.menubar", "ai.aegis.menubar.computer",
            ]
            let excluded = content.applications.filter {
                excludedIdentifiers.contains($0.bundleIdentifier)
            }
            let filter = SCContentFilter(
                display: display,
                excludingApplications: excluded,
                exceptingWindows: []
            )
            if #available(macOS 14.2, *) {
                filter.includeMenuBar = true
            }
            let configuration = SCStreamConfiguration()
            if display.width >= display.height {
                configuration.width = 1_024
                configuration.height = max(1, 1_024 * display.height / display.width)
            } else {
                configuration.width = max(1, 1_024 * display.width / display.height)
                configuration.height = 1_024
            }
            configuration.scalesToFit = true
            configuration.preservesAspectRatio = true
            configuration.showsCursor = false
            configuration.capturesAudio = false
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            try requireFrontmost(expectedBundleIdentifier)
            let attachment = try LocalImageEncoder.encodeImage(image)
            return [
                "status": "ok",
                "media_type": attachment.mediaType,
                "data_base64": attachment.data.base64EncodedString(),
                "frontmost_bundle_identifier": expectedBundleIdentifier,
            ]
        } catch let failure as HelperFailure {
            throw failure
        } catch {
            throw HelperFailure.captureFailed
        }
    }

    private static func act(
        _ command: ComputerControlCommand,
        expectedBundleIdentifier: String
    ) throws {
        guard AXIsProcessTrusted() else {
            throw HelperFailure.accessibilityPermissionRequired
        }
        try requireFrontmost(expectedBundleIdentifier)
        switch command.action {
        case "click":
            try click(command, expectedBundleIdentifier: expectedBundleIdentifier)
        case "type":
            try typeText(command.text, expectedBundleIdentifier: expectedBundleIdentifier)
        case "key":
            try pressKey(
                command.key,
                modifiers: command.modifiers ?? [],
                expectedBundleIdentifier: expectedBundleIdentifier
            )
        case "scroll":
            try scroll(command.direction, amount: command.amount)
        default:
            throw HelperFailure.invalidCommand
        }
        try requireFrontmost(expectedBundleIdentifier)
    }

    private static func click(
        _ command: ComputerControlCommand,
        expectedBundleIdentifier: String
    ) throws {
        guard
            let normalizedX = command.x,
            let normalizedY = command.y,
            let clickCount = command.clickCount,
            let buttonName = command.button
        else {
            throw HelperFailure.invalidCommand
        }
        let bounds = CGDisplayBounds(CGMainDisplayID())
        let point = CGPoint(
            x: bounds.minX + bounds.width * CGFloat(normalizedX) / 1_000,
            y: bounds.minY + bounds.height * CGFloat(normalizedY) / 1_000
        )
        let element = try element(at: point, expectedBundleIdentifier: expectedBundleIdentifier)
        let descriptor = elementDescriptor(element)
        guard !descriptor.isEmpty else { throw HelperFailure.unsafeTarget }
        if ComputerControlSafety.isSensitiveElementText(descriptor) {
            throw HelperFailure.sensitiveTargetBlocked
        }
        guard
            buttonName == "left",
            clickCount == 1,
            let pressable = pressableElement(
                from: element,
                expectedBundleIdentifier: expectedBundleIdentifier
            )
        else {
            throw HelperFailure.unsafeTarget
        }
        let pressableDescriptor = elementDescriptor(pressable)
        if ComputerControlSafety.isSensitiveElementText(pressableDescriptor) {
            throw HelperFailure.sensitiveTargetBlocked
        }
        guard AXUIElementPerformAction(pressable, kAXPressAction as CFString) == .success else {
            throw HelperFailure.unsafeTarget
        }
        usleep(120_000)
    }

    private static func typeText(
        _ text: String?,
        expectedBundleIdentifier: String
    ) throws {
        guard let text else { throw HelperFailure.invalidCommand }
        let element = try focusedElement(expectedBundleIdentifier: expectedBundleIdentifier)
        let role = attribute(element, kAXRoleAttribute as CFString) ?? ""
        let subrole = attribute(element, kAXSubroleAttribute as CFString) ?? ""
        let descriptor = elementDescriptor(element)
        guard
            ComputerControlSafety.isAllowedTextRole(role),
            subrole != "AXSecureTextField",
            !ComputerControlSafety.isSensitiveElementText(descriptor)
        else {
            throw HelperFailure.sensitiveTargetBlocked
        }
        let source = CGEventSource(stateID: .hidSystemState)
        let units = Array(text.utf16)
        for start in stride(from: 0, to: units.count, by: 20) {
            let chunk = Array(units[start ..< min(start + 20, units.count)])
            guard
                let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
                let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
            else {
                throw HelperFailure.unsafeTarget
            }
            chunk.withUnsafeBufferPointer { buffer in
                down.keyboardSetUnicodeString(
                    stringLength: buffer.count,
                    unicodeString: buffer.baseAddress
                )
                up.keyboardSetUnicodeString(
                    stringLength: buffer.count,
                    unicodeString: buffer.baseAddress
                )
            }
            down.post(tap: .cghidEventTap)
            up.post(tap: .cghidEventTap)
            usleep(20_000)
        }
    }

    private static func pressKey(
        _ key: String?,
        modifiers: [String],
        expectedBundleIdentifier: String
    ) throws {
        guard let key, let keyCode = keyCode(key) else { throw HelperFailure.invalidCommand }
        if modifiers.isEmpty, ["enter", "space"].contains(key) {
            let focused = try focusedElement(
                expectedBundleIdentifier: expectedBundleIdentifier
            )
            let descriptor = elementDescriptor(focused)
            guard
                !descriptor.isEmpty,
                !ComputerControlSafety.isSensitiveElementText(descriptor)
            else {
                throw HelperFailure.sensitiveTargetBlocked
            }
        }
        let flags = modifiers.reduce(into: CGEventFlags()) { result, modifier in
            switch modifier {
            case "command": result.insert(.maskCommand)
            case "control": result.insert(.maskControl)
            case "option": result.insert(.maskAlternate)
            case "shift": result.insert(.maskShift)
            default: break
            }
        }
        let source = CGEventSource(stateID: .hidSystemState)
        guard
            let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true),
            let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false)
        else {
            throw HelperFailure.unsafeTarget
        }
        down.flags = flags
        up.flags = flags
        down.post(tap: .cghidEventTap)
        usleep(40_000)
        up.post(tap: .cghidEventTap)
    }

    private static func scroll(_ direction: String?, amount: Int?) throws {
        guard let direction, let amount else { throw HelperFailure.invalidCommand }
        let delta = Int32(amount)
        let vertical: Int32 = direction == "up" ? delta : direction == "down" ? -delta : 0
        let horizontal: Int32 = direction == "left" ? delta : direction == "right" ? -delta : 0
        guard let event = CGEvent(
            scrollWheelEvent2Source: CGEventSource(stateID: .hidSystemState),
            units: .line,
            wheelCount: 2,
            wheel1: vertical,
            wheel2: horizontal,
            wheel3: 0
        ) else {
            throw HelperFailure.unsafeTarget
        }
        event.post(tap: .cghidEventTap)
    }

    private static func element(
        at point: CGPoint,
        expectedBundleIdentifier: String
    ) throws -> AXUIElement {
        var target: AXUIElement?
        let status = AXUIElementCopyElementAtPosition(
            AXUIElementCreateSystemWide(),
            Float(point.x),
            Float(point.y),
            &target
        )
        guard status == .success, let target else { throw HelperFailure.unsafeTarget }
        try requireElementOwner(target, expectedBundleIdentifier: expectedBundleIdentifier)
        return target
    }

    private static func focusedElement(
        expectedBundleIdentifier: String
    ) throws -> AXUIElement {
        var value: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(
            AXUIElementCreateSystemWide(),
            kAXFocusedUIElementAttribute as CFString,
            &value
        )
        guard
            status == .success,
            let value,
            CFGetTypeID(value) == AXUIElementGetTypeID()
        else {
            throw HelperFailure.unsafeTarget
        }
        let element = unsafeDowncast(value, to: AXUIElement.self)
        try requireElementOwner(element, expectedBundleIdentifier: expectedBundleIdentifier)
        return element
    }

    private static func requireElementOwner(
        _ element: AXUIElement,
        expectedBundleIdentifier: String
    ) throws {
        var processID: pid_t = 0
        guard
            AXUIElementGetPid(element, &processID) == .success,
            NSRunningApplication(processIdentifier: processID)?.bundleIdentifier
                == expectedBundleIdentifier
        else {
            throw HelperFailure.unsafeTarget
        }
    }

    private static func pressableElement(
        from element: AXUIElement,
        expectedBundleIdentifier: String
    ) -> AXUIElement? {
        var candidate = element
        for _ in 0 ..< 8 {
            do {
                try requireElementOwner(
                    candidate,
                    expectedBundleIdentifier: expectedBundleIdentifier
                )
            } catch {
                return nil
            }
            if supportsAction(candidate, kAXPressAction as String) {
                return candidate
            }
            guard let parent = parentElement(candidate) else { return nil }
            candidate = parent
        }
        return nil
    }

    private static func supportsAction(_ element: AXUIElement, _ action: String) -> Bool {
        var names: CFArray?
        guard
            AXUIElementCopyActionNames(element, &names) == .success,
            let names = names as? [String]
        else {
            return false
        }
        return names.contains(action)
    }

    private static func parentElement(_ element: AXUIElement) -> AXUIElement? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                element,
                kAXParentAttribute as CFString,
                &value
            ) == .success,
            let value,
            CFGetTypeID(value) == AXUIElementGetTypeID()
        else {
            return nil
        }
        return unsafeDowncast(value, to: AXUIElement.self)
    }

    private static func elementDescriptor(_ element: AXUIElement) -> String {
        [
            kAXTitleAttribute,
            kAXDescriptionAttribute,
            kAXHelpAttribute,
            kAXIdentifierAttribute,
            kAXRoleDescriptionAttribute,
            kAXValueAttribute,
        ]
        .compactMap { attribute(element, $0 as CFString) }
        .joined(separator: " ")
        .prefix(2_048)
        .description
    }

    private static func attribute(_ element: AXUIElement, _ name: CFString) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name, &value) == .success else {
            return nil
        }
        return value as? String
    }

    private static func requireFrontmost(_ expectedBundleIdentifier: String) throws {
        guard
            !ComputerControlSafety.isRestrictedBundleIdentifier(expectedBundleIdentifier),
            frontmostBundleIdentifier() == expectedBundleIdentifier
        else {
            throw HelperFailure.frontmostApplicationMismatch
        }
    }

    private static func frontmostBundleIdentifier() -> String? {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier
    }

    private static func keyCode(_ value: String) -> CGKeyCode? {
        let named: [String: Int] = [
            "enter": kVK_Return,
            "escape": kVK_Escape,
            "tab": kVK_Tab,
            "space": kVK_Space,
            "left": kVK_LeftArrow,
            "right": kVK_RightArrow,
            "up": kVK_UpArrow,
            "down": kVK_DownArrow,
            "home": kVK_Home,
            "end": kVK_End,
            "page_up": kVK_PageUp,
            "page_down": kVK_PageDown,
        ]
        let letters: [String: Int] = [
            "a": kVK_ANSI_A, "b": kVK_ANSI_B, "c": kVK_ANSI_C, "d": kVK_ANSI_D,
            "e": kVK_ANSI_E, "f": kVK_ANSI_F, "g": kVK_ANSI_G, "h": kVK_ANSI_H,
            "i": kVK_ANSI_I, "j": kVK_ANSI_J, "k": kVK_ANSI_K, "l": kVK_ANSI_L,
            "m": kVK_ANSI_M, "n": kVK_ANSI_N, "o": kVK_ANSI_O, "p": kVK_ANSI_P,
            "q": kVK_ANSI_Q, "r": kVK_ANSI_R, "s": kVK_ANSI_S, "t": kVK_ANSI_T,
            "u": kVK_ANSI_U, "v": kVK_ANSI_V, "w": kVK_ANSI_W, "x": kVK_ANSI_X,
            "y": kVK_ANSI_Y, "z": kVK_ANSI_Z,
        ]
        return (named[value] ?? letters[value]).map(CGKeyCode.init)
    }

    private static func write(_ object: [String: Any]) {
        guard
            JSONSerialization.isValidJSONObject(object),
            let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
            data.count <= 65_536
        else {
            exit(1)
        }
        FileHandle.standardOutput.write(data)
    }
}
