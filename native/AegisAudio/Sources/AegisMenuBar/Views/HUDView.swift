import AegisAudioCore
import SwiftUI

struct HUDView: View {
    let model: MenuBarModel
    let close: () -> Void

    var body: some View {
        ZStack {
            NodeSphereView(
                activity: model.hudActivity,
                voiceLevel: model.voiceActivityLevel
            )
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            VStack(spacing: 0) {
                HStack {
                    Text("JARVIS / SWARM")
                        .font(.caption.monospaced().weight(.medium))
                        .tracking(2)
                    Spacer()
                    Button(action: close) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title3)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Cerrar HUD")
                }
                .foregroundStyle(.cyan.opacity(0.85))
                .padding(18)

                Spacer()

                Text(status)
                    .font(.caption2.monospaced().weight(.medium))
                    .tracking(1.5)
                    .foregroundStyle(.cyan.opacity(0.72))
                    .padding(.bottom, 18)
            }
        }
        .frame(width: 560, height: 560)
        .background(Color.clear)
    }

    private var status: String {
        if model.securityState == .compromised {
            return "AUDITORÍA COMPROMETIDA"
        }
        if model.daemonState != .online {
            return "DAEMON NO DISPONIBLE"
        }
        let active = model.hudActivity.keys.sorted { $0.rawValue < $1.rawValue }
        if active.isEmpty {
            return "EN REPOSO"
        }
        return active.map(roleTitle).joined(separator: "  /  ")
    }

    private func roleTitle(_ role: IPCSwarmAgentRole) -> String {
        switch role {
        case .router:
            "ROUTER"
        case .planner:
            "PLANNER"
        case .criticalReasoner:
            "REASONER"
        case .codeSecurity:
            "CODE / SECURITY"
        case .vision:
            "VISION"
        case .omni:
            "OMNI"
        case .synthesizer:
            "SYNTHESIZER"
        }
    }
}
