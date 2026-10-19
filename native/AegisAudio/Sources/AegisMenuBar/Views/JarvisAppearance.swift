import AppKit
import SwiftUI

enum JarvisAppearance: String, CaseIterable {
    case light, dark, system
    static let storageKey = "jarvis.interface.appearance"

    var nativeAppearance: NSAppearance.Name? {
        switch self { case .light: .aqua; case .dark: .darkAqua; case .system: nil }
    }
}

/// A single application appearance keeps SwiftUI content, window chrome and
/// AppKit panels in sync. A nil override lets macOS update System mode live.
@MainActor
final class JarvisAppearanceController {
    static let shared = JarvisAppearanceController()
    private var application: NSApplication?
    private var defaultsObserver: NSObjectProtocol?
    private var applied: JarvisAppearance?

    private init() {}

    func start(application: NSApplication) {
        guard self.application == nil else { return }
        self.application = application
        defaultsObserver = NotificationCenter.default.addObserver(
            forName: UserDefaults.didChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.applySavedAppearance() }
        }
        applySavedAppearance()
    }

    func applySavedAppearance() {
        guard let application else { return }
        let appearance = UserDefaults.standard.string(forKey: JarvisAppearance.storageKey)
            .flatMap(JarvisAppearance.init(rawValue:)) ?? .dark
        guard applied != appearance else { return }
        applied = appearance
        application.appearance = appearance.nativeAppearance.flatMap { NSAppearance(named: $0) }
    }

    func stop() {
        if let defaultsObserver { NotificationCenter.default.removeObserver(defaultsObserver) }
        defaultsObserver = nil
        application = nil
        applied = nil
    }
}

struct JarvisAppearancePicker: View {
    @AppStorage(JarvisAppearance.storageKey) private var appearance = JarvisAppearance.dark.rawValue
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Aspecto", systemImage: "circle.lefthalf.filled")
                .font(.system(size: 12, weight: .medium))
            JarvisSegmentedPicker(title: "Aspecto de Jarvis", selection: Binding(
                get: { appearance },
                set: { value in
                    appearance = value
                    JarvisAppearanceController.shared.applySavedAppearance()
                }
            ), options: [
                .init(value: JarvisAppearance.light.rawValue, title: "Claro"),
                .init(value: JarvisAppearance.dark.rawValue, title: "Oscuro"),
                .init(value: JarvisAppearance.system.rawValue, title: "Sistema"),
            ])
        }
    }
}
