import SwiftUI

enum JarvisAppearance: String, CaseIterable {
    case light, dark, system
    static let storageKey = "jarvis.interface.appearance"

    var colorScheme: ColorScheme? {
        switch self { case .light: .light; case .dark: .dark; case .system: nil }
    }
}

private struct JarvisAppearanceModifier: ViewModifier {
    @AppStorage(JarvisAppearance.storageKey) private var appearance = JarvisAppearance.dark.rawValue
    func body(content: Content) -> some View {
        content.preferredColorScheme((JarvisAppearance(rawValue: appearance) ?? .dark).colorScheme)
    }
}

struct JarvisAppearancePicker: View {
    @AppStorage(JarvisAppearance.storageKey) private var appearance = JarvisAppearance.dark.rawValue
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Aspecto", systemImage: "circle.lefthalf.filled")
                .font(.system(size: 12, weight: .medium))
            JarvisSegmentedPicker(title: "Aspecto de Jarvis", selection: $appearance, options: [
                .init(value: JarvisAppearance.light.rawValue, title: "Claro"),
                .init(value: JarvisAppearance.dark.rawValue, title: "Oscuro"),
                .init(value: JarvisAppearance.system.rawValue, title: "Sistema"),
            ])
        }
    }
}

extension View {
    func jarvisAppearance() -> some View { modifier(JarvisAppearanceModifier()) }
}
