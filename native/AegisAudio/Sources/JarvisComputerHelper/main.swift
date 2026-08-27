import AegisAudioCore
import AppKit
import ApplicationServices
import Carbon.HIToolbox
import CoreGraphics
import Darwin
import Foundation
import ScreenCaptureKit
import Vision

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

private struct ComputerProcessTarget {
    let bundleIdentifier: String
    let launchDate: Date
    let processIdentifier: pid_t
}

private struct ComputerVisualState {
    let display: ComputerDisplayCandidate
    let token: String
}

@main
private enum JarvisComputerHelper {
    static func main() async {
        let arguments = CommandLine.arguments.dropFirst()
        if arguments.contains("--request-screen-capture") {
            requestScreenCapturePermission()
            return
        }
        if arguments.contains("--request-accessibility") {
            requestAccessibilityPermission()
            return
        }
        if arguments.contains("--request-permissions") {
            _ = CGRequestScreenCaptureAccess()
            _ = AXIsProcessTrustedWithOptions(
                ["AXTrustedCheckOptionPrompt": true] as CFDictionary
            )
            write(statusPayload())
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
            let displayIdentifier = try act(
                command,
                expectedBundleIdentifier: expected
            )
            return successPayload(displayIdentifier: displayIdentifier)
        default:
            throw HelperFailure.invalidCommand
        }
    }

    private static func requestScreenCapturePermission() {
        _ = CGRequestScreenCaptureAccess()
        write(statusPayload())
    }

    private static func requestAccessibilityPermission() {
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

    private static func successPayload(
        displayIdentifier: CGDirectDisplayID? = nil
    ) -> [String: Any] {
        var payload: [String: Any] = [
            "status": "ok",
            "frontmost_bundle_identifier": frontmostBundleIdentifier() ?? NSNull(),
        ]
        if let displayIdentifier {
            payload["display_identifier"] = Int(displayIdentifier)
        }
        return payload
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
        let target = try processTarget(expectedBundleIdentifier)
        guard CGPreflightScreenCaptureAccess() else {
            throw HelperFailure.screenCapturePermissionRequired
        }
        guard AXIsProcessTrusted() else {
            throw HelperFailure.accessibilityPermissionRequired
        }
        guard setAccessibilityTimeout(
            ComputerControlAccessibilityPolicy.perceptionMessagingTimeoutSeconds
        ) else {
            throw HelperFailure.unsafeTarget
        }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(
                false,
                onScreenWindowsOnly: true
            )
            let window = try focusedWindow(target: target)
            guard content.displays.count <= ComputerDisplayPlan.maximumActiveDisplays else {
                throw HelperFailure.unsafeTarget
            }
            let displayTarget = try displayTarget(
                for: window,
                candidates: content.displays.map {
                    ComputerDisplayCandidate(
                        identifier: $0.displayID,
                        bounds: CGDisplayBounds($0.displayID)
                    )
                },
                target: target
            )
            let initialVisualContext = try visualToken(
                for: window,
                display: displayTarget,
                target: target
            )
            guard
                let display = content.displays.first(where: {
                    $0.displayID == displayTarget.identifier
                }),
                display.width > 0,
                display.height > 0
            else {
                throw HelperFailure.captureFailed
            }
            guard let includedApplication = content.applications.first(where: {
                $0.bundleIdentifier == target.bundleIdentifier
                    && $0.processID == target.processIdentifier
            }) else {
                throw HelperFailure.applicationUnavailable
            }
            try requireProcessTarget(target)
            let filter = SCContentFilter(
                display: display,
                including: [includedApplication],
                exceptingWindows: []
            )
            if #available(macOS 14.2, *) {
                filter.includeMenuBar = ComputerControlCapturePolicy.includeMenuBar
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
            configuration.showsCursor = ComputerControlCapturePolicy.showCursor
            configuration.capturesAudio = ComputerControlCapturePolicy.captureAudio
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            try requireProcessTarget(target)
            guard let visualSignature = ComputerVisualFingerprint.make(from: image) else {
                throw HelperFailure.captureFailed
            }
            let attachment = try LocalImageEncoder.encodeImage(image)
            let perception = localPerception(
                image: image,
                target: target,
                displayBounds: displayTarget.bounds
            )
            try requireProcessTarget(target)
            let finalVisualState = try visualState(target: target)
            guard
                finalVisualState.display.identifier == displayTarget.identifier,
                finalVisualState.token == initialVisualContext
            else {
                throw HelperFailure.unsafeTarget
            }
            return [
                "status": "ok",
                "media_type": attachment.mediaType,
                "data_base64": attachment.data.base64EncodedString(),
                "visual_signature": visualSignature,
                "visual_context": initialVisualContext,
                "frontmost_bundle_identifier": expectedBundleIdentifier,
                "local_perception": perception,
            ]
        } catch let failure as HelperFailure {
            throw failure
        } catch {
            throw HelperFailure.captureFailed
        }
    }

    private static func localPerception(
        image: CGImage,
        target: ComputerProcessTarget,
        displayBounds: CGRect
    ) -> [String: Any] {
        let accessibility = accessibilityPerception(
            target: target,
            displayBounds: displayBounds
        )
        let vision = visionPerception(image: image)
        let secureContent = accessibility.items.contains {
            $0["secure"] as? Bool == true
        } || vision.items.contains {
            $0["sensitive"] as? Bool == true
        }
        let combined = Array((accessibility.items + vision.items).prefix(48)).map { item in
            var sanitized = item
            sanitized.removeValue(forKey: "secure")
            return sanitized
        }
        return [
            "windows": accessibility.windows,
            "items": combined,
            "secure_content": secureContent,
            "truncated": accessibility.truncated
                || vision.truncated
                || accessibility.items.count + vision.items.count > 48,
        ]
    }

    private static func accessibilityPerception(
        target: ComputerProcessTarget,
        displayBounds: CGRect
    ) -> (windows: [String], items: [[String: Any]], truncated: Bool) {
        guard AXIsProcessTrusted() else { return ([], [], false) }
        guard setAccessibilityTimeout(
            ComputerControlAccessibilityPolicy.perceptionMessagingTimeoutSeconds
        ) else {
            return ([], [], true)
        }
        let deadline = ContinuousClock.now.advanced(
            by: .milliseconds(ComputerControlAccessibilityPolicy.perceptionBudgetMilliseconds)
        )
        let application = AXUIElementCreateApplication(target.processIdentifier)
        let windows = elementArray(application, kAXWindowsAttribute as CFString).filter {
            elementBelongsToProcess($0, target: target)
                && elementIntersectsDisplay($0, displayBounds: displayBounds)
        }
        var windowTitles: [String] = []
        var items: [[String: Any]] = []
        var queue = windows.map { ($0, 0) }
        var visited: Set<CFHashCode> = []
        var truncated = false

        for window in windows.prefix(ComputerControlAccessibilityPolicy.maximumWindows) {
            guard ContinuousClock.now < deadline else {
                truncated = true
                break
            }
            if let title = boundedText(attribute(window, kAXTitleAttribute as CFString)),
               !windowTitles.contains(title)
            {
                windowTitles.append(title)
            }
        }

        while
            !queue.isEmpty,
            items.count < ComputerControlAccessibilityPolicy.maximumItems,
            visited.count < ComputerControlAccessibilityPolicy.maximumElements,
            ContinuousClock.now < deadline
        {
            let (element, depth) = queue.removeFirst()
            guard elementBelongsToProcess(element, target: target) else {
                truncated = true
                continue
            }
            let identity = CFHash(element)
            guard visited.insert(identity).inserted else { continue }
            let role = attribute(element, kAXRoleAttribute as CFString) ?? ""
            let subrole = attribute(element, kAXSubroleAttribute as CFString) ?? ""
            let secure = subrole == "AXSecureTextField"
            let text = perceptionDescriptor(element, includeValue: !secure)
            let pressable = supportsAction(element, kAXPressAction as String)
            let includedRoles: Set<String> = [
                "AXButton", "AXCheckBox", "AXComboBox", "AXHeading", "AXLink",
                "AXMenuItem", "AXPopUpButton", "AXRadioButton", "AXSearchField",
                "AXStaticText", "AXTextArea", "AXTextField",
            ]
            if includedRoles.contains(role), let text = boundedText(text), !text.isEmpty {
                let point = normalizedCenter(element, displayBounds: displayBounds)
                guard secure || point != nil else { continue }
                var item: [String: Any] = [
                    "source": "accessibility",
                    "role": role.hasPrefix("AX") ? String(role.dropFirst(2)) : role,
                    "text": text,
                    "pressable": pressable,
                    "sensitive": secure || ComputerControlSafety.isSensitiveElementText(text),
                    "secure": secure,
                ]
                if let point {
                    item["x"] = point.x
                    item["y"] = point.y
                }
                items.append(item)
            }
            let children = elementArray(element, kAXChildrenAttribute as CFString).filter {
                elementBelongsToProcess($0, target: target)
            }
            if depth < ComputerControlAccessibilityPolicy.maximumDepth {
                if children.count > ComputerControlAccessibilityPolicy.maximumChildrenPerElement {
                    truncated = true
                }
                queue.append(
                    contentsOf: children
                        .prefix(ComputerControlAccessibilityPolicy.maximumChildrenPerElement)
                        .map { ($0, depth + 1) }
                )
            } else if !children.isEmpty {
                truncated = true
            }
        }
        if
            !queue.isEmpty
                || visited.count >= ComputerControlAccessibilityPolicy.maximumElements
                || ContinuousClock.now >= deadline
        {
            truncated = true
        }
        return (windowTitles, items, truncated)
    }

    private static func visionPerception(
        image: CGImage
    ) -> (items: [[String: Any]], truncated: Bool) {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .fast
        request.usesLanguageCorrection = false
        request.automaticallyDetectsLanguage = true
        request.minimumTextHeight = 0.005
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        } catch {
            return ([], false)
        }
        let observations = request.results ?? []
        var items: [[String: Any]] = []
        for observation in observations.prefix(16) {
            guard
                let candidate = observation.topCandidates(1).first,
                candidate.confidence >= 0.30,
                let text = boundedText(candidate.string)
            else {
                continue
            }
            let box = observation.boundingBox
            items.append([
                "source": "vision",
                "role": "Text",
                "text": text,
                "x": boundedCoordinate(box.midX * 1_000),
                "y": boundedCoordinate((1 - box.midY) * 1_000),
                "pressable": false,
                "sensitive": ComputerControlSafety.isSensitiveElementText(text),
                "confidence": Double(candidate.confidence),
            ])
        }
        return (items, observations.count > 16)
    }

    private static func perceptionDescriptor(
        _ element: AXUIElement,
        includeValue: Bool
    ) -> String {
        var names = [
            kAXTitleAttribute,
            kAXDescriptionAttribute,
            kAXHelpAttribute,
            kAXIdentifierAttribute,
            kAXRoleDescriptionAttribute,
        ]
        if includeValue { names.append(kAXValueAttribute) }
        var values: [String] = []
        for name in names {
            guard let value = boundedText(attribute(element, name as CFString)) else { continue }
            if !values.contains(value) { values.append(value) }
        }
        return values.joined(separator: " ")
    }

    private static func elementArray(
        _ element: AXUIElement,
        _ name: CFString
    ) -> [AXUIElement] {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let array = value as? [AXUIElement]
        else {
            return []
        }
        return array
    }

    private static func normalizedCenter(
        _ element: AXUIElement,
        displayBounds: CGRect
    ) -> (x: Int, y: Int)? {
        guard
            displayBounds.width > 0,
            displayBounds.height > 0,
            let position = pointAttribute(element, kAXPositionAttribute as CFString),
            let size = sizeAttribute(element, kAXSizeAttribute as CFString),
            size.width > 0,
            size.height > 0
        else {
            return nil
        }
        let center = CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
        guard displayBounds.insetBy(dx: -1, dy: -1).contains(center) else { return nil }
        return (
            boundedCoordinate((center.x - displayBounds.minX) * 1_000 / displayBounds.width),
            boundedCoordinate((center.y - displayBounds.minY) * 1_000 / displayBounds.height)
        )
    }

    private static func elementIntersectsDisplay(
        _ element: AXUIElement,
        displayBounds: CGRect
    ) -> Bool {
        guard
            let position = pointAttribute(element, kAXPositionAttribute as CFString),
            let size = sizeAttribute(element, kAXSizeAttribute as CFString)
        else {
            return false
        }
        return ComputerDisplayPlan.select(
            windowPosition: position,
            windowSize: size,
            candidates: [ComputerDisplayCandidate(identifier: 1, bounds: displayBounds)]
        ) != nil
    }

    private static func pointAttribute(
        _ element: AXUIElement,
        _ name: CFString
    ) -> CGPoint? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let value,
            CFGetTypeID(value) == AXValueGetTypeID()
        else {
            return nil
        }
        var point = CGPoint.zero
        guard AXValueGetValue(unsafeDowncast(value, to: AXValue.self), .cgPoint, &point) else {
            return nil
        }
        return point
    }

    private static func sizeAttribute(
        _ element: AXUIElement,
        _ name: CFString
    ) -> CGSize? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let value,
            CFGetTypeID(value) == AXValueGetTypeID()
        else {
            return nil
        }
        var size = CGSize.zero
        guard AXValueGetValue(unsafeDowncast(value, to: AXValue.self), .cgSize, &size) else {
            return nil
        }
        return size
    }

    private static func boundedCoordinate(_ value: CGFloat) -> Int {
        min(1_000, max(0, Int(value.rounded())))
    }

    private static func boundedText(_ value: String?) -> String? {
        guard let value else { return nil }
        let normalized = value
            .components(separatedBy: .whitespacesAndNewlines)
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        guard !normalized.isEmpty else { return nil }
        return String(normalized.prefix(256))
    }

    private static func act(
        _ command: ComputerControlCommand,
        expectedBundleIdentifier: String
    ) throws -> CGDirectDisplayID {
        guard AXIsProcessTrusted() else {
            throw HelperFailure.accessibilityPermissionRequired
        }
        guard setAccessibilityTimeout(
            ComputerControlAccessibilityPolicy.actionMessagingTimeoutSeconds
        ) else {
            throw HelperFailure.unsafeTarget
        }
        let target = try processTarget(expectedBundleIdentifier)
        let visualState = try visualState(target: target)
        guard command.expectedVisualContext == visualState.token else {
            throw HelperFailure.unsafeTarget
        }
        switch command.action {
        case "click":
            try click(
                command,
                target: target,
                displayBounds: visualState.display.bounds
            )
        case "type":
            try typeText(command.text, target: target)
        case "key":
            try pressKey(
                command.key,
                modifiers: command.modifiers ?? [],
                target: target
            )
        case "scroll":
            try scroll(
                command.direction,
                amount: command.amount,
                target: target,
                displayBounds: visualState.display.bounds
            )
        default:
            throw HelperFailure.invalidCommand
        }
        try requireProcessTarget(target)
        return visualState.display.identifier
    }

    private static func click(
        _ command: ComputerControlCommand,
        target: ComputerProcessTarget,
        displayBounds: CGRect
    ) throws {
        guard
            let normalizedX = command.x,
            let normalizedY = command.y,
            let clickCount = command.clickCount,
            let buttonName = command.button,
            let expectedTarget = command.target
        else {
            throw HelperFailure.invalidCommand
        }
        let point = CGPoint(
            x: displayBounds.minX + displayBounds.width * CGFloat(normalizedX) / 1_000,
            y: displayBounds.minY + displayBounds.height * CGFloat(normalizedY) / 1_000
        )
        let element = try element(at: point, target: target)
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
                target: target
            )
        else {
            throw HelperFailure.unsafeTarget
        }
        let pressableDescriptor = elementDescriptor(pressable)
        if ComputerControlSafety.isSensitiveElementText(pressableDescriptor) {
            throw HelperFailure.sensitiveTargetBlocked
        }
        guard
            boundedText(perceptionDescriptor(pressable, includeValue: true)) == expectedTarget
        else {
            throw HelperFailure.unsafeTarget
        }
        try requireProcessTarget(target)
        try requireElementOwner(pressable, target: target)
        guard AXUIElementPerformAction(pressable, kAXPressAction as CFString) == .success else {
            throw HelperFailure.unsafeTarget
        }
        usleep(120_000)
    }

    private static func typeText(
        _ text: String?,
        target: ComputerProcessTarget
    ) throws {
        guard let text else { throw HelperFailure.invalidCommand }
        let element = try safeFocusedTextElement(target: target)
        let chunks = ComputerTextInputPlan.chunks(text)
        guard !chunks.isEmpty else { throw HelperFailure.invalidCommand }
        let source = CGEventSource(stateID: .privateState)
        for chunk in chunks {
            let currentElement = try safeFocusedTextElement(target: target)
            guard CFEqual(element, currentElement) else {
                throw HelperFailure.unsafeTarget
            }
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
            try requireProcessTarget(target)
            down.postToPid(target.processIdentifier)
            up.postToPid(target.processIdentifier)
            usleep(20_000)
        }
    }

    private static func safeFocusedTextElement(
        target: ComputerProcessTarget
    ) throws -> AXUIElement {
        try requireProcessTarget(target)
        let element = try focusedElement(target: target)
        let role = attribute(element, kAXRoleAttribute as CFString) ?? ""
        let subrole = attribute(element, kAXSubroleAttribute as CFString) ?? ""
        let descriptor = elementDescriptor(element)
        guard ComputerControlSafety.isAllowedTextRole(role) else {
            throw HelperFailure.unsafeTarget
        }
        guard
            subrole != "AXSecureTextField",
            !ComputerControlSafety.isSensitiveElementText(descriptor)
        else {
            throw HelperFailure.sensitiveTargetBlocked
        }
        return element
    }

    private static func pressKey(
        _ key: String?,
        modifiers: [String],
        target: ComputerProcessTarget
    ) throws {
        guard
            ComputerControlSafety.isSafeKeyPress(key: key, modifiers: modifiers),
            let key,
            let keyCode = keyCode(key)
        else {
            throw HelperFailure.invalidCommand
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
        let source = CGEventSource(stateID: .privateState)
        guard
            let down = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true),
            let up = CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false)
        else {
            throw HelperFailure.unsafeTarget
        }
        down.flags = flags
        up.flags = flags
        try requireProcessTarget(target)
        down.postToPid(target.processIdentifier)
        usleep(40_000)
        up.postToPid(target.processIdentifier)
    }

    private static func scroll(
        _ direction: String?,
        amount: Int?,
        target: ComputerProcessTarget,
        displayBounds: CGRect
    ) throws {
        guard let plan = ComputerScrollPlan(direction: direction, amount: amount) else {
            throw HelperFailure.invalidCommand
        }
        let point = try scrollTarget(target: target, displayBounds: displayBounds)
        guard let event = CGEvent(
            scrollWheelEvent2Source: CGEventSource(stateID: .privateState),
            units: .line,
            wheelCount: 2,
            wheel1: plan.verticalDelta,
            wheel2: plan.horizontalDelta,
            wheel3: 0
        ) else {
            throw HelperFailure.unsafeTarget
        }
        event.location = point
        try requireProcessTarget(target)
        _ = try element(at: point, target: target)
        event.postToPid(target.processIdentifier)
    }

    private static func scrollTarget(
        target: ComputerProcessTarget,
        displayBounds: CGRect
    ) throws -> CGPoint {
        let window = try focusedWindow(target: target)
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString),
            let point = ComputerScrollPlan.target(
                windowPosition: position,
                windowSize: size,
                displayBounds: displayBounds
            )
        else {
            throw HelperFailure.unsafeTarget
        }
        _ = try element(at: point, target: target)
        return point
    }

    private static func visualState(
        target: ComputerProcessTarget
    ) throws -> ComputerVisualState {
        let window = try focusedWindow(target: target)
        let display = try displayTarget(
            for: window,
            candidates: activeDisplayCandidates(),
            target: target
        )
        let token = try visualToken(for: window, display: display, target: target)
        return ComputerVisualState(display: display, token: token)
    }

    private static func visualToken(
        for window: AXUIElement,
        display: ComputerDisplayCandidate,
        target: ComputerProcessTarget
    ) throws -> String {
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString),
            let token = ComputerVisualContext.make(
                bundleIdentifier: target.bundleIdentifier,
                processIdentifier: target.processIdentifier,
                launchDate: target.launchDate,
                display: display,
                windowPosition: position,
                windowSize: size
            )
        else {
            throw HelperFailure.unsafeTarget
        }
        return token
    }

    private static func focusedWindow(
        target: ComputerProcessTarget
    ) throws -> AXUIElement {
        try requireProcessTarget(target)
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                AXUIElementCreateApplication(target.processIdentifier),
                kAXFocusedWindowAttribute as CFString,
                &value
            ) == .success,
            let value,
            CFGetTypeID(value) == AXUIElementGetTypeID()
        else {
            throw HelperFailure.unsafeTarget
        }
        let window = unsafeDowncast(value, to: AXUIElement.self)
        try requireElementOwner(window, target: target)
        return window
    }

    private static func displayTarget(
        for window: AXUIElement,
        candidates: [ComputerDisplayCandidate],
        target: ComputerProcessTarget
    ) throws -> ComputerDisplayCandidate {
        try requireElementOwner(window, target: target)
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString),
            let display = ComputerDisplayPlan.select(
                windowPosition: position,
                windowSize: size,
                candidates: candidates
            )
        else {
            throw HelperFailure.unsafeTarget
        }
        return display
    }

    private static func activeDisplayCandidates() throws -> [ComputerDisplayCandidate] {
        var count: UInt32 = 0
        guard
            CGGetActiveDisplayList(0, nil, &count) == .success,
            count > 0,
            count <= UInt32(ComputerDisplayPlan.maximumActiveDisplays)
        else {
            throw HelperFailure.unsafeTarget
        }
        var identifiers = [CGDirectDisplayID](repeating: 0, count: Int(count))
        var actualCount: UInt32 = 0
        let status = identifiers.withUnsafeMutableBufferPointer { buffer in
            CGGetActiveDisplayList(count, buffer.baseAddress, &actualCount)
        }
        guard status == .success, actualCount == count else {
            throw HelperFailure.unsafeTarget
        }
        return identifiers.map {
            ComputerDisplayCandidate(identifier: $0, bounds: CGDisplayBounds($0))
        }
    }

    private static func element(
        at point: CGPoint,
        target: ComputerProcessTarget
    ) throws -> AXUIElement {
        var elementAtPoint: AXUIElement?
        let status = AXUIElementCopyElementAtPosition(
            AXUIElementCreateSystemWide(),
            Float(point.x),
            Float(point.y),
            &elementAtPoint
        )
        guard status == .success, let elementAtPoint else { throw HelperFailure.unsafeTarget }
        try requireElementOwner(elementAtPoint, target: target)
        return elementAtPoint
    }

    private static func focusedElement(
        target: ComputerProcessTarget
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
        try requireElementOwner(element, target: target)
        return element
    }

    private static func requireElementOwner(
        _ element: AXUIElement,
        target: ComputerProcessTarget
    ) throws {
        var processID: pid_t = 0
        guard
            AXUIElementGetPid(element, &processID) == .success,
            processID == target.processIdentifier,
            let application = NSRunningApplication(processIdentifier: processID),
            application.bundleIdentifier == target.bundleIdentifier,
            application.launchDate == target.launchDate
        else {
            throw HelperFailure.unsafeTarget
        }
    }

    private static func elementBelongsToProcess(
        _ element: AXUIElement,
        target: ComputerProcessTarget
    ) -> Bool {
        var processID: pid_t = 0
        return AXUIElementGetPid(element, &processID) == .success
            && processID == target.processIdentifier
    }

    private static func pressableElement(
        from element: AXUIElement,
        target: ComputerProcessTarget
    ) -> AXUIElement? {
        var candidate = element
        for _ in 0 ..< 8 {
            do {
                try requireElementOwner(
                    candidate,
                    target: target
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

    private static func setAccessibilityTimeout(_ seconds: Float) -> Bool {
        AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), seconds) == .success
    }

    private static func processTarget(_ expectedBundleIdentifier: String) throws -> ComputerProcessTarget {
        guard
            !ComputerControlSafety.isRestrictedBundleIdentifier(expectedBundleIdentifier),
            let application = NSWorkspace.shared.frontmostApplication,
            application.bundleIdentifier == expectedBundleIdentifier,
            let launchDate = application.launchDate,
            application.processIdentifier > 0
        else {
            throw HelperFailure.frontmostApplicationMismatch
        }
        return ComputerProcessTarget(
            bundleIdentifier: expectedBundleIdentifier,
            launchDate: launchDate,
            processIdentifier: application.processIdentifier
        )
    }

    private static func requireProcessTarget(_ target: ComputerProcessTarget) throws {
        guard
            let application = NSWorkspace.shared.frontmostApplication,
            application.bundleIdentifier == target.bundleIdentifier,
            application.processIdentifier == target.processIdentifier,
            application.launchDate == target.launchDate
        else {
            throw HelperFailure.frontmostApplicationMismatch
        }
    }

    private static func frontmostBundleIdentifier() -> String? {
        NSWorkspace.shared.frontmostApplication?.bundleIdentifier
    }

    private static func keyCode(_ value: String) -> CGKeyCode? {
        let named: [String: Int] = [
            "escape": kVK_Escape,
            "tab": kVK_Tab,
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
            "a": kVK_ANSI_A,
            "f": kVK_ANSI_F,
            "l": kVK_ANSI_L,
            "r": kVK_ANSI_R,
            "t": kVK_ANSI_T,
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
