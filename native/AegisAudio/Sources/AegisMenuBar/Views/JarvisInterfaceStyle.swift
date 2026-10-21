import SwiftUI

/// Shared interior chrome. Titled windows keep their native perimeter.
enum JarvisInterfaceStyle {
    static let text = JarvisTheme.ink
    static let secondaryText = JarvisTheme.inkSecondary
    static let settingsMinimumSize = CGSize(width: 820, height: 520)
}

struct JarvisSeparator: View {
    @Environment(\.colorSchemeContrast) private var contrast
    var body: some View {
        Rectangle().fill(contrast == .increased ? JarvisTheme.hairlineStrong : JarvisTheme.hairline)
            .frame(height: 1).accessibilityHidden(true)
    }
}

struct JarvisSectionHeader: View {
    let title: String
    let detail: String
    let symbol: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.system(size: 20, weight: .semibold)).tracking(-0.2)
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
        RoundedRectangle(cornerRadius: JarvisTheme.radiusControl, style: .continuous)
    }

    // Primary actions are ink on bone (clay is kept for Jarvis's own marks); the rest
    // are quiet washes with a hairline, as in the warm palette.
    var body: some View {
        label
            .font(.system(size: compact ? 12 : 13, weight: .medium))
            .foregroundStyle(prominent ? JarvisTheme.canvas
                             : destructive ? JarvisTheme.failure : JarvisTheme.ink)
            .padding(.horizontal, compact ? 10 : 14)
            .padding(.vertical, compact ? 5 : 8)
            .frame(minHeight: compact ? 28 : 34)
            .background(shape.fill(prominent ? JarvisTheme.ink.opacity(highlighted ? 0.86 : 1)
                                   : highlighted ? JarvisTheme.fillSelected : JarvisTheme.fill))
            .overlay(shape.strokeBorder(contrast == .increased ? JarvisTheme.hairlineStrong
                                        : prominent ? .clear : JarvisTheme.hairline)
                .allowsHitTesting(false))
            .opacity(enabled ? (pressed ? 0.85 : 1) : 0.42)
            .contentShape(shape)
            .onHover { hovering = $0 }
    }
}

private struct JarvisCardSurface: ViewModifier {
    @Environment(\.colorSchemeContrast) private var contrast
    func body(content: Content) -> some View {
        content
            .background(JarvisTheme.raised,
                        in: RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous)
                .strokeBorder(contrast == .increased ? JarvisTheme.hairlineStrong : JarvisTheme.hairline)
                .allowsHitTesting(false))
    }
}

private struct JarvisSheetSurface: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    func body(content: Content) -> some View {
        content.background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
            .foregroundStyle(JarvisInterfaceStyle.text)
            .tint(HUDStyle.accent(for: .active)).buttonStyle(MenuBarSmallButtonStyle())
    }
}

extension View {
    func jarvisCard() -> some View { modifier(JarvisCardSurface()) }
    func jarvisSheet() -> some View { modifier(JarvisSheetSurface()) }
}
