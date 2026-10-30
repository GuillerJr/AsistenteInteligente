import DesktopCore
import Foundation
import UserNotifications

/// No permission request at construction or during polling. The center is lazy
/// so source-level app tests do not need a running signed application bundle.
@MainActor final class MacProductNotificationCenter: NSObject, ProductNotificationCenter, UNUserNotificationCenterDelegate {
    var onActivation: (@MainActor (String) async -> Void)?
    private lazy var center = UNUserNotificationCenter.current()
    private func configuredCenter() -> UNUserNotificationCenter { center.delegate = self; return center }
    func requestPermission() async throws -> Bool {
        try await configuredCenter().requestAuthorization(options: [.alert])
    }
    func canDeliver() async -> Bool {
        let settings = await configuredCenter().notificationSettings()
        return settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional
    }
    func existingIdentifiers(namespace: String) async -> [String] {
        let pending = await configuredCenter().pendingNotificationRequests()
        let delivered = await configuredCenter().deliveredNotifications()
        let own = (pending + delivered.map(\.request)).filter {
            $0.identifier.hasPrefix("jarvis.product.")
                && ($0.content.userInfo["jarvis_profile_namespace"] as? String) == namespace
        }
        return Array(Set(own.map(\.identifier)))
    }
    func add(identifier: String, title: String, body: String, namespace: String) async throws {
        let content = UNMutableNotificationContent()
        content.title = title; content.body = body
        content.userInfo = ["jarvis_notification_id": identifier, "jarvis_profile_namespace": namespace]
        try await configuredCenter().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
    }
    func remove(identifiers: [String]) {
        guard !identifiers.isEmpty else { return }
        configuredCenter().removePendingNotificationRequests(withIdentifiers: identifiers)
        configuredCenter().removeDeliveredNotifications(withIdentifiers: identifiers)
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == UNNotificationDefaultActionIdentifier {
            let id = response.notification.request.identifier
            Task { @MainActor [weak self] in await self?.onActivation?(id) }
        }
        completionHandler()
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }
}
