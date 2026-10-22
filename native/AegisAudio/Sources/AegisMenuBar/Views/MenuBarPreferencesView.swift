import AegisAudioCore
import SwiftUI

struct MenuBarPreferencesView: View {
    @Environment(\.openWindow) private var openWindow
    let model: MenuBarModel
    var interactive = true

    var body: some View {
        VStack(spacing: 16) {
            JarvisAppearancePicker().disabled(!interactive)
            HStack(spacing: 10) {
                Image(systemName: wakeWordListeningSymbol)
                    .font(.system(size: 16))
                    .foregroundStyle(wakeWordColor)
                    .frame(width: 24)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(String(localized: "Activación «Jarvis»", bundle: .module)).font(.system(size: 12, weight: .medium))
                    Text(wakeWordSummary)
                        .font(.system(size: 11))
                        .foregroundStyle(JarvisTheme.inkSecondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 4)
                Button(wakeWordActionTitle, action: performWakeWordAction)
                    .buttonStyle(MenuBarSmallButtonStyle())
                    .disabled(!interactive || model.wakeWordListeningState == .starting)
                if model.wakeWordCapability == .ready {
                    MenuBarIconButton(symbol: "slider.horizontal.3", title: String(localized: "Configurar activación", bundle: .module)) {
                        RequestedWindowFocus.open("wake-word-enrollment") { openWindow(id: "wake-word-enrollment") }
                    }
                    .disabled(!interactive)
                }
            }

            HStack(spacing: 10) {
                Image(systemName: model.proactiveAlertsEnabled ? "bell.badge" : "bell.slash")
                    .font(.system(size: 16))
                    .foregroundStyle(JarvisTheme.inkSecondary)
                    .frame(width: 24)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(String(localized: "Alertas proactivas", bundle: .module)).font(.system(size: 12, weight: .medium))
                    Text(String(localized: "Batería, red, agenda y rendimiento", bundle: .module))
                        .font(.system(size: 11))
                        .foregroundStyle(JarvisTheme.inkSecondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 4)
                Toggle(String(localized: "Alertas proactivas", bundle: .module), isOn: Binding(
                    get: { model.proactiveAlertsEnabled },
                    set: { enabled in
                        guard interactive else { return }
                        Task { await model.setProactiveAlertsEnabled(enabled) }
                    }
                ))
                .toggleStyle(JarvisToggleControlStyle())
                .disabled(!interactive)
                .accessibilityHint(String(localized: "Avisos sobre batería, red, agenda y rendimiento", bundle: .module))
            }
        }
        .foregroundStyle(JarvisTheme.ink)
    }

    private var wakeWordColor: Color {
        return switch model.wakeWordListeningState {
        case .listening: HUDStyle.accent(for: .active)
        case .starting, .recovering: HUDStyle.accent(for: .active)
        case .failed: HUDStyle.accent(for: .failure)
        case .paused: HUDStyle.accent(for: .warning)
        case .unavailable, .off: JarvisTheme.inkMuted
        }
    }

    private var wakeWordSummary: String {
        guard model.wakeWordCapability == .ready else {
            return model.wakeWordCapability == .invalid ? String(localized: "Modelo inválido", bundle: .module) : String(localized: "Configuración pendiente", bundle: .module)
        }
        return switch model.wakeWordListeningState {
        case .unavailable: String(localized: "No disponible", bundle: .module)
        case .off: String(localized: "Escucha desactivada", bundle: .module)
        case .starting: String(localized: "Iniciando escucha", bundle: .module)
        case .recovering: String(localized: "Recuperando escucha", bundle: .module)
        case .listening: String(localized: "Escuchando en segundo plano", bundle: .module)
        case .paused: model.wakeWordPauseReason?.title ?? String(localized: "En pausa", bundle: .module)
        case .failed: String(localized: "La escucha falló", bundle: .module)
        }
    }

    private var wakeWordActionTitle: String {
        guard model.wakeWordCapability == .ready else { return String(localized: "Preparar", bundle: .module) }
        if model.wakeWordListeningState == .failed, model.wakeWordOptedIn { return String(localized: "Reintentar", bundle: .module) }
        return model.wakeWordOptedIn ? String(localized: "Desactivar", bundle: .module) : String(localized: "Activar", bundle: .module)
    }

    private func performWakeWordAction() {
        guard model.wakeWordCapability == .ready else {
            RequestedWindowFocus.open("wake-word-enrollment") { openWindow(id: "wake-word-enrollment") }
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
