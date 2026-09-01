@preconcurrency import ApplicationServices
import AppKit
import CoreGraphics
import Foundation

public enum SilentEventOperation: String, Codable, Sendable {
    case press
    case scroll
    case type
}

public struct SilentEventRequest: Codable, Sendable, Equatable {
    public let operation: SilentEventOperation
    public let processIdentifier: Int32
    public let bundleIdentifier: String
    public let accessibilityIdentifier: String?
    public let accessibilityLabel: String?
    public let text: String?
    public let verticalDelta: Int32?
    public let horizontalDelta: Int32?
    public let fallbackPoint: CGPoint?

    public init(
        operation: SilentEventOperation,
        processIdentifier: Int32,
        bundleIdentifier: String,
        accessibilityIdentifier: String? = nil,
        accessibilityLabel: String? = nil,
        text: String? = nil,
        verticalDelta: Int32? = nil,
        horizontalDelta: Int32? = nil,
        fallbackPoint: CGPoint? = nil
    ) {
        self.operation = operation
        self.processIdentifier = processIdentifier
        self.bundleIdentifier = bundleIdentifier
        self.accessibilityIdentifier = accessibilityIdentifier
        self.accessibilityLabel = accessibilityLabel
        self.text = text
        self.verticalDelta = verticalDelta
        self.horizontalDelta = horizontalDelta
        self.fallbackPoint = fallbackPoint
    }
}

public enum SilentEventsError: Error, Equatable, Sendable {
    case accessibilityPermissionDenied
    case applicationIdentityMismatch
    case invalidRequest
    case targetNotFound
    case unsupportedOperation
}

public enum SilentEvents {
    public static let maximumElements = 256
    public static let maximumDepth = 8

    public static func dispatch(_ request: SilentEventRequest) throws -> QuietEventVerification {
        guard
            request.processIdentifier > 1,
            request.bundleIdentifier.range(
                of: #"^[A-Za-z0-9][A-Za-z0-9.-]{2,254}$"#,
                options: .regularExpression
            ) != nil,
            request.accessibilityIdentifier != nil || request.accessibilityLabel != nil,
            !ComputerControlSafety.isRestrictedBundleIdentifier(request.bundleIdentifier)
        else {
            throw SilentEventsError.invalidRequest
        }
        guard AXIsProcessTrusted() else {
            throw SilentEventsError.accessibilityPermissionDenied
        }
        guard
            let running = NSRunningApplication(
                processIdentifier: request.processIdentifier
            ),
            running.bundleIdentifier?.caseInsensitiveCompare(request.bundleIdentifier)
                == .orderedSame
        else {
            throw SilentEventsError.applicationIdentityMismatch
        }

        let application = AXUIElementCreateApplication(request.processIdentifier)
        guard let target = findTarget(in: application, request: request) else {
            throw SilentEventsError.targetNotFound
        }
        let fallback = request.fallbackPoint ?? center(of: target) ?? .zero
        switch request.operation {
        case .press:
            return try QuietEventDispatcher.press(
                element: target,
                application: application,
                processIdentifier: request.processIdentifier,
                fallbackPoint: fallback
            )
        case .type:
            guard let text = request.text else {
                throw SilentEventsError.invalidRequest
            }
            return try QuietEventDispatcher.insertText(
                text,
                into: target,
                application: application,
                processIdentifier: request.processIdentifier
            )
        case .scroll:
            guard
                let vertical = request.verticalDelta,
                let horizontal = request.horizontalDelta,
                vertical != 0 || horizontal != 0
            else {
                throw SilentEventsError.invalidRequest
            }
            return try QuietEventDispatcher.scroll(
                verticalDelta: vertical,
                horizontalDelta: horizontal,
                element: target,
                application: application,
                processIdentifier: request.processIdentifier,
                fallbackPoint: fallback
            )
        }
    }

    private static func findTarget(
        in application: AXUIElement,
        request: SilentEventRequest
    ) -> AXUIElement? {
        var queue: [(AXUIElement, Int)] = [(application, 0)]
        var inspected = 0
        while !queue.isEmpty, inspected < maximumElements {
            let (element, depth) = queue.removeFirst()
            inspected += 1
            if matches(element, request: request) {
                return element
            }
            guard depth < maximumDepth else { continue }
            var value: CFTypeRef?
            guard
                AXUIElementCopyAttributeValue(
                    element,
                    kAXChildrenAttribute as CFString,
                    &value
                ) == .success,
                let children = value as? [AXUIElement]
            else {
                continue
            }
            queue.append(contentsOf: children.prefix(24).map { ($0, depth + 1) })
        }
        return nil
    }

    private static func matches(
        _ element: AXUIElement,
        request: SilentEventRequest
    ) -> Bool {
        if let identifier = request.accessibilityIdentifier {
            return stringAttribute(element, kAXIdentifierAttribute as CFString) == identifier
        }
        guard let label = request.accessibilityLabel else { return false }
        let normalized = normalize(label)
        return [
            kAXTitleAttribute as CFString,
            kAXDescriptionAttribute as CFString,
            kAXHelpAttribute as CFString,
            kAXValueAttribute as CFString,
        ].contains { attribute in
            guard let value = stringAttribute(element, attribute) else { return false }
            return normalize(value) == normalized
        }
    }

    private static func stringAttribute(
        _ element: AXUIElement,
        _ attribute: CFString
    ) -> String? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else {
            return nil
        }
        return value as? String
    }

    private static func center(of element: AXUIElement) -> CGPoint? {
        var positionValue: CFTypeRef?
        var sizeValue: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                element,
                kAXPositionAttribute as CFString,
                &positionValue
            ) == .success,
            AXUIElementCopyAttributeValue(
                element,
                kAXSizeAttribute as CFString,
                &sizeValue
            ) == .success,
            let positionValue,
            let sizeValue,
            CFGetTypeID(positionValue) == AXValueGetTypeID(),
            CFGetTypeID(sizeValue) == AXValueGetTypeID()
        else {
            return nil
        }
        var position = CGPoint.zero
        var size = CGSize.zero
        guard
            AXValueGetValue(positionValue as! AXValue, .cgPoint, &position),
            AXValueGetValue(sizeValue as! AXValue, .cgSize, &size),
            position.x.isFinite,
            position.y.isFinite,
            size.width > 0,
            size.height > 0
        else {
            return nil
        }
        return CGPoint(x: position.x + size.width / 2, y: position.y + size.height / 2)
    }

    private static func normalize(_ value: String) -> String {
        value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
    }
}
