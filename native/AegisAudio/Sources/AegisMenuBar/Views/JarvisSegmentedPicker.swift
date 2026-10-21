import SwiftUI

struct JarvisSelectionOption<Value: Hashable>: Identifiable {
    let value: Value
    let title: String
    var id: Value { value }
}

struct JarvisSegmentedPicker<Value: Hashable>: View {
    let title: String
    @Binding var selection: Value
    let options: [JarvisSelectionOption<Value>]
    var optionWidth: CGFloat? = nil
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var focused: Value?

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options) { option in
                Button {
                    if selection != option.value { selection = option.value }
                } label: {
                    Text(option.title).lineLimit(1)
                        .frame(width: optionWidth, height: 28)
                        .frame(maxWidth: optionWidth == nil ? .infinity : nil)
                }
                .buttonStyle(JarvisSegmentButtonStyle(selected: selection == option.value))
                .focused($focused, equals: option.value)
            }
        }
        .padding(3)
        .background(JarvisTheme.fill,
                    in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous)
            .strokeBorder(contrast == .increased ? JarvisTheme.hairlineStrong : .clear)
            .allowsHitTesting(false))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
            .strokeBorder(HUDStyle.accent(for: .active), lineWidth: 2)
            .padding(-3).opacity(focused != nil ? 1 : 0).allowsHitTesting(false))
        .opacity(enabled ? 1 : 0.42)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: selection)
        .onKeyPress(.leftArrow) { moveSelection(by: -1) }
        .onKeyPress(.rightArrow) { moveSelection(by: 1) }
        .accessibilityRepresentation {
            Picker(title, selection: $selection) {
                ForEach(options) { Text($0.title).tag($0.value) }
            }.pickerStyle(.segmented).labelsHidden().accessibilityLabel(title)
        }
    }

    private func moveSelection(by offset: Int) -> KeyPress.Result {
        guard enabled, let index = options.firstIndex(where: { $0.value == selection }) else { return .ignored }
        let next = index + offset
        if options.indices.contains(next) {
            selection = options[next].value
            focused = options[next].value
        }
        return .handled
    }
}

struct JarvisSegmentButtonStyle: ButtonStyle {
    let selected: Bool
    func makeBody(configuration: Configuration) -> some View {
        JarvisSegmentAppearance(label: configuration.label, selected: selected,
                                pressed: configuration.isPressed)
    }
}

private struct JarvisSegmentAppearance<Label: View>: View {
    let label: Label
    let selected: Bool
    let pressed: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.colorScheme) private var colorScheme
    @State private var hovering = false
    private var highlighted: Bool { enabled && (hovering || pressed) }
    private var shape: RoundedRectangle { RoundedRectangle(cornerRadius: 6, style: .continuous) }

    // Selected segment: a raised bone chip with ink text, as macOS and the warm palette do.
    var body: some View {
        label.font(.system(size: 12, weight: selected ? .medium : .regular))
            .foregroundStyle(selected ? JarvisTheme.ink : JarvisTheme.inkSecondary)
            .background(shape.fill(selected ? JarvisTheme.raised
                                   : highlighted ? JarvisTheme.fill : .clear))
            .overlay(shape.strokeBorder(selected && contrast == .increased ? JarvisTheme.hairlineStrong
                                        : selected ? JarvisTheme.hairline : .clear).allowsHitTesting(false))
            .shadow(color: Color.black.opacity(selected && colorScheme == .light ? 0.06 : 0), radius: 1.5, y: 1)
            .contentShape(shape).opacity(pressed && enabled ? 0.82 : 1)
            .onHover { hovering = $0 }
    }
}
