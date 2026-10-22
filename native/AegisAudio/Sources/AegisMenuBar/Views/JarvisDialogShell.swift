import SwiftUI

struct JarvisDialogShell<Content: View, Footer: View>: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    let title: String
    let symbol: String
    let detail: String
    var height: CGFloat = 560
    @ViewBuilder let content: () -> Content
    @ViewBuilder let footer: () -> Footer

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    Image(systemName: "waveform").frame(width: 30, height: 30)
                        .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 10))
                    Text(String(localized: "Jarvis", bundle: .module)).font(.system(size: 15, weight: .semibold))
                    Spacer()
                    Text(String(localized: "TU DECISIÓN", bundle: .module)).font(.system(size: 9, weight: .medium, design: .monospaced))
                        .tracking(1.5).foregroundStyle(JarvisTheme.inkMuted)
                }
                JarvisSectionHeader(title: title, detail: detail, symbol: symbol)
            }.padding(20)
            separator
            ScrollView {
                VStack(alignment: .leading, spacing: 16) { content() }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(20)
            }.scrollBounceBehavior(.basedOnSize)
            separator
            footer().padding(20)
        }
        .frame(minWidth: 560, idealWidth: 560, minHeight: min(height, 400), idealHeight: height)
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(JarvisTheme.ink)
        .tint(HUDStyle.accent(for: .active))
        .buttonStyle(MenuBarSmallButtonStyle())
    }

    private var separator: some View {
        Rectangle().fill(contrast == .increased ? JarvisTheme.hairlineStrong : JarvisTheme.hairline).frame(height: 1)
    }
}
