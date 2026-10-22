import AppKit
import SwiftUI

/// Native popup tracking, keyboard navigation and accessibility with Jarvis chrome.
struct JarvisMenuPicker<Value: Hashable>: NSViewRepresentable {
    let title: String
    @Binding var selection: Value
    let options: [JarvisSelectionOption<Value>]
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> JarvisPopupButton {
        let button = JarvisPopupButton(frame: .zero, pullsDown: false)
        button.cell = JarvisPopupCell(textCell: "", pullsDown: false)
        button.isBordered = false
        button.focusRingType = .exterior
        button.target = context.coordinator
        button.action = #selector(Coordinator.changed(_:))
        button.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return button
    }

    static func dismantleNSView(_ button: JarvisPopupButton, coordinator: Coordinator) {
        button.menu?.cancelTrackingWithoutAnimation()
        button.target = nil
    }

    func updateNSView(_ button: JarvisPopupButton, context: Context) {
        context.coordinator.parent = self
        let titles = options.isEmpty ? [String(localized: "Seleccionar…", bundle: .module)] : options.map(\.title)
        if button.itemTitles != titles {
            button.removeAllItems()
            button.addItems(withTitles: titles)
        }
        if let index = options.firstIndex(where: { $0.value == selection }) {
            button.selectItem(at: index)
        } else { button.selectItem(at: -1) }
        button.isEnabled = enabled && !options.isEmpty
        let appearance: NSAppearance.Name = contrast == .increased
            ? colorScheme == .dark ? .accessibilityHighContrastDarkAqua : .accessibilityHighContrastAqua
            : colorScheme == .dark ? .darkAqua : .aqua
        button.appearance = NSAppearance(named: appearance)
        button.setAccessibilityLabel(title)
        // AppKit exposes the popup cell as the actual actionable AX element.
        button.cell?.setAccessibilityLabel(title)
        button.toolTip = button.title
        (button.cell as? JarvisPopupCell)?.increasedContrast = contrast == .increased
        button.needsDisplay = true
    }

    func sizeThatFits(_ proposal: ProposedViewSize, nsView: JarvisPopupButton, context: Context) -> CGSize? {
        CGSize(width: max(140, proposal.width ?? 240), height: 36)
    }

    @MainActor final class Coordinator: NSObject {
        var parent: JarvisMenuPicker
        init(_ parent: JarvisMenuPicker) { self.parent = parent }
        @objc func changed(_ sender: NSPopUpButton) {
            let index = sender.indexOfSelectedItem
            guard parent.options.indices.contains(index) else { return }
            let value = parent.options[index].value
            if parent.selection != value { parent.selection = value }
        }
    }
}

final class JarvisPopupButton: NSPopUpButton {
    private static let buttons = NSHashTable<JarvisPopupButton>.weakObjects()
    private var hoverArea: NSTrackingArea?

    override init(frame: NSRect, pullsDown: Bool) {
        super.init(frame: frame, pullsDown: pullsDown)
        Self.buttons.add(self)
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        Self.buttons.add(self)
    }

    static func dismissMenus() {
        for button in buttons.allObjects { button.menu?.cancelTrackingWithoutAnimation() }
    }

    override func viewWillMove(toWindow newWindow: NSWindow?) {
        if newWindow == nil { menu?.cancelTrackingWithoutAnimation() }
        super.viewWillMove(toWindow: newWindow)
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let hoverArea { removeTrackingArea(hoverArea) }
        let area = NSTrackingArea(rect: .zero,
                                 options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect],
                                 owner: self, userInfo: nil)
        addTrackingArea(area)
        hoverArea = area
    }
    override func mouseEntered(with event: NSEvent) { setHover(true) }
    override func mouseExited(with event: NSEvent) { setHover(false) }
    private func setHover(_ value: Bool) {
        (cell as? JarvisPopupCell)?.hovering = value
        needsDisplay = true
    }
}

final class JarvisPopupCell: NSPopUpButtonCell {
    var hovering = false
    var increasedContrast = false

    override func draw(withFrame frame: NSRect, in view: NSView) {
        NSGraphicsContext.saveGraphicsState()
        defer { NSGraphicsContext.restoreGraphicsState() }
        NSGraphicsContext.current?.cgContext.setAlpha(isEnabled ? 1 : 0.42)
        let bounds = frame.insetBy(dx: 0.75, dy: 0.75)
        let shape = NSBezierPath(roundedRect: bounds, xRadius: 9, yRadius: 9)
        let highlighted = isEnabled && (hovering || isHighlighted)
        NSColor.labelColor.withAlphaComponent(highlighted ? 0.075 : 0.045).setFill()
        shape.fill()
        let border = increasedContrast ? NSColor.labelColor.withAlphaComponent(0.55)
            : highlighted ? NSColor(HUDStyle.accent(for: .active)).withAlphaComponent(0.4)
            : NSColor.labelColor.withAlphaComponent(0.14)
        border.setStroke()
        shape.lineWidth = 1
        shape.stroke()
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byTruncatingMiddle
        let text = title.flatMap { $0.isEmpty ? nil : $0 } ?? String(localized: "Seleccionar…", bundle: .module)
        let textRect = NSRect(x: bounds.minX + 12, y: bounds.midY - 8,
                              width: max(0, bounds.width - 40), height: 18)
        (text as NSString).draw(in: textRect, withAttributes: [
            .font: NSFont.systemFont(ofSize: 12), .foregroundColor: NSColor.labelColor,
            .paragraphStyle: paragraph,
        ])
        let x = bounds.maxX - 17
        let y = bounds.midY
        let direction: CGFloat = view.isFlipped ? -1 : 1
        let chevron = NSBezierPath()
        chevron.move(to: NSPoint(x: x - 3, y: y + 1.5 * direction))
        chevron.line(to: NSPoint(x: x, y: y - 1.5 * direction))
        chevron.line(to: NSPoint(x: x + 3, y: y + 1.5 * direction))
        chevron.lineWidth = 1.5
        chevron.lineCapStyle = .round
        NSColor.secondaryLabelColor.setStroke()
        chevron.stroke()
    }

    override func drawFocusRingMask(withFrame frame: NSRect, in view: NSView) {
        NSBezierPath(roundedRect: frame.insetBy(dx: 1, dy: 1), xRadius: 9, yRadius: 9).fill()
    }
}
