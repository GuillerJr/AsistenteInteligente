#if DEBUG
import AegisAudioCore
import Foundation

enum NotchPreviewMode: String {
    case idle
    case ambient
    case listening
    case processing
    case approval
    case speaking
    case failure
    case cycle

    init?(arguments: [String]) {
        guard
            let argument = arguments.first(where: { $0.hasPrefix("--notch-preview=") }),
            let value = argument.split(separator: "=", maxSplits: 1).last
        else {
            return nil
        }
        self.init(rawValue: String(value))
    }
}

@MainActor
extension MenuBarModel {
    func applyApprovalPreview() {
        guard let confirmation = IPCPendingConfirmation.debugPreview(
            toolName: "browser_open_url",
            summary: "Abrir en el navegador: https://docs.nvidia.com/nim/guide",
            expiresAt: Date().addingTimeInterval(120)
        ) else {
            return
        }
        pendingApproval = PendingApproval(
            jobID: UUID(),
            confirmation: confirmation
        )
    }

    func applyNotchPreview(_ mode: NotchPreviewMode) {
        daemonState = .online
        securityState = .intact
        providerState = .configured
        wakeWordListeningState = mode == .ambient ? .listening : .off
        voiceActivityLevel = mode == .listening ? 0.74 : 0
        hudActivity = [:]

        switch mode {
        case .idle, .ambient, .cycle:
            voiceState = .idle
        case .listening:
            voiceState = .listening
        case .processing:
            voiceState = .processing
            hudActivity = [.planner: 1, .criticalReasoner: 1]
        case .approval:
            voiceState = .awaitingAuthorization
        case .speaking:
            voiceState = .speaking
        case .failure:
            voiceState = .failed
        }
    }

    func runNotchPreviewCycle() async {
        let modes: [NotchPreviewMode] = [
            .idle, .listening, .processing, .approval, .speaking, .failure,
        ]
        while !Task.isCancelled {
            for mode in modes {
                applyNotchPreview(mode)
                do {
                    try await Task.sleep(for: .seconds(1.4))
                } catch {
                    return
                }
            }
        }
    }
}
#endif
