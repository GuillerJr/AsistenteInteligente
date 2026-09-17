import SwiftUI

/// Compact control for rows that already display their own label.
struct JarvisToggleControlStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        JarvisToggleControl(isOn: configuration.$isOn, label: configuration.label)
    }
}

private struct JarvisToggleControl<Label: View>: View {
    @Binding var isOn: Bool
    let label: Label
    @FocusState private var focused: Bool

    var body: some View {
        Button { isOn.toggle() } label: {
            Color.clear.frame(width: 42, height: 28)
        }
        .buttonStyle(JarvisToggleButtonStyle(isOn: isOn))
        .focused($focused)
        .overlay {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(HUDStyle.accent(for: .active), lineWidth: 2)
                .padding(-3)
                .opacity(focused ? 1 : 0)
                .allowsHitTesting(false)
        }
        .accessibilityRepresentation {
            Toggle(isOn: $isOn) { label }
                .toggleStyle(.switch)
        }
    }
}

private struct JarvisToggleButtonStyle: ButtonStyle {
    let isOn: Bool

    func makeBody(configuration: Configuration) -> some View {
        JarvisToggleAppearance(isOn: isOn, pressed: configuration.isPressed)
    }
}

private struct JarvisToggleAppearance: View {
    let isOn: Bool
    let pressed: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var hovering = false

    private var accent: Color { HUDStyle.accent(for: .active) }
    private var highlighted: Bool { enabled && (hovering || pressed) }
    private var track: RoundedRectangle {
        RoundedRectangle(cornerRadius: 8, style: .continuous)
    }

    var body: some View {
        track
            .fill(isOn ? accent.opacity(highlighted ? 0.28 : 0.18)
                  : Color.primary.opacity(highlighted ? 0.11 : 0.055))
            .overlay(track.strokeBorder(contrast == .increased ? Color.primary.opacity(0.65)
                                        : isOn || highlighted ? accent.opacity(0.6)
                                        : Color.primary.opacity(0.22)))
            .overlay(alignment: isOn ? .trailing : .leading) {
                RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .fill(isOn ? accent : Color.primary.opacity(0.5))
                    .overlay {
                        Image(systemName: isOn ? "checkmark" : "minus")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(Color(nsColor: .textBackgroundColor))
                    }
                    .frame(width: 18, height: 18)
                    .padding(3)
            }
            .frame(width: 42, height: 24)
            .frame(height: 28)
            .contentShape(Rectangle())
            .opacity(enabled ? (pressed ? 0.82 : 1) : 0.42)
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.16), value: isOn)
            .onHover { hovering = $0 }
    }
}
