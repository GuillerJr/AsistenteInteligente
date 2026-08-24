import AegisAudioCore
import AppKit
import SwiftUI

enum SwarmRoleVisuals {
    static let orderedRoles: [IPCSwarmAgentRole] = [
        .router,
        .planner,
        .criticalReasoner,
        .codeSecurity,
        .vision,
        .omni,
        .synthesizer,
    ]

    static func title(for role: IPCSwarmAgentRole) -> String {
        switch role {
        case .router: "ROUTER"
        case .planner: "PLANNER"
        case .criticalReasoner: "REASONER"
        case .codeSecurity: "SECURITY"
        case .vision: "VISION"
        case .omni: "OMNI"
        case .synthesizer: "SYNTH"
        }
    }

    static func color(for role: IPCSwarmAgentRole) -> Color {
        Color(nsColor: sceneColor(for: role))
    }

    static func sceneColor(for role: IPCSwarmAgentRole) -> NSColor {
        switch role {
        case .router: .systemCyan
        case .planner: .systemBlue
        case .criticalReasoner: .systemPink
        case .codeSecurity: .systemOrange
        case .vision: .systemPurple
        case .omni: .systemTeal
        case .synthesizer: .systemGreen
        }
    }
}
