import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let notchWidth: CGFloat
    let notchHeight: CGFloat
    let expanded: Bool
    let toggle: () -> Void
    let startVoiceTurn: () -> Void
    let showHUD: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                wingButton(mirrored: false)
                Color.clear
                    .frame(width: notchWidth)
                    .allowsHitTesting(false)
                wingButton(mirrored: true)
            }
            .frame(height: notchHeight)

            Capsule()
                .fill(statusColor.opacity(statusOpacity))
                .frame(width: underlineWidth, height: 2)
                .shadow(color: statusColor.opacity(glowOpacity), radius: 4)
                .frame(height: 8, alignment: .top)

            if expanded {
                controls
                    .frame(height: 96)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(alignment: .top) {
            UnevenRoundedRectangle(
                bottomLeadingRadius: 12,
                bottomTrailingRadius: 12
            )
            .fill(.black)
        }
        .animation(.easeOut(duration: 0.12), value: model.voiceActivityLevel)
        .animation(.easeInOut(duration: 0.2), value: model.voiceState)
        .animation(.easeInOut(duration: 0.2), value: expanded)
    }

    private func wingButton(mirrored: Bool) -> some View {
        Button(action: toggle) {
            HStack(spacing: 4) {
                Capsule()
                    .fill(statusColor.opacity(statusOpacity))
                    .frame(width: barWidth, height: 2)
                Circle()
                    .fill(statusColor.opacity(statusOpacity))
                    .frame(width: 4, height: 4)
            }
            .scaleEffect(x: mirrored ? -1 : 1)
            .shadow(color: statusColor.opacity(glowOpacity), radius: 3)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(expanded ? "Contraer Jarvis" : "Abrir controles de Jarvis")
    }

    private var controls: some View {
        VStack(spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 6, height: 6)
                    .shadow(color: statusColor.opacity(0.7), radius: 3)
                Text(statusTitle.uppercased())
                    .font(.caption2.monospaced().weight(.semibold))
                    .tracking(1.2)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Button(action: toggle) {
                    Image(systemName: "chevron.up")
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Contraer Jarvis")
            }
            .foregroundStyle(statusColor.opacity(0.9))

            HStack(spacing: 10) {
                Button(action: startVoiceTurn) {
                    Label("Hablar", systemImage: "waveform")
                        .frame(maxWidth: .infinity)
                }
                .disabled(!model.canStartVoiceTurn)

                Button(action: showHUD) {
                    Label("HUD", systemImage: "circle.hexagongrid.fill")
                        .frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .tint(.cyan)
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 12)
    }

    private var statusTitle: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "Auditoría comprometida"
        }
        if model.daemonState != .online {
            return "Daemon no disponible"
        }
        return model.voiceState.title
    }

    private var statusColor: Color {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return .red
        }
        if model.daemonState == .offline || model.securityState == .unavailable {
            return .orange
        }
        switch model.voiceState {
        case .awaitingApproval:
            return .orange
        case .failed:
            return .red
        case .completed:
            return .green
        case .processing, .submitting:
            return .purple
        case .idle, .listening, .speaking:
            return .cyan
        }
    }

    private var statusOpacity: Double {
        switch model.voiceState {
        case .idle, .completed:
            0.35
        default:
            0.9
        }
    }

    private var glowOpacity: Double {
        model.voiceState == .idle ? 0.15 : 0.65
    }

    private var barWidth: CGFloat {
        10 + (CGFloat(model.voiceActivityLevel) * 10)
    }

    private var underlineWidth: CGFloat {
        switch model.voiceState {
        case .idle, .completed:
            24
        case .listening:
            32 + (CGFloat(model.voiceActivityLevel) * 32)
        default:
            52
        }
    }
}
