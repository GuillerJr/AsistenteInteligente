import AegisAudioCore
import SwiftUI

struct NotchExpandedContent: View {
    let model: MenuBarModel
    let mood: JarvisNotchDotMood
    let pinned: Bool
    let interactive: Bool
    let reduceMotion: Bool
    let togglePinned: () -> Void
    let dismiss: () -> Void
    let openWindow: (String) -> Void

    private var status: AssistantPresentation { model.presentationStatus }
    private var accent: Color { status.tone.color }

    var body: some View {
        VStack(spacing: 12) {
            HStack(spacing: 14) {
                JarvisNotchDot(color: accent, mood: mood, energy: Double(model.voiceActivityLevel),
                               engaged: true, forceReduceMotion: reduceMotion)
                    .frame(width: 58, height: 58)
                VStack(alignment: .leading, spacing: 5) {
                    Text(status.title).font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white).lineLimit(1)
                    Text(status.detail).font(.system(size: 11))
                        .foregroundStyle(.white.opacity(0.65)).lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                VStack(spacing: 8) {
                    Button(action: togglePinned) {
                        Image(systemName: pinned ? "pin.fill" : "pin")
                    }
                    .help(pinned ? "Dejar de fijar el notch" : "Mantener el notch abierto")
                    .accessibilityLabel(pinned ? "Dejar de fijar el notch" : "Fijar notch")
                    Button(action: dismiss) { Image(systemName: "xmark") }
                        .help("Contraer notch").accessibilityLabel("Contraer notch")
                }
                .font(.system(size: 11)).foregroundStyle(.white.opacity(0.65))
                .buttonStyle(.plain)
            }
            .frame(height: 72)

            HStack(spacing: 8) {
                if model.interactionID != nil {
                    action("Revisar", symbol: "hand.raised", accent: .orange) { openWindow("approval") }
                } else {
                    action("Hablar", symbol: "waveform", accent: accent) {
                        // Recheck runtime gates at the moment of the click.
                        guard model.canStartVoiceTurn else { return }
                        Task { await model.startVoiceTurn() }
                    }
                    .disabled(!model.canStartVoiceTurn)
                    .help(model.canStartVoiceTurn ? "Iniciar conversación por voz" : "Voz no disponible; revisa Configuración")
                }
                action("Chat", symbol: "bubble.left.and.bubble.right", accent: accent) { openWindow("chat") }
                action("Ajustes", symbol: "slider.horizontal.3", accent: accent) { openWindow("settings") }
            }
            HStack(spacing: 5) {
                Image(systemName: pinned ? "pin.fill" : "cursorarrow")
                Text(pinned ? "Fijado · clic fuera para cerrar" : "Clic en el notch para fijarlo")
                Spacer()
            }
            .font(.system(size: 10)).foregroundStyle(.white.opacity(0.4))
        }
        .padding(.horizontal, 18).padding(.top, 6).padding(.bottom, 14)
        .disabled(!interactive)
    }

    private func action(_ title: String, symbol: String, accent: Color,
                        perform: @escaping () -> Void) -> some View {
        Button(action: perform) {
            Label(title, systemImage: symbol).font(.system(size: 11, weight: .medium))
                .frame(maxWidth: .infinity, minHeight: 34)
        }
        .buttonStyle(NotchActionStyle(accent: accent))
    }
}

private struct NotchActionStyle: ButtonStyle {
    let accent: Color
    func makeBody(configuration: Configuration) -> some View {
        NotchActionAppearance(label: configuration.label, accent: accent, pressed: configuration.isPressed)
    }
}

private struct NotchActionAppearance<Label: View>: View {
    let label: Label
    let accent: Color
    let pressed: Bool
    @State private var hovered = false
    @Environment(\.isEnabled) private var enabled
    var body: some View {
        label.foregroundStyle(.white.opacity(enabled ? 0.9 : 0.35))
            .background(accent.opacity(enabled && (hovered || pressed) ? 0.22 : 0.10),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10)
                .strokeBorder(accent.opacity(enabled && hovered ? 0.65 : 0.24)).allowsHitTesting(false))
            .scaleEffect(pressed ? 0.96 : 1)
            .onHover { hovered = $0 }
    }
}
