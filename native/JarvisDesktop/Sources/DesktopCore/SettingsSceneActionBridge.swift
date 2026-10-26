import Foundation
import SwiftUI

/// A mounted scene supplies its public SwiftUI action. No window is retained.
@MainActor public final class SettingsSceneActionRegistration {
    private var owner: UUID?
    private var action: (() -> Void)?

    public init() {}

    public func install(owner: UUID, action: @escaping () -> Void) {
        self.owner = owner
        self.action = action
    }

    public func remove(owner: UUID) {
        guard self.owner == owner else { return }
        self.owner = nil
        action = nil
    }

    @discardableResult public func perform() -> Bool {
        guard let action else { return false }
        action()
        return true
    }
}

/// Environment actions must be read inside the WindowGroup's mounted view
/// hierarchy; reading openSettings from the App value supplies no scene context.
@MainActor public struct SettingsSceneActionBridge: View {
    @Environment(\.openSettings) private var openSettings
    @State private var owner = UUID()
    private let registration: SettingsSceneActionRegistration

    public init(registration: SettingsSceneActionRegistration) {
        self.registration = registration
    }

    public var body: some View {
        Color.clear.frame(width: 0, height: 0)
            .onAppear {
                let action = openSettings
                registration.install(owner: owner) { action() }
            }
            .onDisappear { registration.remove(owner: owner) }
    }
}
