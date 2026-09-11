import AegisAudioCore
import SwiftUI

struct MenuBarIconButton: View {
    let symbol: String
    let title: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .medium))
                .frame(width: 28, height: 28)
                .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .foregroundStyle(.white.opacity(hovering ? 0.95 : 0.65))
        .background(.white.opacity(hovering ? 0.12 : 0.04), in: RoundedRectangle(cornerRadius: 8))
        .onHover { hovering = $0 }
        .accessibilityLabel(title)
        .help(title)
    }
}

struct MenuBarSmallButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(.white.opacity(0.9))
            .padding(.horizontal, 10)
            .frame(height: 28)
            .background(.white.opacity(configuration.isPressed ? 0.14 : 0.06),
                        in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8)
                .strokeBorder(.white.opacity(contrast == .increased ? 0.6 : 0.12)))
            .opacity(enabled ? 1 : 0.45)
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
                    .foregroundStyle(.white.opacity(0.87))
                Text(state).font(.system(size: 10))
                    .foregroundStyle(color)
            }
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
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
                    .foregroundStyle(.white.opacity(0.95))
                Text(status.detail)
                    .font(.system(size: 12))
                    .lineSpacing(2)
                    .foregroundStyle(.white.opacity(0.65))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(status.accessibilityDescription)
    }
}
