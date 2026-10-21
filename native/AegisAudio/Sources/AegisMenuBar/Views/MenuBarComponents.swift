import AegisAudioCore
import SwiftUI

/// Full-width disclosure rows without the system style's nested indentation.
struct MenuBarDisclosureStyle: DisclosureGroupStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Button {
                configuration.isExpanded.toggle()
            } label: {
                HStack(spacing: 8) {
                    configuration.label
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: configuration.isExpanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(JarvisTheme.inkMuted)
                        .accessibilityHidden(true)
                }
                .padding(.horizontal, 10).frame(minHeight: JarvisTheme.rowHeight)
                .background(configuration.isExpanded ? JarvisTheme.fill : .clear, in: RoundedRectangle(cornerRadius: 7))
                .contentShape(RoundedRectangle(cornerRadius: 7))
            }
            .buttonStyle(.plain)
            .foregroundStyle(JarvisTheme.ink)
            .accessibilityValue(configuration.isExpanded ? "Expandido" : "Contraído")
            .accessibilityHint(configuration.isExpanded ? "Contraer sección" : "Expandir sección")

            if configuration.isExpanded {
                configuration.content
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct MenuBarIconButton: View {
    let symbol: String
    let title: String
    let action: () -> Void
    @State private var hovering = false
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .regular))
                .frame(width: 28, height: 28)
                .contentShape(RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .foregroundStyle(hovering || contrast == .increased ? JarvisTheme.ink : JarvisTheme.inkSecondary)
        .background(hovering ? JarvisTheme.fill : .clear, in: RoundedRectangle(cornerRadius: 7))
        .overlay(RoundedRectangle(cornerRadius: 7)
            .strokeBorder(contrast == .increased ? JarvisTheme.hairlineStrong : .clear)
            .allowsHitTesting(false))
        .onHover { hovering = $0 }
        .opacity(enabled ? 1 : 0.42)
        .accessibilityLabel(title)
        .help(title)
    }
}

struct MenuBarSmallButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        JarvisButtonStyle(compact: true).makeBody(configuration: configuration)
    }
}

struct MenuBarPermissionItem: View {
    let title: String
    let symbol: String
    let state: String
    let color: Color
    let action: (() -> Void)?

    var body: some View {
        Group {
            if let action {
                Button(action: action) { content }
                    .buttonStyle(.plain)
                    .accessibilityLabel(title + ": " + state)
                    .help("Configurar " + title.lowercased())
            } else {
                content
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(title + ": " + state)
            }
        }
    }

    private var content: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.system(size: 13))
                .foregroundStyle(color)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 11, weight: .medium))
                    .foregroundStyle(JarvisTheme.ink)
                Text(state).font(.system(size: 11))
                    .foregroundStyle(color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
        .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusControl))
        .contentShape(RoundedRectangle(cornerRadius: JarvisTheme.radiusControl))
    }
}

struct MenuBarStatusView: View {
    let status: AssistantPresentation

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 9) {
            Circle().fill(HUDStyle.accent(for: status.tone)).frame(width: 7, height: 7)
                .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
            VStack(alignment: .leading, spacing: 3) {
                Text(status.title)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(JarvisTheme.ink)
                Text(status.detail)
                    .font(.system(size: 12))
                    .lineSpacing(2)
                    .foregroundStyle(JarvisTheme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.accessibilityDescription)
    }
}
