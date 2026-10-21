import AegisAudioCore
import SwiftUI

/// Compact utility surface. Runtime state remains owned by MenuBarModel.
struct HUDView: View {
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.openWindow) private var openWindow

    let model: MenuBarModel
    let close: () -> Void
    var interactive = true
    var forceReduceMotion = false
    var forceHighContrast = false

    private var status: AssistantPresentation { model.presentationStatus }
    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }
    private var highContrast: Bool { contrast == .increased || forceHighContrast }
    private var accent: Color { HUDStyle.accent(for: status.tone) }

    var body: some View {
        GeometryReader { geometry in
            let compact = geometry.size.height < 460
            VStack(spacing: 0) {
                HUDHeader(close: close, highContrast: highContrast)
                separator
                ScrollView {
                    VStack(spacing: compact ? 14 : 18) {
                        HUDPresenceView(
                            accent: accent,
                            symbol: status.symbol,
                            animated: status.isAnimated && !reduceMotion,
                            voiceLevel: model.voiceActivityLevel,
                            listening: !reduceMotion && (status.kind == .listening || status.kind == .followingUp),
                            highContrast: highContrast
                        )
                        .frame(height: compact ? 88 : 144)

                        HUDStatusMessage(status: status)

                        if model.voiceState == .failed, let text = model.voiceOutputText {
                            VStack(alignment: .leading, spacing: 8) {
                                Label("Respuesta en texto", systemImage: "text.alignleft")
                                    .font(.system(size: 11, weight: .semibold))
                                    .foregroundStyle(JarvisTheme.inkSecondary)
                                Text(verbatim: text)
                                    .font(.system(size: 13))
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(14)
                            .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 12))
                        }

                        if status.kind == .working || status.kind == .processing {
                            HUDActivitySummary(activity: model.hudActivity, accent: accent)
                        }

                        actions
                    }
                    .padding(.horizontal, 26)
                    .padding(.vertical, compact ? 12 : 20)
                    .frame(maxWidth: .infinity)
                    .frame(minHeight: max(0, geometry.size.height - 126), alignment: .center)
                }
                .scrollBounceBehavior(.basedOnSize)
                .defaultScrollAnchor(.top)
                separator
                HUDConnectionFooter(daemon: model.daemonState, security: model.securityState)
            }
        }
        .background {
            HUDSurface(opaque: reduceTransparency || highContrast)
        }
        .clipShape(RoundedRectangle(cornerRadius: HUDStyle.cornerRadius, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: HUDStyle.cornerRadius, style: .continuous)
                .strokeBorder(highContrast ? JarvisTheme.hairlineStrong : JarvisTheme.hairline, lineWidth: 1)
                .allowsHitTesting(false)
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.22), value: status.kind)
    }

    private var separator: some View {
        Rectangle()
            .fill(highContrast ? JarvisTheme.hairlineStrong : JarvisTheme.hairline)
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var actions: some View {
        if status.kind == .browserSelection, let selection = model.pendingBrowserSelection {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 125))], spacing: 8) {
                ForEach(selection.options, id: \.bundleIdentifier) { option in
                    Button {
                        Task { await model.selectBrowser(bundleIdentifier: option.bundleIdentifier) }
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: "globe")
                            Text(option.name)
                            Spacer(minLength: 0)
                            Image(systemName: "arrow.up.right").font(.system(size: 10, weight: .medium))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(HUDActionStyle(accent: accent))
                    .disabled(!interactive)
                    .help("Continuar en \(option.name)")
                }
            }
        }
        if model.interactionID != nil {
            Button { RequestedWindowFocus.open("approval") { openWindow(id: "approval") } } label: {
                Label("Revisar solicitud", systemImage: "arrow.up.right")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(HUDActionStyle(accent: accent, prominent: true))
            .disabled(!interactive)
        }
        if model.activeComputerUseJobID != nil {
            Button(role: .destructive) {
                Task { await model.cancelActiveComputerUse() }
            } label: {
                Label("Detener control del Mac", systemImage: "stop.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .failure)))
            .disabled(!interactive)
            .help("Solicitar la cancelación de la tarea de control activa")
        }
    }
}
