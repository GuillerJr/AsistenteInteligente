import AegisAudioCore
import AppKit
import SwiftUI

struct MenuBarView: View {
    let model: MenuBarModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Label(
            "Daemon: \(model.daemonState.title)",
            systemImage: model.daemonState.symbol
        )
        Button("Comprobar daemon") {
            Task { await model.refreshDaemon() }
        }
        .disabled(model.daemonState == .checking)

        Label(
            "Auditoría: \(model.securityState.title)",
            systemImage: model.securityState.symbol
        )

        Divider()
        Label(
            "Micrófono: \(permissionTitle(model.microphonePermission))",
            systemImage: permissionSymbol(model.microphonePermission == .authorized)
        )
        microphoneAction
        Label(
            "Speech: \(permissionTitle(model.speechPermission))",
            systemImage: permissionSymbol(model.speechPermission == .authorized)
        )
        speechAction

        Divider()
        Label(
            "Asistente: \(model.voiceState.title)",
            systemImage: model.voiceState.symbol
        )
        Button("Hablar 8 s") {
            Task { await model.startVoiceTurn() }
        }
        .disabled(!model.canStartVoiceTurn)
        Button("Preguntar sobre imagen…") {
            guard let url = ImageFilePicker.chooseImage() else {
                return
            }
            Task { await model.startImageVoiceTurn(fileURL: url) }
        }
        .disabled(!model.canStartVoiceTurn)
        Label(
            model.voiceShortcutAvailable ? "Atajo: ⌃⇧Espacio" : "Atajo: no disponible",
            systemImage: model.voiceShortcutAvailable ? "keyboard" : "keyboard.badge.exclamationmark"
        )

        Button("Mostrar HUD…") {
            HUDPanelController.shared.show(model: model)
        }

        if model.pendingApproval != nil {
            Divider()
            Label("Aprobación pendiente", systemImage: "exclamationmark.shield.fill")
            Button("Revisar aprobación…") {
                openWindow(id: "approval")
            }
        }

        Divider()
        Button("Salir de Aegis") {
            NSApplication.shared.terminate(nil)
        }
    }

    @ViewBuilder
    private var microphoneAction: some View {
        switch model.microphonePermission {
        case .notDetermined:
            Button("Permitir micrófono") {
                Task { await model.requestMicrophone() }
            }
        case .denied, .restricted:
            Button("Ajustes de micrófono") {
                model.openMicrophoneSettings()
            }
        case .authorized, .unknown:
            EmptyView()
        }
    }

    @ViewBuilder
    private var speechAction: some View {
        switch model.speechPermission {
        case .notDetermined:
            Button("Permitir Speech") {
                Task { await model.requestSpeechRecognition() }
            }
        case .denied, .restricted:
            Button("Ajustes de Speech") {
                model.openSpeechSettings()
            }
        case .authorized, .unknown:
            EmptyView()
        }
    }

    private func permissionTitle(_ permission: MicrophonePermission) -> String {
        switch permission {
        case .authorized:
            "permitido"
        case .denied:
            "denegado"
        case .restricted:
            "restringido"
        case .notDetermined:
            "pendiente"
        case .unknown:
            "desconocido"
        }
    }

    private func permissionTitle(_ permission: SpeechRecognitionPermission) -> String {
        switch permission {
        case .authorized:
            "permitido"
        case .denied:
            "denegado"
        case .restricted:
            "restringido"
        case .notDetermined:
            "pendiente"
        case .unknown:
            "desconocido"
        }
    }

    private func permissionSymbol(_ authorized: Bool) -> String {
        authorized ? "checkmark.circle" : "exclamationmark.circle"
    }
}
