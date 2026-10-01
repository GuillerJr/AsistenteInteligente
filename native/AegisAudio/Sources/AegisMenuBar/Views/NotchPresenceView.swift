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
    @Environment(\.colorSchemeContrast) private var contrast

    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }
    private var status: AssistantPresentation { model.presentationStatus }
    private var interaction: NotchInteractionState { presentation.interaction }
    private var accent: Color { status.tone.color }

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
            VStack(spacing: 0) {
                Button(action: togglePinned) {
                    HStack(spacing: 0) {
                        JarvisNotchDot(color: accent, mood: mood, energy: Double(model.voiceActivityLevel),
                                       engaged: interaction.isHovered || interaction.isPinned,
                                       forceReduceMotion: reduceMotion)
                            .frame(width: 25, height: 25)
                            .frame(width: wingWidth, height: presentation.notchHeight)
                        // The camera remains free; all interactive content is in visible pixels.
                        Color.black.frame(width: presentation.notchWidth, height: presentation.notchHeight)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 4) {
                                Text("JARVIS").tracking(0.7)
                                Image(systemName: interaction.isPinned ? "pin.fill" : "chevron.down")
                                    .font(.system(size: 6, weight: .bold))
                            }
                            .font(.system(size: 8, weight: .bold, design: .rounded))
                            .foregroundStyle(.white.opacity(0.94))
                            Text(status.compactTitle.uppercased())
                                .font(.system(size: 8.5, weight: .semibold, design: .rounded))
                                .foregroundStyle(accent).lineLimit(1).minimumScaleFactor(0.65)
                        }
                        .padding(.horizontal, 5)
                        .frame(width: wingWidth, height: presentation.notchHeight, alignment: .leading)
                        .clipped()
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
            .background(.black)
            .clipShape(UnevenRoundedRectangle(
                bottomLeadingRadius: interaction.isExpanded ? 22 : 10,
                bottomTrailingRadius: interaction.isExpanded ? 22 : 10, style: .continuous
            ))
            .overlay(alignment: .bottom) {
                if interaction.isExpanded {
                    UnevenRoundedRectangle(bottomLeadingRadius: 22, bottomTrailingRadius: 22)
                        .strokeBorder(accent.opacity(contrast == .increased ? 0.75 : 0.18), lineWidth: 1)
                        .allowsHitTesting(false)
                }
            }
            .clipped()
        }
        .ignoresSafeArea()
        .onExitCommand(perform: dismiss)
    }
}
