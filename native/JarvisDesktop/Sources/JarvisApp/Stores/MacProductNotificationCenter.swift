import DesktopCore
import Foundation
import OSLog
import UserNotifications

/// No permission request at construction or during polling. The center is lazy
/// so source-level app tests do not need a running signed application bundle.
@MainActor final class MacProductNotificationCenter: NSObject, ProductNotificationCenter, UNUserNotificationCenterDelegate {
    var onActivation: (@MainActor (String) async -> Void)?
    private lazy var center = UNUserNotificationCenter.current()
    private let logger = Logger(subsystem: "local.jarvis", category: "ProductNotifications")
    private var hasApplicationBundle: Bool {
        Bundle.main.bundleURL.pathExtension.lowercased() == "app" && Bundle.main.bundleIdentifier != nil
    }
    private func configuredCenter() -> UNUserNotificationCenter { center.delegate = self; return center }
    func requestPermission() async throws -> Bool {
        guard hasApplicationBundle else { return false }
        do {
            return try await configuredCenter().requestAuthorization(options: [.alert])
        } catch {
            let failure = error as NSError
            let domain = [UNErrorDomain, NSCocoaErrorDomain, NSPOSIXErrorDomain].contains(failure.domain)
                ? failure.domain : "other"
            // No descriptions, userInfo, paths, notification bodies or account data.
            logger.error("Notification authorization failed domain=\(domain, privacy: .public) code=\(failure.code, privacy: .public)")
            throw error
        }
    }
    func canDeliver() async -> Bool {
        guard hasApplicationBundle else { return false }
        let settings = await configuredCenter().notificationSettings()
        return settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional
    }
    func existingIdentifiers(namespace: String) async -> [String] {
        guard hasApplicationBundle else { return [] }
        let pending = await configuredCenter().pendingNotificationRequests()
        let delivered = await configuredCenter().deliveredNotifications()
        let own = (pending + delivered.map(\.request)).filter {
            $0.identifier.hasPrefix("jarvis.product.")
                && ($0.content.userInfo["jarvis_profile_namespace"] as? String) == namespace
        }
        return Array(Set(own.map(\.identifier)))
    }
    func add(identifier: String, title: String, body: String, namespace: String) async throws {
        guard hasApplicationBundle else { throw RPCFailure.unavailable }
        let content = UNMutableNotificationContent()
        content.title = title; content.body = body
        content.userInfo = ["jarvis_notification_id": identifier, "jarvis_profile_namespace": namespace]
        try await configuredCenter().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
    }
    func remove(identifiers: [String]) {
        guard hasApplicationBundle, !identifiers.isEmpty else { return }
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
