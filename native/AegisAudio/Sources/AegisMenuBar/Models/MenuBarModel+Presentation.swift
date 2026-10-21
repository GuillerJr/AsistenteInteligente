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
            inputPending: pendingInput != nil,
            ambientListening: wakeWordListeningState == .listening,
            activity: hudActivity
        )
    }
}

extension AssistantPresentation.Tone {
    var color: Color {
        switch self {
        // Read on the black notch, so these are the dark-mode warm tones.
        case .neutral: Color(red: 0.97, green: 0.96, blue: 0.93).opacity(0.85)
        case .active: Color(red: 0.89, green: 0.60, blue: 0.47)
        case .warning: Color(red: 0.91, green: 0.70, blue: 0.42)
        case .failure: Color(red: 0.94, green: 0.56, blue: 0.49)
        case .success: Color(red: 0.61, green: 0.78, blue: 0.56)
        }
    }
}
