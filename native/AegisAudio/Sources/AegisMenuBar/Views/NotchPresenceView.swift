import AegisAudioCore
import SwiftUI

/// Both sizes live below the camera and menu bar, with no additional status item.
struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var interactive = true
    var togglePinned: () -> Void = {}
    var dismiss: () -> Void = {}
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    var body: some View {
        let expanded = presentation.interaction.isExpanded
        let outline = NotchOutline(topInset: expanded ? 12 : 0, bottomRadius: expanded ? 22 : 12)
        VStack(spacing: expanded ? 8 : 0) {
            Button(action: togglePinned) {
                JarvisNotchMascot(mood: .init(kind: model.presentationStatus.kind),
                                 energy: Double(model.voiceActivityLevel),
                                 engaged: presentation.interaction.isHovered || presentation.interaction.isPinned,
                                 forceReduceMotion: systemReduceMotion || forceReduceMotion)
                    .frame(width: expanded ? 112 : 36, height: expanded ? 112 : 36)
                    .frame(maxWidth: .infinity)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain).disabled(!interactive)
            .accessibilityLabel("Mascota de Jarvis")
            .accessibilityValue(model.presentationStatus.accessibilityDescription)
            .help(presentation.interaction.isPinned
                  ? "Pulsa para soltar el notch; Escape lo recoge"
                  : "Acerca el cursor para desplegar; pulsa la mascota para fijarla")
            if expanded {
                Text(model.presentationStatus.compactTitle)
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(model.presentationStatus.tone.color)
                    .lineLimit(1)
                    .transition(.opacity)
            }
        }
            .padding(.top, expanded ? 8 : 2)
            .padding(.bottom, expanded ? 12 : 2)
            .padding(.horizontal, expanded ? 12 : 0)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(.black, in: outline)
            .clipShape(outline)
            .contentShape(outline)
            .clipped()
            .ignoresSafeArea()
            .onExitCommand(perform: dismiss)
    }
}
