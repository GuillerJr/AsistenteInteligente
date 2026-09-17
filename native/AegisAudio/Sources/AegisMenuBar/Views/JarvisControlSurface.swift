import SwiftUI

struct JarvisControlSurface: ViewModifier {
    var focused = false
    @Environment(\.isEnabled) private var enabled
    @Environment(\.colorSchemeContrast) private var contrast
    @State private var hovering = false

    func body(content: Content) -> some View {
        content.background(Color.primary.opacity(enabled && hovering ? 0.075 : 0.045),
                           in: RoundedRectangle(cornerRadius: 9, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous)
                .strokeBorder(focused ? HUDStyle.accent(for: .active)
                              : contrast == .increased ? Color.primary.opacity(0.55)
                              : enabled && hovering ? HUDStyle.accent(for: .active).opacity(0.4)
                              : Color.primary.opacity(0.14), lineWidth: focused ? 1.5 : 1)
                .allowsHitTesting(false))
            .opacity(enabled ? 1 : 0.42)
            .onHover { hovering = $0 }
    }
}

extension View {
    func jarvisInput(focused: Bool = false, fontSize: CGFloat = 12) -> some View {
        textFieldStyle(.plain).font(.system(size: fontSize))
            .padding(.horizontal, 12).padding(.vertical, 10)
            .modifier(JarvisControlSurface(focused: focused))
    }
}
