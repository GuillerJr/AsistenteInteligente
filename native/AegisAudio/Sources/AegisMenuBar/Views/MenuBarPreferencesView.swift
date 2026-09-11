import AegisAudioCore
import SwiftUI

struct MenuBarPreferencesView: View {
    @Environment(\.openWindow) private var openWindow
    let model: MenuBarModel
    var interactive = true

    var body: some View {
        VStack(spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: wakeWordListeningSymbol)
                    .font(.system(size: 16))
                    .foregroundStyle(wakeWordColor)
                    .frame(width: 24)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Activación «Jarvis»").font(.system(size: 12, weight: .medium))
                    Text(wakeWordSummary)
                        .font(.system(size: 11))
                        .foregroundStyle(.white.opacity(0.58))
                        .lineLimit(2)
                }
                Spacer(minLength: 4)
                Button(wakeWordActionTitle, action: performWakeWordAction)
                    .buttonStyle(MenuBarSmallButtonStyle())
                    .disabled(!interactive || model.wakeWordListeningState == .starting)
                if model.wakeWordCapability == .ready {
                    MenuBarIconButton(symbol: "slider.horizontal.3", title: "Configurar activación") {
                        openWindow(id: "wake-word-enrollment")
                    }
                    .disabled(!interactive)
                }
            }

            HStack(spacing: 10) {
                Image(systemName: model.proactiveAlertsEnabled ? "bell.badge" : "bell.slash")
                    .font(.system(size: 16))
                    .foregroundStyle(.white.opacity(0.6))
                    .frame(width: 24)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Alertas proactivas").font(.system(size: 12, weight: .medium))
                    Text("Batería, red, agenda y rendimiento")
                        .font(.system(size: 11))
                        .foregroundStyle(.white.opacity(0.58))
                        .lineLimit(2)
                }
                Spacer(minLength: 4)
                Toggle("Alertas proactivas", isOn: Binding(
                    get: { model.proactiveAlertsEnabled },
                    set: { enabled in
                        guard interactive else { return }
                        Task { await model.setProactiveAlertsEnabled(enabled) }
                    }
                ))
                .labelsHidden()
                .toggleStyle(.switch)
                .controlSize(.small)
                .tint(HUDStyle.accent(for: .active))
                .disabled(!interactive)
                .accessibilityHint("Avisos sobre batería, red, agenda y rendimiento")
            }
        }
        .foregroundStyle(.white.opacity(0.9))
    }

    private var wakeWordColor: Color {
        return switch model.wakeWordListeningState {
        case .listening: HUDStyle.accent(for: .active)
        case .starting, .recovering: HUDStyle.accent(for: .active)
        case .failed: HUDStyle.accent(for: .failure)
        case .paused: HUDStyle.accent(for: .warning)
        case .unavailable, .off: .white.opacity(0.55)
        }
    }

    private var wakeWordSummary: String {
        guard model.wakeWordCapability == .ready else {
            return model.wakeWordCapability == .invalid ? "Modelo inválido" : "Configuración pendiente"
        }
        return switch model.wakeWordListeningState {
        case .unavailable: "No disponible"
        case .off: "Escucha desactivada"
        case .starting: "Iniciando escucha"
        case .recovering: "Recuperando escucha"
        case .listening: "Escuchando en segundo plano"
        case .paused: model.wakeWordPauseReason?.title ?? "En pausa"
        case .failed: "La escucha falló"
        }
    }

    private var wakeWordActionTitle: String {
        guard model.wakeWordCapability == .ready else { return "Preparar" }
        if model.wakeWordListeningState == .failed, model.wakeWordOptedIn { return "Reintentar" }
        return model.wakeWordOptedIn ? "Desactivar" : "Activar"
    }

    private func performWakeWordAction() {
        guard model.wakeWordCapability == .ready else {
            openWindow(id: "wake-word-enrollment")
            return
        }
        let enabled = model.wakeWordListeningState == .failed && model.wakeWordOptedIn
            ? true
            : !model.wakeWordOptedIn
        Task { await model.setWakeWordListeningEnabled(enabled) }
    }

    private var wakeWordListeningSymbol: String {
        switch model.wakeWordListeningState {
        case .listening: "ear.badge.waveform"
        case .starting, .recovering: "hourglass.circle"
        case .paused: "pause.circle"
        case .unavailable, .off: "ear"
        case .failed: "exclamationmark.triangle"
        }
    }
}
