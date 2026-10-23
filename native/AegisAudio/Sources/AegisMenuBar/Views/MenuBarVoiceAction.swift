import AegisAudioCore
import SwiftUI

/// Keeps the existing action precedence and runtime permission gates.
struct MenuBarVoiceAction: View {
    @Environment(\.openWindow) private var openWindow
    let model: MenuBarModel
    var interactive = true

    var body: some View {
        Button(action: performPrimaryAction) {
            HStack(spacing: 12) {
                Image(systemName: primarySymbol)
                    .font(.system(size: 21, weight: .medium))
                    .frame(width: 26)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(primaryTitle).font(.system(size: 13, weight: .semibold))
                    Text(primarySubtitle)
                        .font(.system(size: 11))
                        .opacity(0.72)
                        .lineLimit(2)
                }
                Spacer(minLength: 0)
                Image(systemName: "arrow.up.right")
                    .font(.system(size: 11, weight: .medium))
                    .accessibilityHidden(true)
            }
            .frame(maxWidth: .infinity, minHeight: 38, alignment: .leading)
        }
        .buttonStyle(HUDActionStyle(accent: primaryColor, prominent: primaryAvailable))
        .disabled(!interactive || !primaryAvailable)
        .help(primarySubtitle)
    }

    private var voicePermissionPending: Bool {
        model.microphonePermission == .notDetermined || model.speechPermission == .notDetermined
    }

    private var voicePermissionBlocked: Bool {
        [.denied, .restricted].contains(model.microphonePermission)
            || [.denied, .restricted].contains(model.speechPermission)
    }

    private var primaryAvailable: Bool {
        model.interactionID != nil
            || model.activeComputerUseJobID != nil
            || model.canStartVoiceTurn
            || voicePermissionPending
            || voicePermissionBlocked
    }

    private var primaryTitle: String {
        if model.pendingApproval != nil { return String(localized: "Revisar solicitud", bundle: .module) }
        if model.pendingInput != nil { return String(localized: "Responder a Jarvis", bundle: .module) }
        if model.pendingBrowserSelection != nil { return String(localized: "Elegir navegador", bundle: .module) }
        if model.pendingRecovery != nil { return String(localized: "Revisar problema", bundle: .module) }
        if model.activeComputerUseJobID != nil { return String(localized: "Detener control del Mac", bundle: .module) }
        if model.canStartVoiceTurn { return String(localized: "Hablar con Jarvis", bundle: .module) }
        if voicePermissionBlocked { return String(localized: "Ajustar acceso a la voz", bundle: .module) }
        if voicePermissionPending { return String(localized: "Configurar voz", bundle: .module) }
        if !model.hybridBrainReady, model.providerState == .missing {
            return String(localized: "Configurar proveedor", bundle: .module)
        }
        if !model.hybridBrainReady, model.providerState == .unavailable {
            return String(localized: "Proveedor no disponible", bundle: .module)
        }
        return model.voiceState.isBusy ? String(localized: "Jarvis está trabajando", bundle: .module) : String(localized: "Voz no disponible", bundle: .module)
    }

    private var primarySubtitle: String {
        if model.pendingApproval != nil { return String(localized: "Confirmación de un solo uso", bundle: .module) }
        if model.pendingInput != nil { return String(localized: "Completar la información pendiente", bundle: .module) }
        if model.pendingBrowserSelection != nil { return String(localized: "Seleccionar dónde continuar", bundle: .module) }
        if model.pendingRecovery != nil { return String(localized: "Revisar el estado de la solicitud", bundle: .module) }
        if model.activeComputerUseJobID != nil { return String(localized: "Solicitar la cancelación activa", bundle: .module) }
        if model.canStartVoiceTurn, let speaker = model.lastSpeakerID {
            return String(localized: "Voz identificada: \(speaker)", bundle: .module)
        }
        if model.canStartVoiceTurn, model.speakerIdentityCapability == .ready {
            return String(localized: "Audio local · identidad activa", bundle: .module)
        }
        if model.canStartVoiceTurn { return String(localized: "Audio local · identidad pendiente", bundle: .module) }
        if voicePermissionBlocked { return String(localized: "Abrir privacidad de macOS", bundle: .module) }
        if voicePermissionPending { return String(localized: "Micrófono y reconocimiento", bundle: .module) }
        if !model.hybridBrainReady, model.providerState == .missing {
            return String(localized: "Revisa la configuración del proveedor", bundle: .module)
        }
        if !model.hybridBrainReady, model.providerState == .unavailable {
            return String(localized: "Revisa el proveedor y el servicio", bundle: .module)
        }
        return model.voiceState.isBusy ? String(localized: "Procesando solicitud", bundle: .module) : String(localized: "Revisa el servicio y su seguridad", bundle: .module)
    }

    private var primarySymbol: String {
        if model.pendingApproval != nil { return "hand.raised.fill" }
        if model.interactionID != nil { return "bubble.left.fill" }
        if model.activeComputerUseJobID != nil { return "stop.circle.fill" }
        if model.canStartVoiceTurn { return "waveform" }
        if voicePermissionBlocked { return "gearshape.fill" }
        if voicePermissionPending { return "mic.badge.plus" }
        if !model.hybridBrainReady, model.providerState == .missing { return "key.fill" }
        if !model.hybridBrainReady, model.providerState == .unavailable {
            return "questionmark.circle.fill"
        }
        return "waveform.slash"
    }

    private var primaryColor: Color {
        if model.pendingApproval != nil { return HUDStyle.accent(for: .warning) }
        if model.activeComputerUseJobID != nil { return HUDStyle.accent(for: .failure) }
        return HUDStyle.accent(for: .active)
    }

    private func performPrimaryAction() {
        if model.interactionID != nil {
            // The decision is a card in its chat or under the notch; bring it back up.
            if model.decisionBelongsToChat {
                RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
            } else {
                NotchApprovalPanelController.shared.show(model: model)
            }
        } else if model.activeComputerUseJobID != nil {
            Task { await model.cancelActiveComputerUse() }
        } else if model.canStartVoiceTurn {
            Task { await model.startVoiceTurn() }
        } else {
            configureVoice()
        }
    }

    private func configureVoice() {
        switch model.microphonePermission {
        case .denied, .restricted:
            model.openMicrophoneSettings()
            return
        case .authorized, .notDetermined, .unknown:
            break
        }
        switch model.speechPermission {
        case .denied, .restricted:
            model.openSpeechSettings()
            return
        case .authorized, .notDetermined, .unknown:
            break
        }
        Task { await model.requestUndeterminedPermissions() }
    }

}
