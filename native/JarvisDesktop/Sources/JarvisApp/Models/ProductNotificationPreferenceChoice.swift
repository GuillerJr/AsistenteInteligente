import DesktopCore

/// The values shown by one pair of controls, bound to the snapshot they came
/// from. A retained control cannot combine old flags with a newer CAS revision.
@MainActor struct ProductNotificationPreferenceChoice {
    let viewIdentity: String
    let revision: Int
    let enabled: Bool
    let silenced: Bool
    private let store: ProductInteractionStore

    init?(store: ProductInteractionStore) {
        guard let preferences = store.snapshot?.notificationPreferences else { return nil }
        self.store = store
        viewIdentity = store.viewIdentity
        revision = preferences.revision
        enabled = preferences.enabled
        silenced = preferences.silenced
    }

    func setEnabled(_ enabled: Bool) async {
        await store.configureNotifications(enabled: enabled, silenced: silenced,
            expectedIdentity: viewIdentity, expectedRevision: revision)
    }

    func setSilenced(_ silenced: Bool) async {
        await store.configureNotifications(enabled: enabled, silenced: silenced,
            expectedIdentity: viewIdentity, expectedRevision: revision)
    }
}
