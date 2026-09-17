import SwiftUI

/// Two-option selector for rows that already display their own label.
struct JarvisToggleControlStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        JarvisToggleControl(isOn: configuration.$isOn, label: configuration.label)
    }
}

private struct JarvisToggleControl<Label: View>: View {
    @Binding var isOn: Bool
    let label: Label
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var focused: Bool?

    var body: some View {
        HStack(spacing: 0) {
            option("Activado", value: true)
            option("Desactivado", value: false)
        }
        .padding(3)
        .background(Color.primary.opacity(0.065),
                    in: RoundedRectangle(cornerRadius: 9, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous)
            .strokeBorder(Color.primary.opacity(contrast == .increased ? 0.55 : 0.1))
            .allowsHitTesting(false))
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(HUDStyle.accent(for: .active), lineWidth: 2)
                .padding(-3)
                .opacity(focused != nil ? 1 : 0)
                .allowsHitTesting(false)
        }
        .accessibilityRepresentation {
            Toggle(isOn: $isOn) { label }
                .toggleStyle(.switch)
        }
        .opacity(enabled ? 1 : 0.42)
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: isOn)
    }

    private func option(_ title: String, value: Bool) -> some View {
        Button {
            // Selecting the current state must not repeat a settings operation.
            if isOn != value { isOn = value }
        } label: {
            Text(title).frame(width: 76, height: 26)
        }
        .buttonStyle(JarvisToggleButtonStyle(selected: isOn == value))
        .focused($focused, equals: value)
    }
}

private struct JarvisToggleButtonStyle: ButtonStyle {
    let selected: Bool

    func makeBody(configuration: Configuration) -> some View {
        JarvisToggleAppearance(label: configuration.label, selected: selected,
                               pressed: configuration.isPressed)
    }
}

private struct JarvisToggleAppearance<Label: View>: View {
    let label: Label
    let selected: Bool
    let pressed: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.colorScheme) private var colorScheme
    @State private var hovering = false

    private var accent: Color { HUDStyle.accent(for: .active) }
    private var highlighted: Bool { enabled && (hovering || pressed) }
    private var track: RoundedRectangle {
        RoundedRectangle(cornerRadius: 6, style: .continuous)
    }

    var body: some View {
        label
            .font(.system(size: 11, weight: selected ? .semibold : .medium))
            .foregroundStyle(selected ? accent : JarvisInterfaceStyle.secondaryText)
            .background(track.fill(selected
                                   ? colorScheme == .light ? Color(nsColor: .textBackgroundColor)
                                   : Color.primary.opacity(0.1)
                                   : Color.primary.opacity(highlighted ? 0.055 : 0)))
            .overlay(track.strokeBorder(selected
                                        ? contrast == .increased ? accent : accent.opacity(0.22)
                                        : highlighted ? accent.opacity(0.3) : .clear)
                .allowsHitTesting(false))
            .shadow(color: Color.black.opacity(selected ? 0.08 : 0), radius: 2, y: 1)
            .contentShape(track)
            .opacity(pressed && enabled ? 0.82 : 1)
            .onHover { hovering = $0 }
    }
}
