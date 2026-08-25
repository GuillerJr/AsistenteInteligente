import AegisAudioCore
import AppKit
import CoreGraphics
import QuartzCore

@MainActor
final class JarvisPointerController {
    static let shared = JarvisPointerController()

    private let pointerSize = CGSize(width: 42, height: 42)
    private var panel: JarvisPointerPanel?
    private var pointerView: JarvisPointerView?
    private var currentCenter: CGPoint?
    private var feedbackTask: Task<Void, Never>?
    private var hideTask: Task<Void, Never>?

    private init() {}

    func present(_ event: ComputerPointerEvent, success: Bool) {
        guard let screen = mainDisplayScreen() else { return }
        let panel = pointerPanel()
        let center = CGPoint(
            x: screen.frame.minX
                + screen.frame.width * CGFloat(event.normalizedX) / 1_000,
            y: screen.frame.maxY
                - screen.frame.height * CGFloat(event.normalizedY) / 1_000
        )
        let clampedCenter = CGPoint(
            x: min(
                max(center.x, screen.frame.minX + pointerSize.width / 2),
                screen.frame.maxX - pointerSize.width / 2
            ),
            y: min(
                max(center.y, screen.frame.minY + pointerSize.height / 2),
                screen.frame.maxY - pointerSize.height / 2
            )
        )
        let origin = CGPoint(
            x: clampedCenter.x - pointerSize.width / 2,
            y: clampedCenter.y - pointerSize.height / 2
        )

        feedbackTask?.cancel()
        hideTask?.cancel()
        pointerView?.setOutcome(nil)
        let movesFromPreviousTarget = currentCenter != nil && panel.isVisible
        if !movesFromPreviousTarget {
            panel.setFrameOrigin(origin)
            panel.alphaValue = 0
            panel.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.12
                panel.animator().alphaValue = 1
            }
        } else {
            panel.orderFrontRegardless()
            NSAnimationContext.runAnimationGroup { context in
                context.duration = 0.22
                context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                panel.animator().setFrameOrigin(origin)
            }
        }
        currentCenter = clampedCenter
        if movesFromPreviousTarget {
            feedbackTask = Task { @MainActor [weak self] in
                do {
                    try await Task.sleep(for: .milliseconds(225))
                } catch {
                    return
                }
                self?.showFeedback(success: success)
                self?.feedbackTask = nil
            }
        } else {
            showFeedback(success: success)
        }
    }

    func hide() {
        feedbackTask?.cancel()
        feedbackTask = nil
        hideTask?.cancel()
        hideTask = nil
        guard let panel, panel.isVisible else { return }
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.14
            panel.animator().alphaValue = 0
        }
        hideTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(150))
            } catch {
                return
            }
            self?.panel?.orderOut(nil)
            self?.hideTask = nil
        }
    }

    private func pointerPanel() -> JarvisPointerPanel {
        if let panel { return panel }
        let panel = JarvisPointerPanel(
            contentRect: CGRect(origin: .zero, size: pointerSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        let pointerView = JarvisPointerView(frame: CGRect(origin: .zero, size: pointerSize))
        panel.contentView = pointerView
        panel.backgroundColor = .clear
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.hasShadow = false
        panel.hidesOnDeactivate = false
        panel.ignoresMouseEvents = true
        panel.isMovable = false
        panel.isOpaque = false
        panel.isReleasedWhenClosed = false
        panel.level = .statusBar
        self.panel = panel
        self.pointerView = pointerView
        return panel
    }

    private func mainDisplayScreen() -> NSScreen? {
        NSScreen.screens.first { screen in
            let key = NSDeviceDescriptionKey("NSScreenNumber")
            return (screen.deviceDescription[key] as? NSNumber)?.uint32Value
                == CGMainDisplayID()
        } ?? NSScreen.main
    }

    private func scheduleHide(after duration: Duration) {
        hideTask?.cancel()
        hideTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: duration)
            } catch {
                return
            }
            self?.hide()
        }
    }

    private func showFeedback(success: Bool) {
        pointerView?.setOutcome(success)
        pointerView?.pulse()
        scheduleHide(after: success ? .seconds(2) : .milliseconds(700))
    }
}

private final class JarvisPointerPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

private final class JarvisPointerView: NSView {
    private let cyan = NSColor(calibratedRed: 0.15, green: 0.86, blue: 1, alpha: 1)
    private let ring = CAShapeLayer()
    private let center = CAShapeLayer()
    private let pulseRing = CAShapeLayer()
    private var nodeLayers: [CAShapeLayer] = []

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        configureLayers()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        return nil
    }

    func setOutcome(_ success: Bool?) {
        let color = switch success {
        case true: NSColor.systemGreen
        case false: NSColor.systemRed
        case nil: cyan
        }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        ring.strokeColor = color.cgColor
        center.fillColor = color.cgColor
        pulseRing.strokeColor = color.cgColor
        nodeLayers.forEach { $0.fillColor = color.cgColor }
        CATransaction.commit()
    }

    func pulse() {
        pulseRing.removeAllAnimations()
        let scale = CABasicAnimation(keyPath: "transform.scale")
        scale.fromValue = 0.7
        scale.toValue = 1.35
        let opacity = CABasicAnimation(keyPath: "opacity")
        opacity.fromValue = 0.9
        opacity.toValue = 0
        let group = CAAnimationGroup()
        group.animations = [scale, opacity]
        group.duration = 0.34
        group.timingFunction = CAMediaTimingFunction(name: .easeOut)
        pulseRing.add(group, forKey: "jarvis-click")

        let centerPulse = CABasicAnimation(keyPath: "transform.scale")
        centerPulse.fromValue = 1.8
        centerPulse.toValue = 1
        centerPulse.duration = 0.22
        centerPulse.timingFunction = CAMediaTimingFunction(name: .easeOut)
        center.add(centerPulse, forKey: "jarvis-center")
    }

    private func configureLayers() {
        wantsLayer = true
        layer = CALayer()
        layer?.masksToBounds = false

        let outerRect = bounds.insetBy(dx: 7, dy: 7)
        ring.frame = bounds
        ring.path = CGPath(ellipseIn: outerRect, transform: nil)
        ring.fillColor = NSColor.clear.cgColor
        ring.strokeColor = cyan.cgColor
        ring.lineDashPattern = [5, 3]
        ring.lineWidth = 1.5
        ring.shadowColor = cyan.cgColor
        ring.shadowOpacity = 0.75
        ring.shadowRadius = 5
        ring.shadowOffset = .zero
        layer?.addSublayer(ring)

        pulseRing.frame = bounds
        pulseRing.path = CGPath(ellipseIn: outerRect, transform: nil)
        pulseRing.fillColor = NSColor.clear.cgColor
        pulseRing.strokeColor = cyan.cgColor
        pulseRing.lineWidth = 1.25
        layer?.addSublayer(pulseRing)

        center.frame = bounds
        center.path = CGPath(
            ellipseIn: CGRect(x: bounds.midX - 2.5, y: bounds.midY - 2.5, width: 5, height: 5),
            transform: nil
        )
        center.fillColor = cyan.cgColor
        center.shadowColor = cyan.cgColor
        center.shadowOpacity = 0.9
        center.shadowRadius = 4
        center.shadowOffset = .zero
        layer?.addSublayer(center)

        let nodeCenters = [
            CGPoint(x: bounds.midX, y: bounds.maxY - 4),
            CGPoint(x: bounds.maxX - 4, y: bounds.midY),
            CGPoint(x: 7, y: 7),
        ]
        nodeLayers = nodeCenters.map { point in
            let node = CAShapeLayer()
            node.frame = bounds
            node.path = CGPath(
                ellipseIn: CGRect(x: point.x - 1.5, y: point.y - 1.5, width: 3, height: 3),
                transform: nil
            )
            node.fillColor = cyan.cgColor
            layer?.addSublayer(node)
            return node
        }

        let rotation = CABasicAnimation(keyPath: "transform.rotation.z")
        rotation.fromValue = 0
        rotation.toValue = Double.pi * 2
        rotation.duration = 2.4
        rotation.repeatCount = .infinity
        rotation.timingFunction = CAMediaTimingFunction(name: .linear)
        ring.add(rotation, forKey: "jarvis-orbit")
    }
}
