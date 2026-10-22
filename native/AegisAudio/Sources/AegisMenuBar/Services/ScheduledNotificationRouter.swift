import Foundation
@preconcurrency import UserNotifications

struct ScheduledReviewLink: Equatable, Sendable {
    let jobID: UUID
    let conversationID: UUID
    let expiresAt: Date

    init?(_ info: [AnyHashable: Any], now: Date = Date()) {
        guard let job = info["job_id"] as? String, let jobID = UUID(uuidString: job),
              let conversation = info["conversation_id"] as? String,
              let conversationID = UUID(uuidString: conversation),
              let rawExpiry = info["review_expires_at"] as? String else { return nil }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let expiry = fractional.date(from: rawExpiry) ?? ISO8601DateFormatter().date(from: rawExpiry),
              expiry > now else { return nil }
        self.jobID = jobID; self.conversationID = conversationID; self.expiresAt = expiry
    }
}

/// Review is navigation only. The normal signed approval UI still owns the decision.
final class ScheduledNotificationRouter: NSObject, UNUserNotificationCenterDelegate, @unchecked Sendable {
    static let shared = ScheduledNotificationRouter()
    static let category = "ai.aegis.scheduled.review"
    static let reviewAction = "review"
    @MainActor private weak var model: MenuBarModel?

    @MainActor func install(model: MenuBarModel) {
        self.model = model
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.getNotificationCategories { existing in
            var categories = existing.filter { $0.identifier != Self.category }
            categories.insert(UNNotificationCategory(identifier: Self.category, actions: [
                UNNotificationAction(identifier: Self.reviewAction, title: "Revisar", options: [.foreground])
            ], intentIdentifiers: []))
            center.setNotificationCategories(categories)
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        guard response.notification.request.content.categoryIdentifier == Self.category,
              [Self.reviewAction, UNNotificationDefaultActionIdentifier].contains(response.actionIdentifier),
              let link = ScheduledReviewLink(response.notification.request.content.userInfo) else {
            completionHandler(); return
        }
        Task { @MainActor [weak self] in await self?.model?.reviewScheduledApproval(link) }
        completionHandler()
    }
}
