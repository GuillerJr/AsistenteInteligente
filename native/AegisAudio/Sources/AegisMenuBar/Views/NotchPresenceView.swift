import AegisAudioCore
import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var interactive = true
    var togglePinned: () -> Void = {}
    var dismiss: () -> Void = {}
    var openWindow: (String) -> Void = { _ in }
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }
    private var status: AssistantPresentation { model.presentationStatus }
    private var interaction: NotchInteractionState { presentation.interaction }
    private var accent: Color { status.tone.color }

    private var compactSymbol: String {
        if interaction.isPinned { return "pin.fill" }
        switch mood {
        case .listening: return "waveform"
        case .thinking: return "ellipsis"
        case .speaking: return "speaker.wave.2.fill"
        case .attention: return "exclamationmark"
        case .resting: return "chevron.down"
        }
    }

    private var mood: JarvisNotchDotMood {
        switch status.kind {
        case .listening, .followingUp: .listening
        case .processing, .submitting, .working, .connecting, .securityChecking, .brainChecking: .thinking
        case .speaking: .speaking
        case .approval, .browserSelection, .failure, .securityBlocked, .offline,
             .securityUnavailable, .brainMissing, .brainUnavailable: .attention
        default: .resting
        }
    }

    var body: some View {
        GeometryReader { geometry in
            let wingWidth = max(0, (geometry.size.width - presentation.notchWidth) / 2)
            let outline = NotchOutline(topInset: interaction.isExpanded ? 12 : 8,
                                       bottomRadius: interaction.isExpanded ? 22 : 12)
            VStack(spacing: 0) {
                Button(action: togglePinned) {
                    HStack(spacing: 0) {
                        JarvisNotchDot(color: accent, mood: mood, energy: Double(model.voiceActivityLevel),
                                       engaged: interaction.isHovered || interaction.isPinned,
                                       forceReduceMotion: reduceMotion)
                            .frame(width: 25, height: 25)
                            .padding(.leading, 8)
                            .frame(width: wingWidth, height: presentation.notchHeight)
                        // The camera remains free; all interactive content is in visible pixels.
                        Color.black.frame(width: presentation.notchWidth, height: presentation.notchHeight)
                        Image(systemName: compactSymbol)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(accent)
                            .padding(.trailing, 8)
                            .frame(width: wingWidth, height: presentation.notchHeight)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!interactive)
                .help(interaction.isPinned ? "Dejar de fijar el notch" : "Expandir y fijar Jarvis")
                .accessibilityLabel("Notch de Jarvis")
                .accessibilityValue(status.accessibilityDescription)
                .accessibilityHint("Pulsa para expandir y mantener abierto. Escape o clic fuera lo contrae.")

                if interaction.isExpanded {
                    NotchExpandedContent(model: model, mood: mood, pinned: interaction.isPinned,
                                         interactive: interactive, reduceMotion: reduceMotion,
                                         togglePinned: togglePinned, dismiss: dismiss, openWindow: openWindow)
                        .transition(.opacity.combined(with: .offset(y: -6)))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(.black, in: outline)
            .clipShape(outline)
            .contentShape(outline)
            .clipped()
        }
        .ignoresSafeArea()
        .onExitCommand(perform: dismiss)
    }
}
