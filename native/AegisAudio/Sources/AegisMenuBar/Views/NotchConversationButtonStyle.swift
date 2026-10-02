import SwiftUI

struct NotchConversationButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 11, weight: .medium, design: .rounded))
            .foregroundStyle(enabled ? Color.cyan : .gray)
            .padding(.horizontal, 9).padding(.vertical, 6)
            .background(.cyan.opacity(enabled ? (configuration.isPressed ? 0.22 : 0.10) : 0.04),
                        in: RoundedRectangle(cornerRadius: 8))
            .contentShape(RoundedRectangle(cornerRadius: 8))
    }
}
