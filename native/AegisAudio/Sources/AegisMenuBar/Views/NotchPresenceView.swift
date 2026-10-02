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
        let progress = CGFloat(presentation.expansionProgress)
        let mascotSize = 36 + 76 * progress
        let top = 2 + 6 * progress
        let outline = NotchOutline(topInset: 12 * progress, bottomRadius: 12 + 10 * progress)
        let reveal = min(max((presentation.expansionProgress - 0.75) / 0.20, 0), 1)
        let opacity = reveal * reveal * (3 - 2 * reveal)
        GeometryReader { geometry in
            ZStack {
                Button(action: togglePinned) {
                    JarvisNotchMascot(mood: .init(kind: model.presentationStatus.kind),
                                     energy: Double(model.voiceActivityLevel),
                                     engaged: presentation.interaction.isHovered || presentation.interaction.isPinned,
                                     forceReduceMotion: systemReduceMotion || forceReduceMotion)
                        // Keep Canvas resolution fixed and scale its composited
                        // artwork; expanding must not rebuild the texture.
                        .frame(width: 112, height: 112)
                        .scaleEffect(mascotSize / 112)
                        .frame(width: mascotSize, height: mascotSize)
                        .frame(maxWidth: .infinity)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!interactive)
                .accessibilityLabel("Mascota de Jarvis")
                .accessibilityValue(model.presentationStatus.accessibilityDescription)
                .help(presentation.interaction.isPinned
                      ? "Pulsa para soltar el notch; Escape lo recoge"
                      : "Acerca el cursor para desplegar; pulsa la mascota para fijarla")
                .position(x: geometry.size.width / 2, y: top + mascotSize / 2)

                // A stable label avoids insertion/removal changing layout in
                // the middle of resizing. It appears after there is room.
                Text(model.presentationStatus.compactTitle)
                    .font(.system(size: 12, weight: .medium, design: .rounded))
                    .foregroundStyle(model.presentationStatus.tone.color)
                    .lineLimit(1)
                    .frame(width: max(0, geometry.size.width - 24), height: 18)
                    .opacity(opacity)
                    .position(x: geometry.size.width / 2,
                              y: top + mascotSize + 17 + 4 * (1 - CGFloat(opacity)))
                    .allowsHitTesting(false)
                    .accessibilityHidden(opacity < 0.95)
            }
        }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(.black, in: outline)
            .clipShape(outline)
            .contentShape(outline)
            .clipped()
            .ignoresSafeArea()
            .onExitCommand(perform: dismiss)
    }
}
