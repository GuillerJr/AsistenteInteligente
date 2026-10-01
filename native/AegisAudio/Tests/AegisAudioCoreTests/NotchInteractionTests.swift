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
        #expect(expanded.maxY == notch.frame.minY)
        #expect(expanded.size == CGSize(width: 380, height: 176))
        #expect(screen.contains(expanded))
    }

    @Test(arguments: [CGFloat(32), 38, 38.25, 48])
    func neitherPanelSizeCoversMenuBar(menuBarHeight: CGFloat) throws {
        let screen = CGRect(x: -1470, y: 100, width: 1470, height: 956)
        let notch = try #require(AssistantPanelLayout.notch(
            screen: screen, left: CGRect(x: -1470, y: 1024, width: 645.5, height: 32),
            right: CGRect(x: -645.5, y: 1024, width: 645.5, height: 32), safeTop: 32, scale: 2))
        let compact = AssistantPanelLayout.compactNotchFrame(
            notch, screen: screen, scale: 2, menuBarHeight: menuBarHeight)
        let expanded = AssistantPanelLayout.expandedNotchFrame(
            notch, screen: screen, scale: 2, menuBarHeight: menuBarHeight)
        let menuBar = CGRect(x: screen.minX, y: screen.maxY - menuBarHeight,
                             width: screen.width, height: menuBarHeight)
        #expect(compact.midX == notch.frame.midX)
        #expect(compact.size == CGSize(width: notch.width, height: 24))
        for panel in [compact, expanded] {
            #expect(panel.maxY <= menuBar.minY)
            #expect(panel.intersection(menuBar).height <= 0)
            #expect(screen.contains(panel))
        }
    }
}
