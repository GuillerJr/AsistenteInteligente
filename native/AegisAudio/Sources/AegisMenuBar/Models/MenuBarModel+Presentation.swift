import AegisAudioCore
import SwiftUI

extension MenuBarModel {
    var presentationStatus: AssistantPresentation {
        AssistantPresentation.resolve(
            daemon: daemonState,
            security: securityState,
            provider: providerState,
            localBrainAvailable: localBrainAvailable,
            voice: voiceState,
            failureCode: lastVoiceFailureCode,
            interrupting: isInterruptingSpeech,
            approvalPending: pendingApproval != nil,
            browserSelectionPending: pendingBrowserSelection != nil,
            ambientListening: wakeWordListeningState == .listening,
            activity: hudActivity
        )
    }
}

extension AssistantPresentation.Tone {
    var color: Color {
        switch self {
        case .neutral: .cyan
        case .active: .purple
        case .warning: .orange
        case .failure: .red
        case .success: .green
        }
    }
}
