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
        .menuBarExtraStyle(.window)

        Window("Aprobación de Jarvis", id: "approval") {
            ApprovalView(model: model)
        }
        .windowResizability(.contentSize)

        Window("Activación por voz de Jarvis", id: "wake-word-enrollment") {
            WakeWordEnrollmentView(model: model)
        }
        .windowResizability(.contentSize)
    }
}

private struct MenuBarLabel: View {
    let model: MenuBarModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Label("Jarvis", systemImage: "circle.hexagongrid.fill")
            .labelStyle(.iconOnly)
            .task {
                model.startPowerMonitoring()
                NotchPanelController.shared.show(model: model) {
                    openWindow(id: "approval")
                }
                model.voiceShortcutAvailable = VoiceHotKeyController.shared.install {
                    Task { await model.startVoiceTurn() }
                }
                await model.initializeWakeWordListening()
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
}
