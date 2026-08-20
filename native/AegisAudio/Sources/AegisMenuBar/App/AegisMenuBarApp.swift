import SwiftUI

@main
struct AegisMenuBarApp: App {
    @State private var model = MenuBarModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(model: model)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.menu)

        Window("Aprobación de Jarvis", id: "approval") {
            ApprovalView(model: model)
        }
        .windowResizability(.contentSize)
    }
}

private struct MenuBarLabel: View {
    let model: MenuBarModel

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            JarvisMenuBarIcon()
                .frame(width: 18, height: 18)
            Circle()
                .fill(indicator.color)
                .frame(width: 5, height: 5)
                .overlay {
                    Circle().stroke(Color.primary.opacity(0.7), lineWidth: 0.5)
                }
        }
            .frame(width: 18, height: 18)
            .accessibilityLabel("Jarvis")
            .accessibilityValue(indicator.description)
            .task {
                model.voiceShortcutAvailable = VoiceHotKeyController.shared.install {
                    Task { await model.startVoiceTurn() }
                }
                let arguments = ProcessInfo.processInfo.arguments
                if arguments.contains("--request-permissions") {
                    await model.requestUndeterminedPermissions()
                }
                if arguments.contains("--voice-turn") {
                    await model.startVoiceTurn()
                }
                if arguments.contains("--hud") {
                    HUDPanelController.shared.show(model: model)
                }
                await model.monitor()
            }
    }

    private var indicator: (color: Color, description: String) {
        if model.securityState == .compromised
            || model.securityState == .unavailable
            || model.daemonState == .securityFailure {
            return (.red, "seguridad bloqueada")
        }
        if model.pendingApproval != nil || model.voiceState == .awaitingApproval {
            return (.orange, "aprobación pendiente")
        }
        switch model.voiceState {
        case .listening, .submitting, .processing, .speaking:
            return (.cyan, model.voiceState.title)
        case .idle, .awaitingApproval, .completed, .failed:
            break
        }
        let description = "daemon \(model.daemonState.title), auditoría \(model.securityState.title)"
        if model.daemonState == .online && model.securityState == .intact {
            return (.green, description)
        }
        return (.gray, description)
    }
}
