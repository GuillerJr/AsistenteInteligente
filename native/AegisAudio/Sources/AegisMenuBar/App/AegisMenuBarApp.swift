import AppKit
import SwiftUI

@MainActor
private final class AegisAppDelegate: NSObject, NSApplicationDelegate {
    let model = MenuBarModel()

    func applicationWillFinishLaunching(_ notification: Notification) {
        model.startPowerMonitoring()
    }
}

@main
struct AegisMenuBarApp: App {
    @NSApplicationDelegateAdaptor(AegisAppDelegate.self) private var appDelegate

    private var model: MenuBarModel {
        appDelegate.model
    }

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

        Window("Identidad de voz de Jarvis", id: "speaker-enrollment") {
            SpeakerEnrollmentView(model: model)
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
            .onChange(of: model.pendingApproval) { _, pendingApproval in
                guard pendingApproval != nil else { return }
                openWindow(id: "approval")
            }
            .task {
                let arguments = ProcessInfo.processInfo.arguments
#if DEBUG
                if let preview = NotchPreviewMode(arguments: arguments) {
                    model.applyNotchPreview(preview)
                    NotchPanelController.shared.show(model: model)
                    if preview == .approval {
                        model.applyApprovalPreview()
                    }
                    if preview == .cycle {
                        await model.runNotchPreviewCycle()
                    }
                    return
                }
#endif
                await model.initializeOperationalEvidence()
                await model.initializeProactiveAlerts()
                model.startPrivacyChangeMonitoring()
                NotchPanelController.shared.show(model: model)
                model.voiceShortcutAvailable = VoiceHotKeyController.shared.install {
                    Task { await model.startVoiceTurn() }
                }
                if arguments.contains("--hud") {
                    HUDPanelController.shared.show(model: model)
                }
                await model.initializeWakeWordListening()
                if arguments.contains("--enable-wake-word") {
                    await model.setWakeWordListeningEnabled(true)
                }
                if arguments.contains("--request-permissions") {
                    await model.requestAllPrivacyPermissions()
                }
                if arguments.contains("--request-computer-permissions") {
                    model.requestScreenCapture()
                    await model.requestComputerControlAccess()
                }
                if arguments.contains("--voice-turn") {
                    await model.startVoiceTurn()
                }
                await model.runBackgroundMonitoring()
            }
    }
}
