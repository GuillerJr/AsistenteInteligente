@preconcurrency import ApplicationServices
import AppKit
import CoreGraphics
import CryptoKit
import Foundation

public enum QuietEventPathway: String, Codable, Sendable {
    case accessibility
    case processEvent
}

public struct QuietEventVerification: Codable, Equatable, Sendable {
    public let pathway: QuietEventPathway
    public let beforeSHA256: String
    public let afterSHA256: String
    public let stateChanged: Bool
    public let verified: Bool

    public init(
        pathway: QuietEventPathway,
        beforeSHA256: String,
        afterSHA256: String,
        stateChanged: Bool,
        verified: Bool
    ) {
        self.pathway = pathway
        self.beforeSHA256 = beforeSHA256
        self.afterSHA256 = afterSHA256
        self.stateChanged = stateChanged
        self.verified = verified
    }
}

public enum QuietEventDispatcherError: Error, Equatable {
    case accessibilityUnavailable
    case eventCreationFailed
    case invalidProcessIdentifier
    case invalidText
    case stateVerificationFailed
    case unsupportedAction
    case visionFallbackDenied
    case userTakeover
    case effectUncertain
}

public typealias LocalizedVisionAnalyzer = @Sendable (
    LocalizedVisionCapture
) async throws -> LocalizedVisionDecision

public enum QuietEventPhase: Sendable {
    case beforeEffect
    case beforeRelease
    case afterEffect
}

public typealias QuietEventRevalidation = (QuietEventPhase) throws -> Void

/// Shared dispatch boundaries are also used by synthetic tests: no UI events are needed.
public enum QuietEventBoundary {
    public static func mutation(
        revalidate: QuietEventRevalidation,
        effect: () throws -> Void
    ) throws {
        try revalidate(.beforeEffect)
        do {
            try effect()
            try revalidate(.afterEffect)
        } catch {
            throw terminalFailure(error)
        }
    }

    public static func pair(
        revalidate: QuietEventRevalidation,
        down: () -> Void,
        up: () -> Void
    ) throws {
        try revalidate(.beforeEffect)
        down()
        do {
            try revalidate(.beforeRelease)
        } catch {
            // A delivered down must be released even when the owner takes over.
            // This cleanup is never a second click or a replay of typed text.
            up()
            throw terminalFailure(error)
        }
        up()
        do {
            try revalidate(.afterEffect)
        } catch {
            throw terminalFailure(error)
        }
    }

    public static func textFragments(
        _ text: String,
        revalidate: QuietEventRevalidation,
        post: ([UInt16]) throws -> Void
    ) throws {
        let chunks = ComputerTextInputPlan.chunks(text)
        guard !chunks.isEmpty else { throw QuietEventDispatcherError.invalidText }
        var delivered = false
        for chunk in chunks {
            do {
                try revalidate(.beforeEffect)
                try post(chunk)
                delivered = true
                try revalidate(.afterEffect)
            } catch {
                if delivered { throw terminalFailure(error) }
                throw error
            }
        }
    }

    public static func localizedClick(
        expectedScreenPoint: CGPoint,
        revalidate: QuietEventRevalidation,
        capture: () async throws -> LocalizedVisionCapture,
        analyzer: LocalizedVisionAnalyzer,
        validateCorrectedPoint: (CGPoint) throws -> Void,
        post: (CGPoint) throws -> Void
    ) async throws {
        try revalidate(.beforeEffect)
        let observation = try await capture()
        try Task.checkCancellation()
        try revalidate(.beforeEffect)
        let decision = try await analyzer(observation)
        try Task.checkCancellation()
        try revalidate(.beforeEffect)
        let pointInCrop = try decision.validatedPoint(around: observation.expectedPointInCrop)
        guard pointInCrop.x >= 0, pointInCrop.y >= 0,
              pointInCrop.x < CGFloat(OnDemandVisionCapture.edgeLength),
              pointInCrop.y < CGFloat(OnDemandVisionCapture.edgeLength)
        else { throw QuietEventDispatcherError.visionFallbackDenied }
        let corrected = CGPoint(x: expectedScreenPoint.x + decision.offsetX,
                                y: expectedScreenPoint.y + decision.offsetY)
        try validateCorrectedPoint(corrected)
        try revalidate(.beforeEffect)
        try post(corrected)
    }

    public static func terminalFailure(_ error: any Error) -> any Error {
        if error as? QuietEventDispatcherError == .userTakeover { return error }
        return QuietEventDispatcherError.effectUncertain
    }
}

/// Optional AX metadata stays exact or explicitly reports truncation; URLs are never rewritten.
public enum ComputerAXObservationMetadata {
    public static func value(_ raw: String) -> (text: String, truncated: Bool) {
        (String(raw.prefix(256)), raw.count > 256)
    }

    public static func currentURL(_ raw: String?, secureContent: Bool) -> String? {
        guard !secureContent, let raw, !raw.isEmpty, raw.utf8.count <= 512,
              !raw.unicodeScalars.contains(where: { $0.value < 33 || $0.value == 127 }),
              let parsed = URLComponents(string: raw),
              ["http", "https"].contains(parsed.scheme?.lowercased() ?? ""),
              parsed.host != nil, parsed.user == nil, parsed.password == nil,
              parsed.fragment == nil
        else { return nil }
        let secretNames = ["token", "key", "secret", "password", "auth", "authorization",
                           "code", "signature", "session", "credential", "passcode"]
        for item in parsed.queryItems ?? [] {
            let name = item.name.lowercased()
            if secretNames.contains(where: { name.contains($0) }) { return nil }
        }
        let decoded = raw.removingPercentEncoding ?? raw
        if decoded.range(of: #"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"#,
                         options: [.caseInsensitive, .regularExpression]) != nil
            || decoded.range(of: #"(?:nvapi-|Bearer\s|eyJ[A-Za-z0-9_-]+\.)"#,
                             options: [.caseInsensitive, .regularExpression]) != nil
        { return nil }
        return raw
    }
}

public enum QuietEventDispatcher {
    public static let verificationDelayMicroseconds: useconds_t = 50_000

    public static func verifyAccessibilityMutation(
        element: AXUIElement,
        application: AXUIElement,
        revalidate: QuietEventRevalidation = { _ in },
        mutation: () throws -> Void
    ) throws -> QuietEventVerification {
        let before = try stateDigest(element: element, application: application)
        try QuietEventBoundary.mutation(revalidate: revalidate, effect: mutation)
        return try verify(before: before, element: element,
                          application: application, pathway: .accessibility)
    }

    public static func press(
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        fallbackPoint: CGPoint,
        revalidate: QuietEventRevalidation? = nil
    ) throws -> QuietEventVerification {
        let validate = try boundaryValidation(
            element: element, application: application, processIdentifier: processIdentifier,
            supplied: revalidate
        )
        let before = try stateDigest(element: element, application: application)
        let pathway: QuietEventPathway
        if supports(element: element, action: kAXPressAction as String) {
            try QuietEventBoundary.mutation(revalidate: validate) {
                guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
                    throw QuietEventDispatcherError.accessibilityUnavailable
                }
            }
            pathway = .accessibility
        } else {
            try postClick(processIdentifier: processIdentifier, point: fallbackPoint,
                          revalidate: validate)
            pathway = .processEvent
        }
        return try verify(before: before, element: element,
                          application: application, pathway: pathway)
    }

    public static func pressWithLocalizedVisionFallback(
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        windowID: CGWindowID,
        expectedScreenPoint: CGPoint,
        revalidate: @escaping QuietEventRevalidation,
        validateCorrectedPoint: (CGPoint) throws -> Void,
        analyzer: LocalizedVisionAnalyzer
    ) async throws -> QuietEventVerification {
        let validate = try boundaryValidation(
            element: element, application: application, processIdentifier: processIdentifier,
            supplied: revalidate
        )
        let before = try stateDigest(element: element, application: application)
        if supports(element: element, action: kAXPressAction as String) {
            // Any attempted AX effect is terminal on failure. An ambiguous result
            // must never produce a duplicate click through the visual path.
            try QuietEventBoundary.mutation(revalidate: validate) {
                guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
                    throw QuietEventDispatcherError.accessibilityUnavailable
                }
            }
            return try verify(before: before, element: element,
                              application: application, pathway: .accessibility)
        }
        try await QuietEventBoundary.localizedClick(
            expectedScreenPoint: expectedScreenPoint,
            revalidate: validate,
            capture: {
                try await OnDemandVisionCapture.capture(
                    windowID: windowID, processIdentifier: processIdentifier,
                    expectedScreenPoint: expectedScreenPoint
                )
            },
            analyzer: analyzer,
            validateCorrectedPoint: validateCorrectedPoint,
            post: { point in
                try postClick(processIdentifier: processIdentifier, point: point) { phase in
                    try validate(phase)
                    if phase == .beforeEffect { try validateCorrectedPoint(point) }
                }
            }
        )
        return try verify(before: before, element: element,
                          application: application, pathway: .processEvent)
    }

    public static func insertText(
        _ text: String,
        into element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        revalidate: QuietEventRevalidation? = nil
    ) throws -> QuietEventVerification {
        guard !text.isEmpty, text.utf16.count <= 2_048,
              !text.unicodeScalars.contains(where: { $0.value == 0 })
        else { throw QuietEventDispatcherError.invalidText }
        let validate = try boundaryValidation(
            element: element, application: application, processIdentifier: processIdentifier,
            requireFocus: true, supplied: revalidate
        )
        let before = try stateDigest(element: element, application: application)
        var selectedTextSettable = DarwinBoolean(false)
        let pathway: QuietEventPathway
        if AXUIElementIsAttributeSettable(element, kAXSelectedTextAttribute as CFString,
                                         &selectedTextSettable) == .success,
           selectedTextSettable.boolValue
        {
            try QuietEventBoundary.mutation(revalidate: validate) {
                guard AXUIElementSetAttributeValue(element, kAXSelectedTextAttribute as CFString,
                                                  text as CFString) == .success else {
                    throw QuietEventDispatcherError.accessibilityUnavailable
                }
            }
            pathway = .accessibility
        } else {
            try QuietEventBoundary.textFragments(text, revalidate: validate) { chunk in
                let source = CGEventSource(stateID: .privateState)
                guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
                      let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
                else { throw QuietEventDispatcherError.eventCreationFailed }
                chunk.withUnsafeBufferPointer { buffer in
                    down.keyboardSetUnicodeString(stringLength: buffer.count,
                                                  unicodeString: buffer.baseAddress)
                    up.keyboardSetUnicodeString(stringLength: buffer.count,
                                                unicodeString: buffer.baseAddress)
                }
                try QuietEventBoundary.pair(revalidate: validate,
                                            down: { down.postToPid(processIdentifier) },
                                            up: { up.postToPid(processIdentifier) })
            }
            pathway = .processEvent
        }
        return try verify(before: before, element: element,
                          application: application, pathway: pathway)
    }

    public static func scroll(
        verticalDelta: Int32,
        horizontalDelta: Int32,
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        fallbackPoint: CGPoint,
        revalidate: QuietEventRevalidation? = nil
    ) throws -> QuietEventVerification {
        guard verticalDelta != 0 || horizontalDelta != 0,
              abs(Int64(verticalDelta)) <= 64, abs(Int64(horizontalDelta)) <= 64,
              fallbackPoint.x.isFinite, fallbackPoint.y.isFinite
        else { throw QuietEventDispatcherError.unsupportedAction }
        let validate = try boundaryValidation(
            element: element, application: application, processIdentifier: processIdentifier,
            supplied: revalidate
        )
        let before = try stateDigest(element: element, application: application)
        let accessibilityAction: CFString? = horizontalDelta == 0
            ? (verticalDelta > 0 ? kAXIncrementAction as CFString : kAXDecrementAction as CFString)
            : nil
        let pathway: QuietEventPathway
        if let accessibilityAction, supports(element: element, action: accessibilityAction as String) {
            try QuietEventBoundary.mutation(revalidate: validate) {
                guard AXUIElementPerformAction(element, accessibilityAction) == .success else {
                    throw QuietEventDispatcherError.accessibilityUnavailable
                }
            }
            pathway = .accessibility
        } else {
            guard let event = CGEvent(scrollWheelEvent2Source: CGEventSource(stateID: .privateState),
                                      units: .line, wheelCount: 2, wheel1: verticalDelta,
                                      wheel2: horizontalDelta, wheel3: 0)
            else { throw QuietEventDispatcherError.eventCreationFailed }
            event.location = fallbackPoint
            try QuietEventBoundary.mutation(revalidate: validate) {
                event.postToPid(processIdentifier)
            }
            pathway = .processEvent
        }
        return try verify(before: before, element: element,
                          application: application, pathway: pathway)
    }

    public static func keyPress(
        virtualKey: CGKeyCode,
        flags: CGEventFlags,
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        revalidate: QuietEventRevalidation? = nil
    ) throws -> QuietEventVerification {
        let validate = try boundaryValidation(
            element: element, application: application, processIdentifier: processIdentifier,
            requireFocus: true, supplied: revalidate
        )
        let before = try stateDigest(element: element, application: application)
        let source = CGEventSource(stateID: .privateState)
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: virtualKey, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: virtualKey, keyDown: false)
        else { throw QuietEventDispatcherError.eventCreationFailed }
        down.flags = flags
        up.flags = flags
        try QuietEventBoundary.pair(revalidate: validate,
                                    down: { down.postToPid(processIdentifier) },
                                    up: { up.postToPid(processIdentifier) })
        return try verify(before: before, element: element,
                          application: application, pathway: .processEvent)
    }

    private static func postClick(
        processIdentifier: pid_t,
        point: CGPoint,
        revalidate: QuietEventRevalidation
    ) throws {
        let source = CGEventSource(stateID: .privateState)
        guard point.x.isFinite, point.y.isFinite,
              let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown,
                                 mouseCursorPosition: point, mouseButton: .left),
              let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp,
                               mouseCursorPosition: point, mouseButton: .left)
        else { throw QuietEventDispatcherError.eventCreationFailed }
        try QuietEventBoundary.pair(revalidate: revalidate,
                                    down: { down.postToPid(processIdentifier) },
                                    up: { up.postToPid(processIdentifier) })
    }

    private static func boundaryValidation(
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        requireFocus: Bool = false,
        supplied: QuietEventRevalidation?
    ) throws -> QuietEventRevalidation {
        try requireValid(processIdentifier: processIdentifier)
        guard let process = NSRunningApplication(processIdentifier: processIdentifier),
              let launchDate = process.launchDate, let bundle = process.bundleIdentifier
        else { throw QuietEventDispatcherError.invalidProcessIdentifier }
        let inputCounter = CGEventSource.counterForEventType(
            .hidSystemState, eventType: CGEventType(rawValue: UInt32.max)!
        )
        let window = elementAttribute(application, name: kAXFocusedWindowAttribute as CFString)
        return { phase in
            let currentInput = CGEventSource.counterForEventType(
                .hidSystemState, eventType: CGEventType(rawValue: UInt32.max)!
            )
            guard inputCounter == currentInput else { throw QuietEventDispatcherError.userTakeover }
            guard let current = NSRunningApplication(processIdentifier: processIdentifier),
                  !current.isTerminated, current.bundleIdentifier == bundle,
                  current.launchDate == launchDate
            else { throw QuietEventDispatcherError.invalidProcessIdentifier }
            var ownerPID: pid_t = 0
            guard AXUIElementGetPid(element, &ownerPID) == .success, ownerPID == processIdentifier
            else { throw QuietEventDispatcherError.invalidProcessIdentifier }
            if phase == .beforeEffect {
                if let window {
                    guard let currentWindow = elementAttribute(
                        application, name: kAXFocusedWindowAttribute as CFString
                    ), CFEqual(window, currentWindow)
                    else { throw QuietEventDispatcherError.stateVerificationFailed }
                }
                if requireFocus {
                    guard let focused = elementAttribute(
                        application, name: kAXFocusedUIElementAttribute as CFString
                    ), CFEqual(element, focused)
                    else { throw QuietEventDispatcherError.stateVerificationFailed }
                }
            }
            try supplied?(phase)
        }
    }

    private static func verify(
        before: String,
        element: AXUIElement,
        application: AXUIElement,
        pathway: QuietEventPathway
    ) throws -> QuietEventVerification {
        usleep(verificationDelayMicroseconds)
        let after = try stateDigest(element: element, application: application)
        guard before.count == 64, after.count == 64 else {
            throw QuietEventDispatcherError.stateVerificationFailed
        }
        return QuietEventVerification(
            pathway: pathway,
            beforeSHA256: before,
            afterSHA256: after,
            stateChanged: before != after,
            verified: true
        )
    }

    private static func requireValid(processIdentifier: pid_t) throws {
        guard processIdentifier > 0 else {
            throw QuietEventDispatcherError.invalidProcessIdentifier
        }
    }

    private static func supports(element: AXUIElement, action: String) -> Bool {
        var rawActions: CFArray?
        guard
            AXUIElementCopyActionNames(element, &rawActions) == .success,
            let actions = rawActions as? [String]
        else {
            return false
        }
        return actions.contains(action)
    }

    private static func stateDigest(
        element: AXUIElement,
        application: AXUIElement
    ) throws -> String {
        let focusedWindow = elementAttribute(
            application,
            name: kAXFocusedWindowAttribute as CFString
        )
        let payload: [String: Any] = [
            "element": state(of: element),
            "focused_window": focusedWindow.map(state(of:)) ?? [:],
        ]
        guard
            JSONSerialization.isValidJSONObject(payload),
            let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        else {
            throw QuietEventDispatcherError.stateVerificationFailed
        }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func state(of element: AXUIElement) -> [String: Any] {
        var result: [String: Any] = [:]
        let stringAttributes: [(String, CFString)] = [
            ("description", kAXDescriptionAttribute as CFString),
            ("help", kAXHelpAttribute as CFString),
            ("role", kAXRoleAttribute as CFString),
            ("subrole", kAXSubroleAttribute as CFString),
            ("title", kAXTitleAttribute as CFString),
            ("value", kAXValueAttribute as CFString),
        ]
        for (key, attributeName) in stringAttributes {
            if let value = stringAttribute(element, name: attributeName) {
                result[key] = String(value.prefix(2_048))
            }
        }
        let booleanAttributes: [(String, CFString)] = [
            ("enabled", kAXEnabledAttribute as CFString),
            ("expanded", kAXExpandedAttribute as CFString),
            ("focused", kAXFocusedAttribute as CFString),
            ("selected", kAXSelectedAttribute as CFString),
        ]
        for (key, attributeName) in booleanAttributes {
            if let value = boolAttribute(element, name: attributeName) {
                result[key] = value
            }
        }
        var rawActions: CFArray?
        if
            AXUIElementCopyActionNames(element, &rawActions) == .success,
            let actions = rawActions as? [String]
        {
            let sortedActions = actions.sorted()
            result["actions"] = Array(sortedActions[0 ..< min(sortedActions.count, 32)])
        }
        return result
    }

    private static func stringAttribute(_ element: AXUIElement, name: CFString) -> String? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let value
        else {
            return nil
        }
        if let string = value as? String {
            return string
        }
        if let number = value as? NSNumber {
            return number.stringValue
        }
        return nil
    }

    private static func boolAttribute(_ element: AXUIElement, name: CFString) -> Bool? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let number = value as? NSNumber
        else {
            return nil
        }
        return number.boolValue
    }

    private static func elementAttribute(
        _ element: AXUIElement,
        name: CFString
    ) -> AXUIElement? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let value,
            CFGetTypeID(value) == AXUIElementGetTypeID()
        else {
            return nil
        }
        return unsafeDowncast(value, to: AXUIElement.self)
    }
}
