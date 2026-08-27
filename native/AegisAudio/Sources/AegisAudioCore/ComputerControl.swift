import CoreGraphics
import Foundation

public enum ComputerControlCommandError: Error, Equatable, Sendable {
    case invalidEnvelope
    case invalidCommand
    case invalidAction
    case restrictedApplication
}

public enum ComputerControlCapabilityState: Equatable, Sendable {
    case ready
    case screenCaptureMissing
    case accessibilityMissing
    case permissionsMissing
    case helperUnavailable

    public var nextPermissionRequest: ComputerControlPermissionRequest? {
        switch self {
        case .ready, .helperUnavailable:
            nil
        case .screenCaptureMissing, .permissionsMissing:
            .screenCapture
        case .accessibilityMissing:
            .accessibility
        }
    }
}

public enum ComputerControlPermissionRequest: Equatable, Sendable {
    case screenCapture
    case accessibility
}

public enum ComputerControlCapturePolicy {
    public static let includeMenuBar = false
    public static let showCursor = false
    public static let captureAudio = false
}

public enum ComputerVisualFingerprint {
    private static let width = 17
    private static let height = 16

    public static func make(from image: CGImage) -> String? {
        var pixels = [UInt8](repeating: 0, count: width * height)
        let rendered = pixels.withUnsafeMutableBytes { buffer in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width,
                space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: CGImageAlphaInfo.none.rawValue
            ) else {
                return false
            }
            context.interpolationQuality = .low
            context.setBlendMode(.copy)
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard rendered else { return nil }

        var fingerprint = [UInt8](repeating: 0, count: 32)
        for row in 0 ..< height {
            for column in 0 ..< width - 1 {
                let bit = row * (width - 1) + column
                if pixels[row * width + column] > pixels[row * width + column + 1] {
                    fingerprint[bit / 8] |= 1 << (7 - bit % 8)
                }
            }
        }
        return fingerprint.map { String(format: "%02x", $0) }.joined()
    }
}

public enum ComputerControlSafety {
    private static let restrictedBundleIdentifiers: Set<String> = [
        "com.1password.1password",
        "com.agilebits.onepassword7",
        "com.apple.automator",
        "com.apple.finder",
        "com.apple.keychainaccess",
        "com.apple.mail",
        "com.apple.passwords",
        "com.apple.scripteditor2",
        "com.apple.systempreferences",
        "com.apple.terminal",
        "com.googlecode.iterm2",
        "dev.warp.warp-stable",
    ]
    private static let sensitiveTerms = [
        "allow", "buy", "checkout", "close", "confirm", "delete", "download", "erase",
        "grant", "install", "log in", "login", "password", "pay", "publish", "purchase",
        "quit", "remove", "reset", "send", "sign in", "submit", "uninstall", "upload",
        "autorizar", "borrar", "cerrar", "comprar", "confirmar", "contraseña", "descargar",
        "desinstalar", "eliminar", "enviar", "iniciar sesión", "instalar", "pagar",
        "permitir", "publicar", "salir", "subir",
    ]
    private static let textRoles: Set<String> = [
        "AXComboBox", "AXSearchField", "AXTextArea", "AXTextField",
    ]

    public static func isRestrictedBundleIdentifier(_ value: String) -> Bool {
        restrictedBundleIdentifiers.contains(value.lowercased())
    }

    public static func isSensitiveElementText(_ value: String) -> Bool {
        let normalized = value
            .folding(options: [.caseInsensitive, .diacriticInsensitive], locale: .current)
            .lowercased()
        return sensitiveTerms.contains { normalized.contains($0) }
    }

    public static func isAllowedTextRole(_ value: String) -> Bool {
        textRoles.contains(value)
    }
}

public struct ComputerControlCommand: Decodable, Equatable, Sendable {
    public let protocolVersion: String
    public let command: String
    public let bundleIdentifier: String?
    public let expectedBundleIdentifier: String?
    public let action: String?
    public let x: Int?
    public let y: Int?
    public let button: String?
    public let clickCount: Int?
    public let target: String?
    public let text: String?
    public let key: String?
    public let modifiers: [String]?
    public let direction: String?
    public let amount: Int?

    enum CodingKeys: String, CodingKey, CaseIterable {
        case protocolVersion = "protocol_version"
        case command
        case bundleIdentifier = "bundle_identifier"
        case expectedBundleIdentifier = "expected_bundle_identifier"
        case action
        case x
        case y
        case button
        case clickCount = "click_count"
        case target
        case text
        case key
        case modifiers
        case direction
        case amount
    }

    public static func decode(_ data: Data) throws -> ComputerControlCommand {
        guard
            !data.isEmpty,
            data.count <= 8_192,
            let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            throw ComputerControlCommandError.invalidEnvelope
        }
        let command: ComputerControlCommand
        do {
            command = try JSONDecoder().decode(ComputerControlCommand.self, from: data)
        } catch {
            throw ComputerControlCommandError.invalidEnvelope
        }
        try command.validate(keys: Set(object.keys))
        return command
    }

    private func validate(keys: Set<String>) throws {
        guard protocolVersion == "1.0" else {
            throw ComputerControlCommandError.invalidEnvelope
        }
        let base: Set<String> = ["protocol_version", "command"]
        switch command {
        case "status":
            guard keys == base else { throw ComputerControlCommandError.invalidCommand }
        case "activate":
            guard
                keys == base.union(["bundle_identifier"]),
                let bundleIdentifier,
                Self.isValidBundleIdentifier(bundleIdentifier)
            else {
                throw ComputerControlCommandError.invalidCommand
            }
            if ComputerControlSafety.isRestrictedBundleIdentifier(bundleIdentifier) {
                throw ComputerControlCommandError.restrictedApplication
            }
        case "capture":
            guard
                keys == base.union(["expected_bundle_identifier"]),
                let expectedBundleIdentifier,
                Self.isValidBundleIdentifier(expectedBundleIdentifier)
            else {
                throw ComputerControlCommandError.invalidCommand
            }
            if ComputerControlSafety.isRestrictedBundleIdentifier(expectedBundleIdentifier) {
                throw ComputerControlCommandError.restrictedApplication
            }
        case "act":
            try validateAction(keys: keys, base: base)
        default:
            throw ComputerControlCommandError.invalidCommand
        }
    }

    private func validateAction(keys: Set<String>, base: Set<String>) throws {
        guard
            let action,
            let expectedBundleIdentifier,
            Self.isValidBundleIdentifier(expectedBundleIdentifier),
            !ComputerControlSafety.isRestrictedBundleIdentifier(expectedBundleIdentifier)
        else {
            throw ComputerControlCommandError.invalidAction
        }
        let actionBase = base.union(["expected_bundle_identifier", "action"])
        let valid: Bool
        switch action {
        case "click":
            valid = keys == actionBase.union(["x", "y", "button", "click_count", "target"])
                && (0 ... 1_000).contains(x ?? -1)
                && (0 ... 1_000).contains(y ?? -1)
                && button == "left"
                && clickCount == 1
                && Self.isValidTarget(target)
        case "type":
            valid = keys == actionBase.union(["text"])
                && Self.isValidText(text)
        case "key":
            valid = keys == actionBase.union(["key", "modifiers"])
                && Self.isValidKey(key)
                && Self.areValidModifiers(modifiers)
                && Self.isSafeShortcut(key: key, modifiers: modifiers)
        case "scroll":
            valid = keys == actionBase.union(["direction", "amount"])
                && ["up", "down", "left", "right"].contains(direction ?? "")
                && (1 ... 8).contains(amount ?? 0)
        default:
            valid = false
        }
        guard valid else { throw ComputerControlCommandError.invalidAction }
    }

    private static func isValidBundleIdentifier(_ value: String) -> Bool {
        guard (3 ... 255).contains(value.count), let first = value.first, let last = value.last else {
            return false
        }
        let alphanumeric = CharacterSet.alphanumerics
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
        return first.unicodeScalars.allSatisfy(alphanumeric.contains)
            && last.unicodeScalars.allSatisfy(alphanumeric.contains)
            && value.unicodeScalars.allSatisfy(allowed.contains)
    }

    private static func isValidText(_ value: String?) -> Bool {
        guard let value, (1 ... 500).contains(value.count) else { return false }
        return !value.unicodeScalars.contains { $0.value < 32 }
    }

    private static func isValidTarget(_ value: String?) -> Bool {
        guard let value, (1 ... 256).contains(value.count) else { return false }
        return !value.unicodeScalars.contains { $0.value < 32 }
    }

    private static func isValidKey(_ value: String?) -> Bool {
        guard let value else { return false }
        let named: Set<String> = [
            "down", "end", "enter", "escape", "home", "left", "page_down", "page_up",
            "right", "space", "tab", "up",
        ]
        return named.contains(value)
            || (value.count == 1 && value.first?.isASCII == true && value.first?.isLowercase == true)
    }

    private static func areValidModifiers(_ values: [String]?) -> Bool {
        guard let values, values.count <= 3, values.count == Set(values).count else { return false }
        let allowed: Set<String> = ["command", "control", "option", "shift"]
        return values.allSatisfy(allowed.contains)
    }

    private static func isSafeShortcut(key: String?, modifiers: [String]?) -> Bool {
        let modifierSet = Set(modifiers ?? [])
        if modifierSet.isEmpty {
            return true
        }
        if modifierSet == ["command"] {
            return ["a", "f", "l", "r", "t"].contains(key ?? "")
        }
        return modifierSet == ["shift"] && key == "tab"
    }
}

public struct ComputerPointerEvent: Equatable, Sendable {
    public let normalizedX: Int
    public let normalizedY: Int

    public init?(command: [String: Any]) {
        guard
            command["command"] as? String == "act",
            command["action"] as? String == "click",
            command["button"] as? String == "left",
            command["click_count"] as? Int == 1,
            let target = command["target"] as? String,
            (1 ... 256).contains(target.count),
            !target.unicodeScalars.contains(where: { $0.value < 32 }),
            let x = command["x"] as? Int,
            let y = command["y"] as? Int,
            (0 ... 1_000).contains(x),
            (0 ... 1_000).contains(y)
        else {
            return nil
        }
        normalizedX = x
        normalizedY = y
    }
}
