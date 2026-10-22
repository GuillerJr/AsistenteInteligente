import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Scheduled; observable storage remains in MenuBarModel.
extension MenuBarModel {
    /// Results of scheduled tasks ("cada mañana resúmeme mis correos") become Mac
    /// notifications; the full result is in the task's own conversation.
    func monitorScheduledDeliveries() async {
        guard !scheduledDeliveriesMonitoring else { return }
        scheduledDeliveriesMonitoring = true
        ScheduledNotificationRouter.shared.install(model: self)
        defer { scheduledDeliveriesMonitoring = false }
        // Only results delivered after launch: older ones were already shown or sent.
        var since = ISO8601DateFormatter().string(from: Date())
        var authorizationRequested = false
        while !Task.isCancelled {
            if userSessionAvailable, daemonState == .online, securityState == .intact,
               let ipcSecret {
                let cursor = since
                let deliveries = await Task.detached(priority: .utility) {
                    Self.scheduledDeliveries(since: cursor, secret: ipcSecret)
                }.value
                if !deliveries.isEmpty, !authorizationRequested {
                    authorizationRequested = true
                    _ = try? await UNUserNotificationCenter.current()
                        .requestAuthorization(options: [.alert, .sound])
                }
                for delivery in deliveries {
                    since = max(since, delivery.deliveredAt)
                    let content = UNMutableNotificationContent()
                    content.title = String(localized: "Jarvis · \(delivery.title)", bundle: .module)
                    content.body = String(delivery.text.prefix(600))
                    content.sound = .default
                    content.threadIdentifier = "ai.aegis.scheduled"
                    if let link = delivery.review {
                        content.categoryIdentifier = ScheduledNotificationRouter.category
                        content.userInfo = [
                            "job_id": link.jobID.uuidString.lowercased(),
                            "conversation_id": link.conversationID.uuidString.lowercased(),
                            "review_expires_at": ISO8601DateFormatter().string(from: link.expiresAt)
                        ]
                    }
                    try? await UNUserNotificationCenter.current().add(UNNotificationRequest(
                        identifier: "ai.aegis.scheduled.\(delivery.id)",
                        content: content, trigger: nil
                    ))
                    logger.info("scheduled_delivery_notified")
                }
            }
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
        }
    }

    private struct ScheduledDeliveryNotice: Sendable {
        let id: String
        let title: String
        let text: String
        let deliveredAt: String
        let review: ScheduledReviewLink?
    }

    func reviewScheduledApproval(_ link: ScheduledReviewLink) async {
        guard canManageChatHistory, link.expiresAt > Date(),
              let permit = userSessionExecutionGate.permit, let secret = ipcSecret else { return }
        let status = await Task.detached(priority: .userInitiated) {
            guard let response = try? LocalIPCClient(secret: secret).call(method: "jobs.status", payload: [
                "job_id": link.jobID.uuidString.lowercased()
            ]), response.payload["conversation_id"] as? String == link.conversationID.uuidString.lowercased()
            else { return Optional<IPCJobStatusEvent>.none }
            return IPCJobStatusEvent(response: response)
        }.value
        guard userSessionExecutionGate.isCurrent(permit), canManageChatHistory,
              let status, status.jobID == link.jobID, status.state == .awaitingConfirmation,
              let confirmation = status.confirmation, confirmation.expiresAt > Date() else {
            chat.error = String(localized: "Esa aprobación ya no está disponible. Revisa la tarea programada.", bundle: .module)
            return
        }
        // Read the task conversation before installing the existing pending approval.
        let entry = ChatConversationSummary(id: link.conversationID, title: String(localized: "Tarea programada", bundle: .module),
                                           updatedAt: "", archived: false)
        await chat.open(entry, model: self)
        guard userSessionExecutionGate.isCurrent(permit), canManageChatHistory,
              confirmation.expiresAt > Date() else { return }
        activeJobID = link.jobID
        handleJobOutcome(.awaitingConfirmation(confirmation), jobID: link.jobID)
    }

    nonisolated private static func scheduledDeliveries(
        since: String,
        secret: Data
    ) -> [ScheduledDeliveryNotice] {
        guard
            let response = try? LocalIPCClient(secret: secret).call(
                method: "schedules.deliveries", payload: ["since": since]
            ),
            response.ok,
            let items = response.payload["deliveries"] as? [[String: Any]]
        else { return [] }
        return items.compactMap { item in
            guard let id = item["delivery_id"] as? String,
                  let title = item["title"] as? String,
                  let text = item["text"] as? String,
                  let deliveredAt = item["delivered_at"] as? String
            else { return nil }
            return ScheduledDeliveryNotice(
                id: id, title: String(title.prefix(80)), text: text, deliveredAt: deliveredAt,
                review: ScheduledReviewLink(item)
            )
        }
    }
}
