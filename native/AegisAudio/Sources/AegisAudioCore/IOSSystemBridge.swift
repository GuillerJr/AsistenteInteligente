import AppIntents
import Foundation
import LocalAuthentication

public enum JarvisFocusMode: String, AppEnum, Codable, Sendable {
    case normal
    case work
    case sleeping
    case personal

    public static let typeDisplayRepresentation = TypeDisplayRepresentation(
        name: "Modo de Jarvis"
    )

    public static let caseDisplayRepresentations: [JarvisFocusMode: DisplayRepresentation] = [
        .normal: "Normal",
        .work: "Trabajo",
        .sleeping: "Descanso",
        .personal: "Personal",
    ]
}

public enum IOSSystemBridgeError: Error, Sendable, Equatable {
    case localIPCUnavailable
    case daemonRejectedFocusUpdate
    case biometricUnavailable
    case biometricDenied
}

public enum IOSSystemBridge {
    public static func publishFocus(
        mode: JarvisFocusMode,
        active: Bool
    ) async throws {
        let accepted = try await Task.detached(priority: .utility) {
            let store = try MacOSIPCSecretStore()
            let client = try LocalIPCClient(secretStore: store)
            return try client.updateFocusMode(
                active ? mode.rawValue : "normal",
                active: active
            ).ok
        }.value
        guard accepted else {
            throw IOSSystemBridgeError.daemonRejectedFocusUpdate
        }
    }
}

@available(macOS 14.0, *)
public struct JarvisFocusFilterIntent: SetFocusFilterIntent {
    public static let title: LocalizedStringResource = "Ajustar prioridad de Jarvis"
    public static let description = IntentDescription(
        "Sincroniza la prioridad de planificación de Jarvis con este modo de Concentración."
    )

    @Parameter(title: "Modo", default: .work)
    public var mode: JarvisFocusMode

    public init() {}

    public init(mode: JarvisFocusMode) {
        self.mode = mode
    }

    public var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "Jarvis: \(mode.rawValue)")
    }

    public func perform() async throws -> some IntentResult {
        try await IOSSystemBridge.publishFocus(mode: mode, active: mode != .normal)
        return .result()
    }
}

public struct MobileUserPresenceAuthorizer: Sendable {
    public init() {}

    public func authorize(reason: String) async throws {
        guard
            !reason.isEmpty,
            reason.utf8.count <= 256,
            reason.unicodeScalars.allSatisfy({
                !CharacterSet.controlCharacters.contains($0)
            })
        else {
            throw IOSSystemBridgeError.biometricDenied
        }
        let context = LAContext()
        context.localizedCancelTitle = "Cancelar"
        var policyError: NSError?
        guard context.canEvaluatePolicy(
            .deviceOwnerAuthenticationWithBiometrics,
            error: &policyError
        ) else {
            throw IOSSystemBridgeError.biometricUnavailable
        }
        let accepted = try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Bool, Error>) in
            context.evaluatePolicy(
                .deviceOwnerAuthenticationWithBiometrics,
                localizedReason: reason
            ) { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: success)
                }
            }
        }
        guard accepted else {
            throw IOSSystemBridgeError.biometricDenied
        }
    }
}
