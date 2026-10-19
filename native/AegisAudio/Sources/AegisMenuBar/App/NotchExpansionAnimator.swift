import AegisAudioCore
import AppKit
import QuartzCore

/// The display clock exists only while the notch is changing size. Both the
/// native frame and SwiftUI geometry receive the same progress on each tick.
@MainActor
final class NotchExpansionAnimator: NSObject {
    private var motion = NotchExpansionMotion()
    private var displayLink: CADisplayLink?
    private var lastTimestamp: CFTimeInterval = 0
    private var update: ((Double) -> Void)?

    func move(expanded: Bool, in view: NSView, animated: Bool,
              update: @escaping (Double) -> Void) {
        self.update = update
        motion.retarget(expanded: expanded)
        guard animated, motion.isMoving else {
            finish(expanded: expanded)
            return
        }
        guard displayLink == nil else { return }
        lastTimestamp = CACurrentMediaTime()
        let link = view.displayLink(target: self, selector: #selector(tick(_:)))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
        displayLink = link
        link.add(to: .main, forMode: .common)
    }

    func finish(expanded: Bool) {
        stopClock()
        motion.finish(expanded: expanded)
        update?(motion.progress)
        update = nil
    }

    func cancel() {
        stopClock()
        update = nil
        motion = NotchExpansionMotion()
    }

    @objc private func tick(_ link: CADisplayLink) {
        let now = CACurrentMediaTime()
        motion.advance(by: now - lastTimestamp)
        lastTimestamp = now
        update?(motion.progress)
        if !motion.isMoving {
            stopClock()
            update = nil
        }
    }

    private func stopClock() {
        displayLink?.invalidate()
        displayLink = nil
    }
}
