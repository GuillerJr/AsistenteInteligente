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
                        .foregroundStyle(Color.primary.opacity(0.55))
                        .accessibilityHidden(true)
                }
                .padding(8)
                .background(Color.primary.opacity(configuration.isExpanded ? 0.055 : 0.025), in: RoundedRectangle(cornerRadius: 8))
                .contentShape(RoundedRectangle(cornerRadius: 8))
            }
            .buttonStyle(.plain)
            .foregroundStyle(Color.primary.opacity(0.86))
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
                .font(.system(size: 12, weight: .medium))
                .frame(width: 30, height: 30)
                .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color.primary.opacity(hovering || contrast == .increased ? 0.95 : 0.72))
        .background(Color.primary.opacity(hovering ? 0.12 : 0.04), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8)
            .strokeBorder(Color.primary.opacity(contrast == .increased ? 0.6 : hovering ? 0.14 : 0.07))
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
                    .foregroundStyle(Color.primary.opacity(0.87))
                Text(state).font(.system(size: 11))
                    .foregroundStyle(color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
        .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
        .contentShape(RoundedRectangle(cornerRadius: 10))
    }
}

struct MenuBarStatusView: View {
    let status: AssistantPresentation

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: status.symbol)
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(HUDStyle.accent(for: status.tone))
                .frame(width: 32, height: 32)
                .background(HUDStyle.accent(for: status.tone).opacity(0.07),
                            in: RoundedRectangle(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 5) {
                Text(status.title)
                    .font(.system(size: 15, weight: .semibold))
                    .tracking(-0.2)
                    .foregroundStyle(Color.primary.opacity(0.95))
                Text(status.detail)
                    .font(.system(size: 12))
                    .lineSpacing(2)
                    .foregroundStyle(Color.primary.opacity(0.65))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.accessibilityDescription)
    }
}
