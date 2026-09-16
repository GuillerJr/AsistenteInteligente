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
                        .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                    Text("Jarvis").font(.system(size: 15, weight: .semibold))
                    Spacer()
                    Text("TU DECISIÓN").font(.system(size: 9, weight: .medium, design: .monospaced))
                        .tracking(1.5).foregroundStyle(.white.opacity(0.5))
                }
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: symbol).font(.system(size: 20))
                        .foregroundStyle(HUDStyle.accent(for: .active)).frame(width: 40, height: 40)
                        .background(.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(title).font(.system(size: 19, weight: .semibold))
                        Text(detail).font(.system(size: 12)).foregroundStyle(.white.opacity(0.65))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }.padding(20)
            separator
            ScrollView {
                VStack(alignment: .leading, spacing: 16) { content() }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(20)
            }.scrollBounceBehavior(.basedOnSize)
            separator
            footer().padding(20)
        }
        .frame(width: 560, height: height)
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(.white.opacity(0.95))
        .preferredColorScheme(.dark)
        .tint(HUDStyle.accent(for: .active))
        .buttonStyle(MenuBarSmallButtonStyle())
    }

    private var separator: some View {
        Rectangle().fill(.white.opacity(contrast == .increased ? 0.3 : 0.07)).frame(height: 1)
    }
}
