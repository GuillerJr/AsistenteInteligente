import AegisAudioCore
import Foundation
import Testing

@Suite("Interactive camera notch")
struct NotchInteractionTests {
    @Test func returningPointerProtectsAgainstDelayedCollapse() {
        var state = NotchInteractionState()
        state.setHovered(true)
        state.setHovered(false)
        state.setHovered(true)
        state.collapseIfUnhovered()
        #expect(state.isExpanded)
        state.setHovered(false)
        state.collapseIfUnhovered()
        #expect(!state.isExpanded)
    }

    @Test func pinSurvivesPointerExitAndDismissResetsIt() {
        var state = NotchInteractionState()
        state.togglePinned()
        state.setHovered(false)
        state.collapseIfUnhovered()
        #expect(state.isPinned && state.isExpanded)
        state.dismiss()
        #expect(state == NotchInteractionState())
    }

    @Test func unpinOutsideClosesButUnpinInsideStaysOpen() {
        var state = NotchInteractionState()
        state.setHovered(true)
        state.togglePinned()
        state.togglePinned()
        #expect(state.isExpanded && !state.isPinned)
        state.togglePinned()
        state.setHovered(false)
        state.togglePinned()
        #expect(!state.isExpanded)
    }

    @Test(arguments: [CGRect(x: 0, y: 0, width: 1470, height: 956),
                      CGRect(x: -1512, y: 900, width: 1512, height: 982)])
    func expansionKeepsCameraAlignedOnEachScreen(screen: CGRect) throws {
        let notch = try #require(AssistantPanelLayout.notch(
            screen: screen,
            left: CGRect(x: screen.minX, y: screen.maxY - 32, width: (screen.width - 179) / 2, height: 32),
            right: CGRect(x: screen.midX + 89.5, y: screen.maxY - 32,
                          width: (screen.width - 179) / 2, height: 32), safeTop: 32, scale: 2))
        let expanded = AssistantPanelLayout.expandedNotchFrame(notch, screen: screen, scale: 2)
        #expect(expanded.midX == notch.frame.midX)
        #expect(expanded.maxY == notch.frame.maxY)
        #expect(expanded.size == CGSize(width: 380, height: 208))
        #expect(screen.contains(expanded))
    }
}
