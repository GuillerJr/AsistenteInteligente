import Foundation
@preconcurrency import LocalAuthentication

@MainActor
final class OwnerPresenceAuthenticator {
    func verify() async -> Bool {
        let context = LAContext()
        context.localizedCancelTitle = String(localized: "Continuar sin memoria", bundle: .module)
        context.localizedFallbackTitle = String(localized: "Usar contraseña", bundle: .module)
        context.touchIDAuthenticationAllowableReuseDuration = 0
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            return false
        }
        return await withCheckedContinuation { continuation in
            context.evaluatePolicy(
                .deviceOwnerAuthentication,
                localizedReason: String(localized: "Autorizar memoria privada de Jarvis durante 30 minutos", bundle: .module)
            ) { success, _ in
                continuation.resume(returning: success)
            }
        }
    }
}
