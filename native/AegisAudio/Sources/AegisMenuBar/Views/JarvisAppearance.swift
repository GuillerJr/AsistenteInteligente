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
            Picker("Aspecto de Jarvis", selection: $appearance) {
                Text("Claro").tag(JarvisAppearance.light.rawValue)
                Text("Oscuro").tag(JarvisAppearance.dark.rawValue)
                Text("Sistema").tag(JarvisAppearance.system.rawValue)
            }.pickerStyle(.segmented).labelsHidden().accessibilityLabel("Aspecto de Jarvis")
        }
    }
}

extension View {
    func jarvisAppearance() -> some View { modifier(JarvisAppearanceModifier()) }
}
