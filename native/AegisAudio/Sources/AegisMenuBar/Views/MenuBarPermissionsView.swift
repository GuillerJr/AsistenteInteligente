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
                MenuBarPermissionItem(title: String(localized: "Micrófono", bundle: .module), symbol: "mic",
                    state: permissionTitle(model.microphonePermission), color: microphoneColor,
                    action: microphoneAction)
                MenuBarPermissionItem(title: String(localized: "Reconocimiento", bundle: .module), symbol: "quote.bubble",
                    state: permissionTitle(model.speechPermission), color: speechColor,
                    action: speechAction)
                MenuBarPermissionItem(title: String(localized: "Pantalla", bundle: .module), symbol: "rectangle.inset.filled",
                    state: model.screenCaptureAuthorized ? String(localized: "Listo", bundle: .module) : String(localized: "Permitir", bundle: .module),
                    color: HUDStyle.accent(for: model.screenCaptureAuthorized ? .active : .warning),
                    action: model.screenCaptureAuthorized ? nil : { model.requestScreenCapture() })
                MenuBarPermissionItem(title: String(localized: "Control del Mac", bundle: .module), symbol: "cursorarrow.motionlines",
                    state: computerControlState, color: computerControlColor,
                    action: model.computerControlCapability == .ready
                        ? nil : { Task { await model.requestComputerControlAccess() } })
                    .help(computerControlHelp)
            }
            .disabled(!interactive)
        } label: {
            HStack {
                Label(String(localized: "Permisos", bundle: .module), systemImage: "lock.shield")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(JarvisTheme.ink)
                Spacer()
                Text(ready ? String(localized: "Todos listos", bundle: .module) : String(localized: "Revisar accesos", bundle: .module))
                    .font(.system(size: 11))
                    .foregroundStyle(ready ? JarvisTheme.inkMuted : HUDStyle.accent(for: .warning))
            }
        }
        .disclosureGroupStyle(MenuBarDisclosureStyle())
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
        case .ready: String(localized: "Listo", bundle: .module)
        case .screenCaptureMissing: String(localized: "Pantalla pendiente", bundle: .module)
        case .accessibilityMissing: String(localized: "Control pendiente", bundle: .module)
        case .permissionsMissing: String(localized: "Permitir", bundle: .module)
        case .helperUnavailable: String(localized: "No disponible", bundle: .module)
        }
    }

    private var computerControlHelp: String {
        switch model.computerControlCapability {
        case .ready:
            String(localized: "JarvisComputerHelper puede observar y controlar aplicaciones compatibles", bundle: .module)
        case .screenCaptureMissing:
            String(localized: "Falta permitir la pantalla a JarvisComputerHelper en Privacidad y seguridad", bundle: .module)
        case .accessibilityMissing:
            String(localized: "Activa Jarvis y JarvisComputerHelper en Control de dispositivos; Jarvis se reinicia al salir de Ajustes", bundle: .module)
        case .permissionsMissing:
            String(localized: "Jarvis solicitará primero pantalla y después control, sin superponer paneles de macOS", bundle: .module)
        case .helperUnavailable:
            String(localized: "El componente local JarvisComputerHelper no está disponible", bundle: .module)
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
        case .authorized: String(localized: "Listo", bundle: .module)
        case .denied, .restricted: String(localized: "Abrir ajustes", bundle: .module)
        case .notDetermined: String(localized: "Permitir", bundle: .module)
        case .unknown: String(localized: "Revisar", bundle: .module)
        }
    }

    private func permissionTitle(_ permission: SpeechRecognitionPermission) -> String {
        switch permission {
        case .authorized: String(localized: "Listo", bundle: .module)
        case .denied, .restricted: String(localized: "Abrir ajustes", bundle: .module)
        case .notDetermined: String(localized: "Permitir", bundle: .module)
        case .unknown: String(localized: "Revisar", bundle: .module)
        }
    }

}
