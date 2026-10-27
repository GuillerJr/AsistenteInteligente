import AppKit
import SwiftUI

/// One stable accessibility element with an explicit changing value. Child
/// Text/Form/LabeledContent aggregation must not retain an obsolete value.
struct MemoryMetadataRow: View {
    let label: String
    let value: String
    let identifier: String
    init(_ label: String, value: String, id: String) {
        self.label = label; self.value = value; identifier = id
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(verbatim: label)
            Spacer(minLength: 8)
            Text(verbatim: value).multilineTextAlignment(.trailing)
        }
        .accessibilityHidden(true)
        .overlay {
            MemoryMetadataAccessibility(label: label, value: value, identifier: identifier)
        }
    }
}

/// The visible row stays in SwiftUI. Its single semantic element has a stable
/// AppKit identity, so a Form cannot keep an older aggregated AX value.
@MainActor private struct MemoryMetadataAccessibility: NSViewRepresentable {
    let label: String
    let value: String
    let identifier: String
    func makeNSView(context: Context) -> MemoryMetadataAccessibilityView {
        let view = MemoryMetadataAccessibilityView(frame: .zero)
        view.update(label: label, value: value, identifier: identifier)
        return view
    }
    func updateNSView(_ view: MemoryMetadataAccessibilityView, context: Context) {
        view.update(label: label, value: value, identifier: identifier)
    }
}

@MainActor final class MemoryMetadataAccessibilityView: NSView {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func update(label: String, value: String, identifier: String) {
        let changed = accessibilityValue() as? String != value
        if !isAccessibilityElement() { setAccessibilityElement(true) }
        if accessibilityRole() != .staticText { setAccessibilityRole(.staticText) }
        if accessibilityLabel() != label { setAccessibilityLabel(label) }
        if changed { setAccessibilityValue(value) }
        if accessibilityIdentifier() != identifier { setAccessibilityIdentifier(identifier) }
        if changed, window != nil {
            NSAccessibility.post(element: self, notification: .valueChanged)
        }
    }
}
