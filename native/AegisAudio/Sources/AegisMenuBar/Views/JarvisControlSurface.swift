import SwiftUI

struct JarvisControlSurface: ViewModifier {
    var focused = false
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @State private var hovering = false

    func body(content: Content) -> some View {
        content.background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusControl, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusControl, style: .continuous)
                .strokeBorder(focused ? JarvisTheme.clay.opacity(0.7)
                              : contrast == .increased ? JarvisTheme.inkMuted
                              : enabled && hovering ? JarvisTheme.hairlineStrong
                              : JarvisTheme.hairline, lineWidth: 1)
                .allowsHitTesting(false))
            .opacity(enabled ? 1 : 0.42)
            .onHover { hovering = $0 }
    }
}

extension View {
    func jarvisInput(focused: Bool = false, fontSize: CGFloat = 12) -> some View {
        textFieldStyle(.plain).font(.system(size: fontSize))
            .padding(.horizontal, 10).padding(.vertical, 7)
            .modifier(JarvisControlSurface(focused: focused))
    }
}
