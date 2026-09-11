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
        model.pendingApproval != nil
            || model.activeComputerUseJobID != nil
            || model.canStartVoiceTurn
            || voicePermissionPending
            || voicePermissionBlocked
    }

    private var primaryTitle: String {
        if model.pendingApproval != nil { return "Revisar solicitud" }
        if model.activeComputerUseJobID != nil { return "Detener control del Mac" }
        if model.canStartVoiceTurn { return "Hablar con Jarvis" }
        if voicePermissionBlocked { return "Ajustar acceso a la voz" }
        if voicePermissionPending { return "Configurar voz" }
        if !model.hybridBrainReady, model.providerState == .missing {
            return "Configurar proveedor"
        }
        if !model.hybridBrainReady, model.providerState == .unavailable {
            return "Proveedor no disponible"
        }
        return model.voiceState.isBusy ? "Jarvis está trabajando" : "Voz no disponible"
    }

    private var primarySubtitle: String {
        if model.pendingApproval != nil { return "Confirmación de un solo uso" }
        if model.activeComputerUseJobID != nil { return "Solicitar la cancelación activa" }
        if model.canStartVoiceTurn, let speaker = model.lastSpeakerID {
            return "Voz identificada: \(speaker)"
        }
        if model.canStartVoiceTurn, model.speakerIdentityCapability == .ready {
            return "Audio local · identidad activa"
        }
        if model.canStartVoiceTurn { return "Audio local · identidad pendiente" }
        if voicePermissionBlocked { return "Abrir privacidad de macOS" }
        if voicePermissionPending { return "Micrófono y reconocimiento" }
        if !model.hybridBrainReady, model.providerState == .missing {
            return "Revisa la configuración del proveedor"
        }
        if !model.hybridBrainReady, model.providerState == .unavailable {
            return "Revisa el proveedor y el servicio"
        }
        return model.voiceState.isBusy ? "Procesando solicitud" : "Revisa el servicio y su seguridad"
    }

    private var primarySymbol: String {
        if model.pendingApproval != nil { return "hand.raised.fill" }
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
        if model.pendingApproval != nil {
            openWindow(id: "approval")
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
