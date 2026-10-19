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
        .background(Color.primary.opacity(0.065),
                    in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous)
            .strokeBorder(Color.primary.opacity(contrast == .increased ? 0.55 : 0.1))
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
    private var accent: Color { HUDStyle.accent(for: .active) }
    private var highlighted: Bool { enabled && (hovering || pressed) }
    private var shape: RoundedRectangle { RoundedRectangle(cornerRadius: 6, style: .continuous) }

    var body: some View {
        label.font(.system(size: 11, weight: selected ? .semibold : .medium))
            .foregroundStyle(selected ? accent : JarvisInterfaceStyle.secondaryText)
            .background(shape.fill(selected
                                   ? colorScheme == .light ? Color(nsColor: .textBackgroundColor)
                                   : Color.primary.opacity(0.1)
                                   : Color.primary.opacity(highlighted ? 0.055 : 0)))
            .overlay(shape.strokeBorder(selected
                                        ? contrast == .increased ? accent : accent.opacity(0.22)
                                        : highlighted ? accent.opacity(0.3) : .clear).allowsHitTesting(false))
            .shadow(color: Color.black.opacity(selected ? 0.08 : 0), radius: 2, y: 1)
            .contentShape(shape).opacity(pressed && enabled ? 0.82 : 1)
            .onHover { hovering = $0 }
    }
}
