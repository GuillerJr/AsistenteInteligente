import AegisAudioCore
import SwiftUI

/// Both sizes live below the camera and menu bar, with no additional status item.
struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var interactive = true
    var togglePinned: () -> Void = {}
    var dismiss: () -> Void = {}
    var openWindow: (String) -> Void = { _ in }
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    var body: some View {
        let expanded = presentation.interaction.isExpanded
        let outline = NotchOutline(topInset: expanded ? 12 : 0, bottomRadius: expanded ? 22 : 12)
        Group {
            if expanded {
                NotchExpandedContent(model: model, mood: .init(kind: model.presentationStatus.kind),
                                     pinned: presentation.interaction.isPinned, interactive: interactive,
                                     reduceMotion: systemReduceMotion || forceReduceMotion,
                                     togglePinned: togglePinned, dismiss: dismiss, openWindow: openWindow)
                    .transition(.opacity)
            } else {
                Button(action: togglePinned) {
                    JarvisNotchDot(color: model.presentationStatus.tone.color,
                                   mood: .init(kind: model.presentationStatus.kind),
                                   energy: Double(model.voiceActivityLevel),
                                   engaged: presentation.interaction.isHovered,
                                   forceReduceMotion: systemReduceMotion || forceReduceMotion)
                        .frame(width: 22, height: 22)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!interactive)
                .accessibilityLabel("Notch de Jarvis")
                .accessibilityValue(model.presentationStatus.accessibilityDescription)
                .help("Acerca el cursor o pulsa para abrir el panel de Jarvis")
                .transition(.opacity)
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
