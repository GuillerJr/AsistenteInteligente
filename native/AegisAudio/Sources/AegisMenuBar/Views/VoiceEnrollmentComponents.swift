import SwiftUI

/// Shared desktop shell for the two local voice setup flows.
struct VoiceEnrollmentPanel<Content: View, Status: View, Footer: View>: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    let title: String
    let symbol: String
    let detail: String
    let width: CGFloat
    let height: CGFloat
    @ViewBuilder let content: () -> Content
    @ViewBuilder let status: () -> Status
    @ViewBuilder let footer: () -> Footer

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    Image(systemName: "waveform")
                        .font(.system(size: 15, weight: .medium))
                        .frame(width: 32, height: 32)
                        .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                        .accessibilityHidden(true)
                    Text("Jarvis").font(.system(size: 15, weight: .semibold))
                    Text("VOZ LOCAL")
                        .font(.system(size: 9, weight: .medium, design: .monospaced))
                        .tracking(1.5)
                        .foregroundStyle(Color.primary.opacity(0.5))
                    Spacer()
                    Label("En este Mac", systemImage: "lock.shield")
                        .font(.system(size: 11))
                        .foregroundStyle(Color.primary.opacity(0.6))
                }
                JarvisSectionHeader(title: title, detail: detail, symbol: symbol)
                status()
                    .font(.system(size: 12, weight: .medium))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .voiceEnrollmentCard()
                    .accessibilityElement(children: .combine)
            }
            .padding(20)
            separator
            ScrollView {
                VStack(alignment: .leading, spacing: 16) { content() }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
            }
            .scrollBounceBehavior(.basedOnSize)
            separator
            footer()
                .padding(.horizontal, 20)
                .padding(.vertical, 14)
        }
        .frame(minWidth: width, idealWidth: width, minHeight: min(height, 480), idealHeight: height)
        // The titled window owns the perimeter. Content meets the titlebar
        // flush, and macOS clips the bottom corners to its native silhouette.
        .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
        .foregroundStyle(Color.primary.opacity(0.95))
        .tint(HUDStyle.accent(for: .active))
        .buttonStyle(MenuBarSmallButtonStyle())
    }

    private var separator: some View {
        Rectangle().fill(Color.primary.opacity(contrast == .increased ? 0.3 : 0.07))
            .frame(height: 1)
            .accessibilityHidden(true)
    }
}

extension View {
    func voiceEnrollmentCard() -> some View { jarvisCard() }
}

struct VoiceEnrollmentSampleRow: View {
    let title: String
    let subtitle: String
    let symbol: String
    let guidance: String
    let count: Int
    let targetCount: Int
    let buttonTitle: String
    let canRecord: Bool
    let isRecording: Bool
    let record: () -> Void
    var canRemove = false
    var remove: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: symbol)
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(HUDStyle.accent(for: .active))
                    .frame(width: 32, height: 32)
                    .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 10))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.system(size: 13, weight: .semibold)).fixedSize(horizontal: false, vertical: true)
                    Text(subtitle).font(.system(size: 11)).foregroundStyle(Color.primary.opacity(0.6))
                }
                Spacer(minLength: 8)
                if let remove {
                    Button(role: .destructive, action: remove) {
                        Image(systemName: "trash").frame(width: 28, height: 28)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(HUDStyle.accent(for: .failure))
                    .disabled(!canRemove)
                    .accessibilityLabel("Eliminar perfil \(title)")
                    .help("Eliminar perfil \(title)")
                }
                Button(action: record) {
                    Label(buttonTitle, systemImage: isRecording ? "record.circle.fill" : "mic")
                }
                .buttonStyle(HUDActionStyle(accent: isRecording ? .red : HUDStyle.accent(for: .active),
                                           prominent: true))
                .disabled(!canRecord)
                .accessibilityLabel("\(buttonTitle): \(title)")
            }
            Text("Siguiente: \(guidance)")
                .font(.system(size: 12))
                .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 12) {
                ProgressView(value: Double(min(count, targetCount)), total: Double(targetCount))
                    .tint(HUDStyle.accent(for: count >= targetCount ? .success : .active))
                    .accessibilityLabel("Muestras de \(title)")
                    .accessibilityValue("\(count) de \(targetCount) mínimas")
                Text("\(count) / \(targetCount) mín.")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .monospacedDigit()
                    .foregroundStyle(HUDStyle.accent(for: count >= targetCount ? .success : .neutral))
            }
        }
        .padding(14)
        .voiceEnrollmentCard()
    }
}
