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
        Label(
            "Pantalla: \(model.screenCaptureAuthorized ? "permitida" : "requiere permiso")",
            systemImage: permissionSymbol(model.screenCaptureAuthorized)
        )
        if !model.screenCaptureAuthorized {
            Button("Permitir pantalla") {
                model.requestScreenCapture()
            }
        }

        Divider()
        Label(
            "Asistente: \(model.voiceState.title)",
            systemImage: model.voiceState.symbol
        )
        Button("Hablar") {
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
        Button("Preguntar sobre pantalla") {
            Task { await model.startScreenVoiceTurn() }
        }
        .disabled(!model.canStartScreenTurn)
        Label(
            model.voiceShortcutAvailable ? "Atajo: ⌃⇧Espacio" : "Atajo: no disponible",
            systemImage: model.voiceShortcutAvailable ? "keyboard" : "keyboard.badge.exclamationmark"
        )
        Label(
            wakeWordCapabilityTitle,
            systemImage: wakeWordCapabilitySymbol
        )
        if model.wakeWordCapability == .ready {
            Label(wakeWordListeningTitle, systemImage: wakeWordListeningSymbol)
            wakeWordActions
        }
        Button("Preparar activación por “Jarvis”…") {
            openWindow(id: "wake-word-enrollment")
        }

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
        Button("Salir de Jarvis") {
            NSApplication.shared.terminate(nil)
        }
    }

    @ViewBuilder
    private var wakeWordActions: some View {
        if model.wakeWordListeningState == .failed, model.wakeWordOptedIn {
            Button("Reintentar escucha “Jarvis”") {
                Task { await model.setWakeWordListeningEnabled(true) }
            }
            Button("Desactivar escucha “Jarvis”") {
                Task { await model.setWakeWordListeningEnabled(false) }
            }
        } else {
            Button(
                model.wakeWordOptedIn
                    ? "Desactivar escucha “Jarvis”"
                    : "Activar escucha “Jarvis”"
            ) {
                Task { await model.setWakeWordListeningEnabled(!model.wakeWordOptedIn) }
            }
            .disabled(model.wakeWordListeningState == .starting)
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

    private var wakeWordCapabilityTitle: String {
        switch model.wakeWordCapability {
        case .missing:
            "Activación “Jarvis”: modelo pendiente"
        case .invalid:
            "Activación “Jarvis”: modelo inválido"
        case .ready:
            "Activación “Jarvis”: modelo disponible"
        }
    }

    private var wakeWordCapabilitySymbol: String {
        switch model.wakeWordCapability {
        case .missing:
            "waveform.slash"
        case .invalid:
            "exclamationmark.triangle"
        case .ready:
            "waveform.badge.magnifyingglass"
        }
    }

    private var wakeWordListeningTitle: String {
        switch model.wakeWordListeningState {
        case .unavailable:
            "Escucha “Jarvis”: no disponible"
        case .off:
            "Escucha “Jarvis”: apagada"
        case .starting:
            "Escucha “Jarvis”: iniciando"
        case .listening:
            "Escucha “Jarvis”: activa"
        case .paused:
            "Escucha “Jarvis”: en pausa"
        case .failed:
            "Escucha “Jarvis”: falló"
        }
    }

    private var wakeWordListeningSymbol: String {
        switch model.wakeWordListeningState {
        case .listening:
            "ear.badge.waveform"
        case .starting:
            "hourglass.circle"
        case .paused:
            "pause.circle"
        case .unavailable, .off:
            "ear"
        case .failed:
            "exclamationmark.triangle"
        }
    }
}
