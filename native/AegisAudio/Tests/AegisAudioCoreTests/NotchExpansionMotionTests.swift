import AegisAudioCore
import Foundation
import Testing

@Suite("Continuous notch expansion")
struct NotchExpansionMotionTests {
    @Test(arguments: [30.0, 60, 120])
    func openingAndClosingSettleWithoutOvershoot(frameRate: Double) {
        var motion = NotchExpansionMotion()
        motion.retarget(expanded: true)
        var previous = motion.progress
        for _ in 0..<Int(frameRate) {
            motion.advance(by: 1 / frameRate)
            #expect(motion.progress >= previous && motion.progress <= 1)
            previous = motion.progress
        }
        #expect(motion.progress == 1 && !motion.isMoving)
        motion.retarget(expanded: false)
        for _ in 0..<Int(frameRate) {
            motion.advance(by: 1 / frameRate)
            #expect(motion.progress <= previous && motion.progress >= 0)
            previous = motion.progress
        }
        #expect(motion.progress == 0 && !motion.isMoving)
    }

    @Test func motionDoesNotDependOnFrameSubdivision() {
        var whole = NotchExpansionMotion()
        whole.retarget(expanded: true)
        var subdivided = whole
        whole.advance(by: 0.16)
        for _ in 0..<20 { subdivided.advance(by: 0.008) }
        #expect(abs(whole.progress - subdivided.progress) < 0.000_001)
        #expect(abs(whole.velocity - subdivided.velocity) < 0.000_001)
    }

    @Test func reversalPreservesMomentumInsteadOfJumping() {
        var motion = NotchExpansionMotion()
        motion.retarget(expanded: true)
        motion.advance(by: 0.08)
        let before = motion
        motion.retarget(expanded: false)
        #expect(motion.progress == before.progress)
        #expect(motion.velocity == before.velocity)
        motion.advance(by: 1 / 240)
        #expect(motion.progress > before.progress)
        #expect(motion.velocity > 0 && motion.velocity < before.velocity)
        motion.advance(by: 1)
        #expect(motion.progress == 0 && !motion.isMoving)
    }

    @Test func repeatedReversalsKeepEveryFrameBelowTheMenuBar() {
        let compact = CGRect(x: -824.5, y: 984, width: 179, height: 40)
        let expanded = CGRect(x: -845, y: 864, width: 220, height: 160)
        var motion = NotchExpansionMotion()
        for frame in 0..<240 {
            if frame.isMultiple(of: 7) { motion.retarget(expanded: frame.isMultiple(of: 2)) }
            motion.advance(by: 1 / 120)
            let rendered = AssistantPanelLayout.interpolatedNotchFrame(
                compact: compact, expanded: expanded, progress: motion.progress)
            #expect(motion.progress >= 0 && motion.progress <= 1)
            #expect(rendered.maxY == compact.maxY)
            #expect(rendered.midX == compact.midX)
            #expect(expanded.contains(rendered))
        }
        motion.retarget(expanded: false)
        motion.advance(by: 1)
        #expect(!motion.isMoving)
    }

    @Test func reducedMotionFinishesAndStopsTheClock() {
        var motion = NotchExpansionMotion()
        motion.retarget(expanded: true)
        motion.advance(by: 0.1)
        motion.finish(expanded: true)
        #expect(motion.progress == 1 && motion.velocity == 0 && !motion.isMoving)
        motion.finish(expanded: false)
        #expect(motion.progress == 0 && motion.velocity == 0 && !motion.isMoving)
    }

    @Test(arguments: [Double.nan, .infinity, -1, 0])
    func invalidClockSamplesCannotCorruptTheTransition(seconds: Double) {
        var motion = NotchExpansionMotion()
        motion.retarget(expanded: true)
        motion.advance(by: 0.1)
        let before = motion
        motion.advance(by: seconds)
        #expect(motion.progress == before.progress && motion.velocity == before.velocity)
    }
}
