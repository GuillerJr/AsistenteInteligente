import SwiftUI

struct NotchPresenceView: View {
    let model: MenuBarModel
    let notchWidth: CGFloat
    let notchHeight: CGFloat

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                wing(mirrored: false)
                    .frame(width: 28)
                Color.clear
                    .frame(width: notchWidth)
                wing(mirrored: true)
                    .frame(width: 28)
            }
            .frame(height: notchHeight)

            Capsule()
                .fill(statusColor.opacity(statusOpacity))
                .frame(width: underlineWidth, height: 2)
                .shadow(color: statusColor.opacity(glowOpacity), radius: 4)
                .frame(height: 8, alignment: .top)
        }
        .background(alignment: .top) {
            UnevenRoundedRectangle(
                bottomLeadingRadius: 12,
                bottomTrailingRadius: 12
            )
            .fill(.black)
        }
        .animation(.easeOut(duration: 0.12), value: model.voiceActivityLevel)
        .animation(.easeInOut(duration: 0.2), value: model.voiceState)
        .accessibilityHidden(true)
    }

    private func wing(mirrored: Bool) -> some View {
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
