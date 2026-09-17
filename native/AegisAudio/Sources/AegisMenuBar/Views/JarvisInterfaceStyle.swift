import SwiftUI

/// Shared interior chrome. Titled windows keep their native perimeter.
enum JarvisInterfaceStyle {
    static let text = Color.primary.opacity(0.95)
    static let secondaryText = Color.secondary
    static let settingsMinimumSize = CGSize(width: 820, height: 520)
}

struct JarvisSeparator: View {
    @Environment(\.colorSchemeContrast) private var contrast
    var body: some View {
        Rectangle().fill(Color.primary.opacity(contrast == .increased ? 0.3 : 0.09))
            .frame(height: 1).accessibilityHidden(true)
    }
}

struct JarvisSectionHeader: View {
    let title: String
    let detail: String
    let symbol: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 20, weight: .medium))
                .foregroundStyle(HUDStyle.accent(for: .active))
                .frame(width: 44, height: 44)
                .background(HUDStyle.accent(for: .active).opacity(0.08),
                            in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.system(size: 21, weight: .semibold)).tracking(-0.3)
                    .foregroundStyle(JarvisInterfaceStyle.text)
                    .fixedSize(horizontal: false, vertical: true)
                Text(detail).font(.system(size: 12)).lineSpacing(3)
                    .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct JarvisSwitchStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 16) {
            configuration.label.fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 12)
            Toggle(isOn: configuration.$isOn) { configuration.label }
                .toggleStyle(JarvisToggleControlStyle())
        }.frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityRepresentation {
                Toggle(isOn: configuration.$isOn) { configuration.label }.toggleStyle(.switch)
            }
    }
}

struct JarvisButtonStyle: ButtonStyle {
    var accent = HUDStyle.accent(for: .active)
    var prominent = false
    var compact = false

    func makeBody(configuration: Configuration) -> some View {
        JarvisButtonAppearance(label: configuration.label, pressed: configuration.isPressed,
                               accent: accent, prominent: prominent, compact: compact,
                               destructive: configuration.role == .destructive)
    }
}

private struct JarvisButtonAppearance<Label: View>: View {
    let label: Label
    let pressed: Bool
    let accent: Color
    let prominent: Bool
    let compact: Bool
    let destructive: Bool
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @State private var hovering = false

    private var highlighted: Bool { enabled && (hovering || pressed) }
    private var shape: RoundedRectangle {
        RoundedRectangle(cornerRadius: compact ? 8 : 10, style: .continuous)
    }

    var body: some View {
        label
            .font(.system(size: compact ? 11 : 12, weight: .medium))
            .foregroundStyle(prominent ? Color(nsColor: .textBackgroundColor)
                             : destructive ? HUDStyle.accent(for: .failure) : JarvisInterfaceStyle.text)
            .padding(.horizontal, compact ? 10 : 12)
            .padding(.vertical, compact ? 7 : 11)
            .frame(minHeight: compact ? 30 : 38)
            .background(shape.fill(prominent ? accent : Color.primary.opacity(highlighted ? 0.11 : 0.055)))
            .overlay(shape.strokeBorder(contrast == .increased ? Color.primary.opacity(0.6)
                                        : highlighted ? accent.opacity(0.55) : Color.primary.opacity(0.14))
                .allowsHitTesting(false))
            .opacity(enabled ? (pressed ? 0.82 : 1) : 0.42)
            .contentShape(shape)
            .onHover { hovering = $0 }
    }
}

private struct JarvisCardSurface: ViewModifier {
    @Environment(\.colorSchemeContrast) private var contrast
    func body(content: Content) -> some View {
        content
            .background(Color.primary.opacity(contrast == .increased ? 0.065 : 0.035),
                        in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.primary.opacity(contrast == .increased ? 0.45 : 0.11))
                .allowsHitTesting(false))
    }
}

private struct JarvisSheetSurface: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    func body(content: Content) -> some View {
        content.background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
            .foregroundStyle(JarvisInterfaceStyle.text).jarvisAppearance()
            .tint(HUDStyle.accent(for: .active)).buttonStyle(MenuBarSmallButtonStyle())
    }
}

extension View {
    func jarvisCard() -> some View { modifier(JarvisCardSurface()) }
    func jarvisSheet() -> some View { modifier(JarvisSheetSurface()) }
}
