import AegisAudioCore
import SwiftUI

/// Conversation lives below the camera; hovering never starts a request.
struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var interactive = true
    var togglePinned: () -> Void = {}
    var keepOpen: () -> Void = {}
    var activateVoice: () -> Void = {}
    var reviewInteraction: () -> Void = {}
    var dismiss: () -> Void = {}
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    @State private var dropTargeted = false

    var body: some View {
        let progress = CGFloat(presentation.expansionProgress)
        let mascotSize = 36 + 36 * progress
        let top = 2 + 6 * progress
        let outline = NotchOutline(topInset: 12 * progress, bottomRadius: 12 + 10 * progress)
        let reveal = min(max((presentation.expansionProgress - 0.75) / 0.20, 0), 1)
        let opacity = reveal * reveal * (3 - 2 * reveal)
        GeometryReader { geometry in
            ZStack(alignment: .top) {
                Button(action: togglePinned) {
                    NotchMascotArtwork(model: model,
                                       engaged: presentation.interaction.isHovered || presentation.interaction.isPinned,
                                       reduceMotion: systemReduceMotion || forceReduceMotion)
                        .frame(width: 112, height: 112)
                        .scaleEffect(mascotSize / 112)
                        .frame(width: mascotSize, height: mascotSize)
                        .frame(maxWidth: .infinity)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!interactive)
                .accessibilityLabel("Mascota de Jarvis")
                .accessibilityValue(model.presentationStatus.accessibilityDescription)
                .accessibilityAction(named: Text("Hablar con Jarvis"), activateVoice)
                .help("Pulsa para fijar o soltar. Doble clic para hablar o interrumpir una respuesta hablada.")
                .position(x: geometry.size.width / 2, y: top + mascotSize / 2)

                Text(model.presentationStatus.compactTitle)
                    .font(.system(size: 11, weight: .medium, design: .rounded))
                    .foregroundStyle(model.presentationStatus.tone.color)
                    .lineLimit(1)
                    .frame(width: max(0, geometry.size.width - 32), height: 18)
                    .position(x: geometry.size.width / 2, y: top + mascotSize + 12)
                    .opacity(opacity).allowsHitTesting(false)
                    .accessibilityHidden(opacity < 0.95)

                NotchConversationView(model: model, keepOpen: keepOpen,
                                      activateVoice: activateVoice, reviewInteraction: reviewInteraction)
                    .frame(width: max(0, geometry.size.width - 32),
                           height: max(0, geometry.size.height - 120), alignment: .top)
                    .position(x: geometry.size.width / 2, y: 108 + max(0, geometry.size.height - 120) / 2)
                    .opacity(opacity)
                    .disabled(!interactive)
                    .allowsHitTesting(interactive && opacity > 0.95)
                    .accessibilityHidden(opacity < 0.95)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(.black, in: outline)
        .overlay { if dropTargeted { outline.stroke(.cyan.opacity(0.7), lineWidth: 2) } }
        .clipShape(outline).contentShape(outline).clipped().ignoresSafeArea()
        .preferredColorScheme(.dark)
        .onExitCommand(perform: dismiss)
        .onChange(of: model.mascotSessionAvailable) { _, available in
            if !available { dismiss() }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard interactive, model.canManageChatHistory, !urls.isEmpty else { return false }
            keepOpen()
            Task { await model.addMascotFiles(urls) }
            return true
        } isTargeted: { targeted in
            dropTargeted = targeted
            if targeted && interactive && model.canManageChatHistory { keepOpen() }
        }
    }

}

/// Audio metering redraws the artwork, not the Markdown conversation or input.
private struct NotchMascotArtwork: View {
    let model: MenuBarModel
    let engaged: Bool
    let reduceMotion: Bool
    var body: some View {
        JarvisNotchMascot(mood: model.mascotNotice != nil && !model.voiceState.isBusy
                         && model.voiceState != .speaking ? .attention : .init(kind: model.presentationStatus.kind),
                         energy: Double(model.voiceActivityLevel), engaged: engaged,
                         forceReduceMotion: reduceMotion)
    }
}
