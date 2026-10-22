import SwiftUI

struct JarvisSidebarSectionHeader: View {
    let title: String
    @Binding var expanded: Bool

    var body: some View {
        Button { expanded.toggle() } label: {
            HStack(spacing: 6) {
                Image(systemName: expanded ? "chevron.down" : "chevron.right")
                    .font(.system(size: 9, weight: .semibold)).frame(width: 10)
                    .accessibilityHidden(true)
                Text(verbatim: title).font(.system(size: 11, weight: .medium))
                Spacer(minLength: 0)
            }
            .foregroundStyle(JarvisTheme.inkMuted)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.leading, 10).frame(minHeight: 30)
        .accessibilityLabel(Text(verbatim: title))
        .accessibilityValue(expanded ? String(localized: "Expandido", bundle: .module)
                                    : String(localized: "Contraído", bundle: .module))
        .accessibilityHint(expanded ? String(localized: "Ocultar sección", bundle: .module)
                                   : String(localized: "Mostrar sección", bundle: .module))
    }
}
