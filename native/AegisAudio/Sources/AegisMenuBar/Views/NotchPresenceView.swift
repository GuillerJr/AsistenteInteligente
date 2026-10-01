import AegisAudioCore
import SwiftUI

/// Only the expanded content lives in a panel. No window covers the menu bar.
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
        let outline = NotchOutline(topInset: 12, bottomRadius: 22)
        NotchExpandedContent(model: model, mood: .init(kind: model.presentationStatus.kind),
                             pinned: presentation.interaction.isPinned, interactive: interactive,
                             reduceMotion: systemReduceMotion || forceReduceMotion,
                             togglePinned: togglePinned, dismiss: dismiss, openWindow: openWindow)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(.black, in: outline)
            .clipShape(outline)
            .contentShape(outline)
            .opacity(presentation.interaction.isExpanded ? 1 : 0)
            .scaleEffect(presentation.interaction.isExpanded ? 1 : 0.97, anchor: .top)
            .clipped()
            .ignoresSafeArea()
            .onExitCommand(perform: dismiss)
    }
}
