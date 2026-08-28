import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Local voice turn feedback")
struct LocalVoiceTurnFeedbackTests {
    @Test("Schedules one bounded reconciliation at approval expiry")
    func approvalExpiryDelayIsBounded() {
        let now = Date(timeIntervalSince1970: 1_000)

        #expect(
            LocalVoiceTurnFeedback.approvalExpiryDelay(
                expiresAt: now.addingTimeInterval(30),
                now: now
            ) == 30.05
        )
        #expect(
            LocalVoiceTurnFeedback.approvalExpiryDelay(
                expiresAt: now.addingTimeInterval(-5),
                now: now
            ) == 0.05
        )
        #expect(
            LocalVoiceTurnFeedback.approvalExpiryDelay(
                expiresAt: now.addingTimeInterval(600),
                now: now
            ) == 125
        )
    }

    @Test("Explains the first unavailable preflight dependency")
    func preflightFeedbackIsActionable() {
        #expect(
            LocalVoiceTurnFeedback.spokenPreflightFailure(
                microphoneAuthorized: false,
                speechRecognitionAuthorized: false,
                runtimeAvailable: false
            ) == "Necesito permiso para usar el micrófono."
        )
        #expect(
            LocalVoiceTurnFeedback.spokenPreflightFailure(
                microphoneAuthorized: true,
                speechRecognitionAuthorized: false,
                runtimeAvailable: true
            ) == "Necesito permiso para reconocer tu voz."
        )
        #expect(
            LocalVoiceTurnFeedback.spokenPreflightFailure(
                microphoneAuthorized: true,
                speechRecognitionAuthorized: true,
                runtimeAvailable: true,
                screenCaptureAuthorized: false
            ) == "Necesito permiso para ver la pantalla."
        )
        #expect(
            LocalVoiceTurnFeedback.spokenPreflightFailure(
                microphoneAuthorized: true,
                speechRecognitionAuthorized: true,
                runtimeAvailable: false
            ).contains("servicio local")
        )
    }

    @Test("Maps local capture failures without exposing implementation details")
    func captureFeedbackIsBounded() {
        #expect(
            LocalVoiceTurnFeedback.spokenCaptureFailure(for: "noAudibleInput")
                == "No alcancé a escucharte. Di Jarvis e inténtalo otra vez."
        )
        #expect(
            LocalVoiceTurnFeedback.spokenCaptureFailure(for: "recognizerUnavailable")
                == "El reconocimiento de voz local no está disponible."
        )
        #expect(
            LocalVoiceTurnFeedback.spokenCaptureFailure(for: "unexpected_private_error")
                == "No pude entenderte. Di Jarvis e inténtalo otra vez."
        )
        #expect(LocalVoiceTurnFeedback.spokenCaptureFailure(for: "cancelled") == nil)
    }

    @Test("Maps internal job failures to bounded safe Spanish")
    func jobFailureMessagesDoNotExposeCodes() {
        let expected = [
            "confirmation_expired": "La aprobación caducó. No ejecuté la acción.",
            "approved_tool_execution_failed": "No pude completar la acción aprobada.",
            "secret_material_rejected": (
                "No procesé la solicitud porque parecía contener una credencial."
            ),
            "swarm_execution_timeout": "La respuesta tardó demasiado. Inténtalo otra vez.",
            "unexpected_private_error_42": "No pude completar la solicitud.",
        ]

        for (code, message) in expected {
            let response = LocalVoiceTurnFeedback.spokenJobFailure(for: code)
            #expect(response == message)
            #expect(response?.contains(code) == false)
        }
    }

    @Test("Keeps interruption cancellation silent")
    func interruptionFailuresStaySilent() {
        #expect(LocalVoiceTurnFeedback.spokenJobFailure(for: "job_superseded") == nil)
        #expect(LocalVoiceTurnFeedback.spokenJobFailure(for: "job_cancelled") == nil)
    }
}
