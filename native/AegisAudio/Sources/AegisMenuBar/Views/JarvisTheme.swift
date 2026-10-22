import AppKit
import SwiftUI

/// Quiet neutral workspace surfaces; the clay accent retains Jarvis's identity.
/// Shared adaptive tokens keep the chat, auxiliary panels and settings consistent.
enum JarvisTheme {
    /// Window canvas: chat thread, settings pane, dialogs.
    static let canvas = adaptive(light: 0xFFFFFF, dark: 0x212121)
    /// Sidebars and the menu bar panel, a step below the canvas.
    static let sidebar = adaptive(light: 0xF7F7F7, dark: 0x181818)
    /// Raised surfaces: cards, the composer, popovers.
    static let raised = adaptive(light: 0xF7F7F7, dark: 0x303030)
    static let ink = adaptive(light: 0x181818, dark: 0xECECEC)
    static let inkSecondary = adaptive(light: 0x5D5D5D, dark: 0xB8B8B8)
    static let inkMuted = adaptive(light: 0x737373, dark: 0xA0A0A0)
    static let hairline = adaptive(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.09, darkAlpha: 0.1)
    static let hairlineStrong = adaptive(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.22, darkAlpha: 0.25)
    /// Hover and chip wash.
    static let fill = adaptive(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.04, darkAlpha: 0.06)
    /// Selected row wash.
    static let fillSelected = adaptive(light: 0x000000, dark: 0xFFFFFF, lightAlpha: 0.075, darkAlpha: 0.1)
    static let userBubble = adaptive(light: 0xF1F1F1, dark: 0x353535)
    static let clay = adaptive(light: 0xC4643F, dark: 0xD9805C)
    static let warning = adaptive(light: 0x9A6312, dark: 0xE8B36A)
    static let failure = adaptive(light: 0xB3412F, dark: 0xF08E7E)
    static let success = adaptive(light: 0x4E7A47, dark: 0x9CC68F)

    static let radiusControl: CGFloat = 8
    static let radiusCard: CGFloat = 12
    static let radiusComposer: CGFloat = 20
    static let rowHeight: CGFloat = 30

    /// System type keeps long answers and workspace controls visually coherent.
    static func voice(_ size: CGFloat = 14) -> Font { .system(size: size) }

    static func adaptive(light: UInt32, dark: UInt32,
                         lightAlpha: CGFloat = 1, darkAlpha: CGFloat = 1) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            let value = isDark ? dark : light
            return NSColor(srgbRed: CGFloat((value >> 16) & 0xFF) / 255,
                           green: CGFloat((value >> 8) & 0xFF) / 255,
                           blue: CGFloat(value & 0xFF) / 255,
                           alpha: isDark ? darkAlpha : lightAlpha)
        })
    }
}

/// A full-width row in sidebars and menus: icon, title, optional trailing detail.
struct JarvisRowButtonStyle: ButtonStyle {
    var selected = false

    func makeBody(configuration: Configuration) -> some View {
        JarvisRowAppearance(label: configuration.label, pressed: configuration.isPressed, selected: selected)
    }
}

private struct JarvisRowAppearance<Label: View>: View {
    let label: Label
    let pressed: Bool
    let selected: Bool
    @Environment(\.isEnabled) private var enabled
    @State private var hovering = false

    var body: some View {
        label
            .font(.system(size: 13))
            .foregroundStyle(selected ? JarvisTheme.ink : JarvisTheme.ink.opacity(0.88))
            .padding(.horizontal, 10)
            .frame(maxWidth: .infinity, minHeight: JarvisTheme.rowHeight, alignment: .leading)
            .background(selected || pressed ? JarvisTheme.fillSelected : hovering ? JarvisTheme.fill : .clear,
                        in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
            .opacity(enabled ? 1 : 0.45)
            .onHover { hovering = $0 }
    }
}

/// Toolbar icons: no chrome until hovered, like a native window toolbar.
struct JarvisIconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        JarvisIconAppearance(label: configuration.label, pressed: configuration.isPressed)
    }
}

private struct JarvisIconAppearance<Label: View>: View {
    let label: Label
    let pressed: Bool
    @Environment(\.isEnabled) private var enabled
    @State private var hovering = false

    var body: some View {
        label
            .font(.system(size: 13))
            .foregroundStyle(hovering ? JarvisTheme.ink : JarvisTheme.inkSecondary)
            .padding(.horizontal, 6).frame(minWidth: 28, minHeight: 28)
            .background(pressed ? JarvisTheme.fillSelected : hovering ? JarvisTheme.fill : .clear,
                        in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
            .opacity(enabled ? 1 : 0.4)
            .onHover { hovering = $0 }
    }
}
