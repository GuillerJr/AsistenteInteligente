import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Local voice job feedback")
struct LocalVoiceJobFeedbackTests {
    @Test("Schedules one bounded reconciliation at approval expiry")
    func approvalExpiryDelayIsBounded() {
        let now = Date(timeIntervalSince1970: 1_000)

        #expect(
            LocalVoiceJobFeedback.approvalExpiryDelay(
                expiresAt: now.addingTimeInterval(30),
                now: now
            ) == 30.05
        )
        #expect(
            LocalVoiceJobFeedback.approvalExpiryDelay(
                expiresAt: now.addingTimeInterval(-5),
                now: now
            ) == 0.05
        )
        #expect(
            LocalVoiceJobFeedback.approvalExpiryDelay(
                expiresAt: now.addingTimeInterval(600),
                now: now
            ) == 125
        )
    }

    @Test("Maps internal failures to bounded safe Spanish")
    func failureMessagesDoNotExposeCodes() {
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
            let response = LocalVoiceJobFeedback.spokenFailure(for: code)
            #expect(response == message)
            #expect(response?.contains(code) == false)
        }
    }

    @Test("Keeps interruption cancellation silent")
    func interruptionFailuresStaySilent() {
        #expect(LocalVoiceJobFeedback.spokenFailure(for: "job_superseded") == nil)
        #expect(LocalVoiceJobFeedback.spokenFailure(for: "job_cancelled") == nil)
    }
}
