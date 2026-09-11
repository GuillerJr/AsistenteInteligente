import AegisAudioCore
import SwiftUI

struct MenuBarPermissionsView: View {
    let model: MenuBarModel
    var interactive = true
    @State private var expanded = false

    private var ready: Bool {
        model.microphonePermission == .authorized && model.speechPermission == .authorized
            && model.screenCaptureAuthorized && model.computerControlCapability == .ready
    }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                MenuBarPermissionItem(title: "Micrófono", symbol: "mic",
                    state: permissionTitle(model.microphonePermission), color: microphoneColor,
                    action: microphoneAction)
                MenuBarPermissionItem(title: "Reconocimiento", symbol: "quote.bubble",
                    state: permissionTitle(model.speechPermission), color: speechColor,
                    action: speechAction)
                MenuBarPermissionItem(title: "Pantalla", symbol: "rectangle.inset.filled",
                    state: model.screenCaptureAuthorized ? "Listo" : "Permitir",
                    color: HUDStyle.accent(for: model.screenCaptureAuthorized ? .active : .warning),
                    action: model.screenCaptureAuthorized ? nil : { model.requestScreenCapture() })
                MenuBarPermissionItem(title: "Control del Mac", symbol: "cursorarrow.motionlines",
                    state: computerControlState, color: computerControlColor,
                    action: model.computerControlCapability == .ready
                        ? nil : { Task { await model.requestComputerControlAccess() } })
                    .help(computerControlHelp)
            }
            .disabled(!interactive)
            .padding(.top, 10)
        } label: {
            HStack {
                Label("Permisos", systemImage: "lock.shield")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.white.opacity(0.86))
                Spacer()
                Text(ready ? "Todos listos" : "Revisar accesos")
                    .font(.system(size: 11))
                    .foregroundStyle(ready ? .white.opacity(0.55) : HUDStyle.accent(for: .warning))
            }
            .padding(.vertical, 4)
        }
        .tint(HUDStyle.accent(for: .active))
    }

    private var microphoneAction: (() -> Void)? {
        switch model.microphonePermission {
        case .notDetermined:
            return { Task { await model.requestMicrophone() } }
        case .denied, .restricted:
            return { model.openMicrophoneSettings() }
        case .authorized, .unknown:
            return nil
        }
    }

    private var speechAction: (() -> Void)? {
        switch model.speechPermission {
        case .notDetermined:
            return { Task { await model.requestSpeechRecognition() } }
        case .denied, .restricted:
            return { model.openSpeechSettings() }
        case .authorized, .unknown:
            return nil
        }
    }

    private var microphoneColor: Color {
        permissionColor(model.microphonePermission)
    }

    private var speechColor: Color {
        switch model.speechPermission {
        case .authorized: HUDStyle.accent(for: .active)
        case .notDetermined, .unknown: HUDStyle.accent(for: .warning)
        case .denied, .restricted: HUDStyle.accent(for: .failure)
        }
    }

    private var computerControlState: String {
        switch model.computerControlCapability {
        case .ready: "Listo"
        case .screenCaptureMissing: "Pantalla pendiente"
        case .accessibilityMissing: "Control pendiente"
        case .permissionsMissing: "Permitir"
        case .helperUnavailable: "No disponible"
        }
    }

    private var computerControlHelp: String {
        switch model.computerControlCapability {
        case .ready:
            "JarvisComputerHelper puede observar y controlar aplicaciones compatibles"
        case .screenCaptureMissing:
            "Falta permitir la pantalla a JarvisComputerHelper en Privacidad y seguridad"
        case .accessibilityMissing:
            "Activa Jarvis y JarvisComputerHelper en Control de dispositivos; Jarvis se reinicia al salir de Ajustes"
        case .permissionsMissing:
            "Jarvis solicitará primero pantalla y después control, sin superponer paneles de macOS"
        case .helperUnavailable:
            "El componente local JarvisComputerHelper no está disponible"
        }
    }

    private var computerControlColor: Color {
        switch model.computerControlCapability {
        case .ready: HUDStyle.accent(for: .active)
        case .screenCaptureMissing, .accessibilityMissing, .permissionsMissing: HUDStyle.accent(for: .warning)
        case .helperUnavailable: HUDStyle.accent(for: .failure)
        }
    }

    private func permissionColor(_ permission: MicrophonePermission) -> Color {
        switch permission {
        case .authorized: HUDStyle.accent(for: .active)
        case .notDetermined, .unknown: HUDStyle.accent(for: .warning)
        case .denied, .restricted: HUDStyle.accent(for: .failure)
        }
    }

    private func permissionTitle(_ permission: MicrophonePermission) -> String {
        switch permission {
        case .authorized: "Listo"
        case .denied, .restricted: "Abrir ajustes"
        case .notDetermined: "Permitir"
        case .unknown: "Revisar"
        }
    }

    private func permissionTitle(_ permission: SpeechRecognitionPermission) -> String {
        switch permission {
        case .authorized: "Listo"
        case .denied, .restricted: "Abrir ajustes"
        case .notDetermined: "Permitir"
        case .unknown: "Revisar"
        }
    }

}
