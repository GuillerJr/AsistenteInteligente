import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Mascot; observable storage remains in MenuBarModel.
extension MenuBarModel {
    var mascotSessionAvailable: Bool { userSessionAvailable }

    var canPlanMascotIntent: Bool {
        userSessionAvailable && daemonState == .online && securityState == .intact
            && providerState == .configured && activeJobID == nil && !chat.isBusy
            && !voiceState.isBusy && interactionID == nil && ipcSecret != nil
    }

    /// Companion traffic uses independent, short IPC calls and never chat errors/jobs.
    func performMascotIPC(_ method: String, payload: [String: Any]) async -> Data? {
        guard userSessionAvailable, let permit = userSessionExecutionGate.permit,
              let secret = ipcSecret,
              let encoded = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        let result = await Task.detached(priority: .utility) { () -> Data? in
            guard let decoded = try? JSONSerialization.jsonObject(with: encoded) as? [String: Any],
                  let response = try? LocalIPCClient(secret: secret, timeoutSeconds: 2)
                    .call(method: method, payload: decoded), response.ok else { return nil }
            return try? JSONSerialization.data(withJSONObject: response.payload)
        }.value
        guard userSessionExecutionGate.isCurrent(permit), !Task.isCancelled else { return nil }
        return result
    }

    var canActivateMascotVoice: Bool {
        userSessionAvailable && !chat.isEditing && pendingApproval == nil
            && pendingBrowserSelection == nil && pendingRecovery == nil
            && (canStartVoiceTurn || voiceState == .speaking || voiceState == .processing
                || voiceState == .followingUp)
    }

    func cancelMascotListening() {
        guard userSessionAvailable, voiceState == .listening || voiceState == .followingUp,
              !mascotCancellingCapture else { return }
        cancelFollowUpWindow()
        if let activeTranscriber {
            mascotCancellingCapture = true
            activeTranscriber.cancel()
        } else {
            voiceState = .idle
            scheduleWakeWordResume(if: wakeWordOptedIn)
        }
    }

    private func configureMascotAlerts() {
        proactiveEventMonitor.onAlert = { [weak self] message in
            guard let self, userSessionAvailable else { return }
            mascotNoticeTask?.cancel()
            mascotNotice = String(message.prefix(512))
            mascotNoticeTask = Task { @MainActor [weak self] in
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                self?.mascotNotice = nil
                self?.mascotNoticeTask = nil
            }
        }
    }

    func initializeProactiveAlerts() async {
        configureMascotAlerts()
        guard proactiveAlertsEnabled else { return }
        proactiveAlertsEnabled = await proactiveEventMonitor.setEnabled(true)
        UserDefaults.standard.set(
            proactiveAlertsEnabled,
            forKey: proactiveAlertsDefaultsKey
        )
    }

    func setProactiveAlertsEnabled(_ enabled: Bool) async {
        configureMascotAlerts()
        if !enabled {
            mascotNoticeTask?.cancel()
            mascotNoticeTask = nil
            mascotNotice = nil
        }
        proactiveAlertsEnabled = await proactiveEventMonitor.setEnabled(enabled)
        UserDefaults.standard.set(
            proactiveAlertsEnabled,
            forKey: proactiveAlertsDefaultsKey
        )
    }

    /// Dot reacts when something new needs the owner: an approval waiting in Telegram,
    /// a scheduled task that could not finish, a blocked goal. It only reads the list.
    func monitorAttention() async {
        guard !attentionMonitoring else { return }
        attentionMonitoring = true
        defer { attentionMonitoring = false }
        while !Task.isCancelled {
            if userSessionAvailable, daemonState == .online, securityState == .intact,
               let ipcSecret {
                let entries = await Task.detached(priority: .utility) {
                    Self.attentionEntries(secret: ipcSecret)
                }.value
                if let entries {
                    let grew = entries.count > pendingAttentionCount
                    pendingAttentionCount = entries.count
                    chat.attention = entries
                    if grew { announcePending(entries) }
                }
            }
            do { try await Task.sleep(for: .seconds(30)) } catch { return }
        }
    }

    private func announcePending(_ entries: [JarvisChatStore.AttentionEntry]) {
        let approvals = entries.filter { $0.action == "review_approval" }.count
        let message = approvals > 0
            ? (approvals == 1 ? String(localized: "Una acción espera tu aprobación.", bundle: .module) : String(localized: "\(approvals) acciones esperan tu aprobación.", bundle: .module))
            : (entries.count == 1 ? String(localized: "Hay algo pendiente para ti.", bundle: .module) : String(localized: "Hay \(entries.count) cosas pendientes para ti.", bundle: .module))
        mascotNoticeTask?.cancel()
        mascotNotice = message
        mascotNoticeTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(12)) } catch { return }
            self?.mascotNotice = nil
            self?.mascotNoticeTask = nil
        }
    }

    nonisolated private static func attentionEntries(secret: Data) -> [JarvisChatStore.AttentionEntry]? {
        guard let response = try? LocalIPCClient(secret: secret).call(method: "attention.list", payload: [:]),
              response.ok, let items = response.payload["items"] as? [[String: Any]]
        else { return nil }
        return JarvisChatStore.attentionEntries(items)
    }
}
