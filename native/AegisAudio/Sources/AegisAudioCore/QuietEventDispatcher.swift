@preconcurrency import ApplicationServices
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
}

public enum QuietEventDispatcher {
    public static let verificationDelayMicroseconds: useconds_t = 50_000

    public static func press(
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        fallbackPoint: CGPoint
    ) throws -> QuietEventVerification {
        try requireValid(processIdentifier: processIdentifier)
        let before = try stateDigest(element: element, application: application)
        let pathway: QuietEventPathway
        if supports(element: element, action: kAXPressAction as String) {
            guard AXUIElementPerformAction(element, kAXPressAction as CFString) == .success else {
                throw QuietEventDispatcherError.accessibilityUnavailable
            }
            pathway = .accessibility
        } else {
            let source = CGEventSource(stateID: .privateState)
            guard
                fallbackPoint.x.isFinite,
                fallbackPoint.y.isFinite,
                let down = CGEvent(
                    mouseEventSource: source,
                    mouseType: .leftMouseDown,
                    mouseCursorPosition: fallbackPoint,
                    mouseButton: .left
                ),
                let up = CGEvent(
                    mouseEventSource: source,
                    mouseType: .leftMouseUp,
                    mouseCursorPosition: fallbackPoint,
                    mouseButton: .left
                )
            else {
                throw QuietEventDispatcherError.eventCreationFailed
            }
            down.postToPid(processIdentifier)
            up.postToPid(processIdentifier)
            pathway = .processEvent
        }
        return try verify(
            before: before,
            element: element,
            application: application,
            pathway: pathway
        )
    }

    public static func insertText(
        _ text: String,
        into element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t
    ) throws -> QuietEventVerification {
        try requireValid(processIdentifier: processIdentifier)
        guard
            !text.isEmpty,
            text.utf16.count <= 2_048,
            !text.unicodeScalars.contains(where: { $0.value == 0 })
        else {
            throw QuietEventDispatcherError.invalidText
        }
        let before = try stateDigest(element: element, application: application)
        var selectedTextSettable = DarwinBoolean(false)
        let pathway: QuietEventPathway
        if
            AXUIElementIsAttributeSettable(
                element,
                kAXSelectedTextAttribute as CFString,
                &selectedTextSettable
            ) == .success,
            selectedTextSettable.boolValue,
            AXUIElementSetAttributeValue(
                element,
                kAXSelectedTextAttribute as CFString,
                text as CFString
            ) == .success
        {
            pathway = .accessibility
        } else {
            let source = CGEventSource(stateID: .privateState)
            let units = Array(text.utf16)
            guard !units.isEmpty else { throw QuietEventDispatcherError.invalidText }
            for chunkStart in stride(from: 0, to: units.count, by: 64) {
                let chunk = Array(units[chunkStart ..< min(units.count, chunkStart + 64)])
                guard
                    let down = CGEvent(
                        keyboardEventSource: source,
                        virtualKey: 0,
                        keyDown: true
                    ),
                    let up = CGEvent(
                        keyboardEventSource: source,
                        virtualKey: 0,
                        keyDown: false
                    )
                else {
                    throw QuietEventDispatcherError.eventCreationFailed
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
                down.postToPid(processIdentifier)
                up.postToPid(processIdentifier)
            }
            pathway = .processEvent
        }
        return try verify(
            before: before,
            element: element,
            application: application,
            pathway: pathway
        )
    }

    public static func scroll(
        verticalDelta: Int32,
        horizontalDelta: Int32,
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t,
        fallbackPoint: CGPoint
    ) throws -> QuietEventVerification {
        try requireValid(processIdentifier: processIdentifier)
        guard
            verticalDelta != 0 || horizontalDelta != 0,
            abs(Int64(verticalDelta)) <= 64,
            abs(Int64(horizontalDelta)) <= 64,
            fallbackPoint.x.isFinite,
            fallbackPoint.y.isFinite
        else {
            throw QuietEventDispatcherError.unsupportedAction
        }
        let before = try stateDigest(element: element, application: application)
        let accessibilityAction: CFString? = if horizontalDelta == 0 {
            verticalDelta > 0 ? kAXIncrementAction as CFString : kAXDecrementAction as CFString
        } else {
            nil
        }
        let pathway: QuietEventPathway
        if
            let accessibilityAction,
            supports(element: element, action: accessibilityAction as String),
            AXUIElementPerformAction(element, accessibilityAction) == .success
        {
            pathway = .accessibility
        } else {
            guard let event = CGEvent(
                scrollWheelEvent2Source: CGEventSource(stateID: .privateState),
                units: .line,
                wheelCount: 2,
                wheel1: verticalDelta,
                wheel2: horizontalDelta,
                wheel3: 0
            ) else {
                throw QuietEventDispatcherError.eventCreationFailed
            }
            event.location = fallbackPoint
            event.postToPid(processIdentifier)
            pathway = .processEvent
        }
        return try verify(
            before: before,
            element: element,
            application: application,
            pathway: pathway
        )
    }

    public static func keyPress(
        virtualKey: CGKeyCode,
        flags: CGEventFlags,
        element: AXUIElement,
        application: AXUIElement,
        processIdentifier: pid_t
    ) throws -> QuietEventVerification {
        try requireValid(processIdentifier: processIdentifier)
        let before = try stateDigest(element: element, application: application)
        let source = CGEventSource(stateID: .privateState)
        guard
            let down = CGEvent(
                keyboardEventSource: source,
                virtualKey: virtualKey,
                keyDown: true
            ),
            let up = CGEvent(
                keyboardEventSource: source,
                virtualKey: virtualKey,
                keyDown: false
            )
        else {
            throw QuietEventDispatcherError.eventCreationFailed
        }
        down.flags = flags
        up.flags = flags
        down.postToPid(processIdentifier)
        up.postToPid(processIdentifier)
        return try verify(
            before: before,
            element: element,
            application: application,
            pathway: .processEvent
        )
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
