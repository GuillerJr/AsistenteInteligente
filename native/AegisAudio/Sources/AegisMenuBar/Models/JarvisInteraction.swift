import AegisAudioCore
import Foundation

struct PendingInput: Equatable, Sendable {
    let id: UUID
    let request: IPCInputRequest
}

struct PendingRecovery: Equatable, Sendable {
    let id = UUID()
    let code: String
    let message: String

    var outcomeUnknown: Bool {
        ["job_status_unavailable", "job_timeout", "swarm_execution_timeout", "approved_tool_execution_failed"].contains(code)
    }
}

extension MenuBarModel {
    var interactionID: String? {
        if let approval = pendingApproval { return "approval:\(approval.jobID):\(approval.confirmation.callDigest)" }
        if let browser = pendingBrowserSelection { return "browser:\(browser.transcript.captureID)" }
        if let input = pendingInput { return "input:\(input.id)" }
        if let recovery = pendingRecovery { return "recovery:\(recovery.id)" }
        return nil
    }
}
