import AppKit
import Foundation

private final class QualificationApplicationDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private var statusLabel: NSTextField?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let panel = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 210),
            styleMask: [.titled],
            backing: .buffered,
            defer: false
        )
        panel.title = "Jarvis P8 Qualification"
        panel.isReleasedWhenClosed = false
        panel.isRestorable = false
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary]
        panel.standardWindowButton(.closeButton)?.isHidden = true
        panel.standardWindowButton(.miniaturizeButton)?.isHidden = true
        panel.standardWindowButton(.zoomButton)?.isHidden = true

        let content = NSView(frame: panel.contentView?.bounds ?? .zero)
        content.autoresizingMask = [.width, .height]

        let heading = NSTextField(labelWithString: "Jarvis Background Driver")
        heading.frame = NSRect(x: 30, y: 150, width: 360, height: 24)
        heading.font = .systemFont(ofSize: 17, weight: .semibold)
        heading.setAccessibilityLabel("P8 Driver Heading")

        let input = NSSearchField(frame: NSRect(x: 30, y: 105, width: 360, height: 28))
        input.placeholderString = "P8 Input"
        input.identifier = NSUserInterfaceItemIdentifier("p8.input")
        input.setAccessibilityLabel("P8 Input")
        input.setAccessibilityHelp("Bounded qualification text field")

        let button = NSButton(
            title: "P8 Advance",
            target: self,
            action: #selector(advance(_:))
        )
        button.frame = NSRect(x: 30, y: 55, width: 150, height: 32)
        button.bezelStyle = .rounded
        button.identifier = NSUserInterfaceItemIdentifier("p8.advance")
        button.setAccessibilityLabel("P8 Advance")
        button.setAccessibilityHelp("Advance the bounded qualification state")

        let status = NSTextField(labelWithString: "P8 Ready")
        status.frame = NSRect(x: 205, y: 60, width: 185, height: 22)
        status.identifier = NSUserInterfaceItemIdentifier("p8.status")
        status.setAccessibilityLabel("P8 Status")
        status.setAccessibilityValue("P8 Ready")

        content.addSubview(heading)
        content.addSubview(input)
        content.addSubview(button)
        content.addSubview(status)
        panel.contentView = content
        panel.center()
        panel.orderFrontRegardless()
        panel.makeKey()
        window = panel
        statusLabel = status
    }

    @MainActor @objc
    private func advance(_ sender: NSButton) {
        sender.title = "P8 Advance Complete"
        sender.setAccessibilityLabel("P8 Advance Complete")
        statusLabel?.stringValue = "P8 Complete"
        statusLabel?.setAccessibilityValue("P8 Complete")
        if let statusLabel {
            NSAccessibility.post(element: statusLabel, notification: .valueChanged)
        }
    }
}

@main
private enum JarvisUIQualificationFixture {
    static func main() {
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        let delegate = QualificationApplicationDelegate()
        application.delegate = delegate
        application.run()
        _ = delegate
    }
}
