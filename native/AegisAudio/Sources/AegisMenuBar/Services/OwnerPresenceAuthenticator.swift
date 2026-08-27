import Foundation
@preconcurrency import LocalAuthentication

@MainActor
final class OwnerPresenceAuthenticator {
    func verify() async -> Bool {
        let context = LAContext()
        context.localizedCancelTitle = "Continuar sin memoria"
        context.localizedFallbackTitle = "Usar contraseña"
        context.touchIDAuthenticationAllowableReuseDuration = 0
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            return false
        }
        return await withCheckedContinuation { continuation in
            context.evaluatePolicy(
                .deviceOwnerAuthentication,
                localizedReason: "Autorizar memoria privada de Jarvis durante 30 minutos"
            ) { success, _ in
                continuation.resume(returning: success)
            }
        }
    }
}
