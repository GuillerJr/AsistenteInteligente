import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Computer; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func monitorComputerBridge() async {
        guard !computerBridgeMonitoring else { return }
        computerBridgeMonitoring = true
        defer { computerBridgeMonitoring = false }
        var retryMilliseconds = 250
        while !Task.isCancelled {
            guard
                userSessionAvailable,
                let executionPermit = userSessionExecutionGate.permit,
                daemonState == .online,
                securityState == .intact,
                let ipcSecret
            else {
                do {
                    try await Task.sleep(for: .milliseconds(500))
                } catch {
                    return
                }
                continue
            }
            let executionGate = userSessionExecutionGate
            let available = await Task.detached(priority: .utility) { @Sendable [ipcSecret] in
                Self.relayNextComputerCommand(
                    secret: ipcSecret,
                    executionGate: executionGate,
                    executionPermit: executionPermit
                )
            }.value
            guard !Task.isCancelled else { return }
            if available {
                retryMilliseconds = 250
                continue
            }
            do {
                try await Task.sleep(for: .milliseconds(retryMilliseconds))
            } catch {
                return
            }
            retryMilliseconds = min(retryMilliseconds * 2, 2_000)
        }
    }

    nonisolated static func reportTCCPermissionDenied(
        permission: TCCPrivacyPermission,
        operation: TCCPrivacyOperation,
        jobID: UUID?,
        secret: Data
    ) -> Bool {
        guard
            let client = try? LocalIPCClient(secret: secret),
            let response = try? client.reportTCCPermissionDenied(
                permission: permission,
                operation: operation,
                jobID: jobID
            )
        else {
            return false
        }
        return response.ok
    }

    nonisolated private static func relayNextComputerCommand(
        secret: Data,
        executionGate: UserSessionExecutionGate,
        executionPermit: UserSessionExecutionGate.Permit
    ) -> Bool {
        guard let client = try? LocalIPCClient(secret: secret) else { return false }
        guard
            let pending = try? client.waitForComputerCommand(),
            pending.ok,
            let available = pending.payload["available"] as? Bool
        else {
            return false
        }
        guard available else { return true }
        guard
            let commandIDText = pending.payload["command_id"] as? String,
            let commandID = UUID(uuidString: commandIDText),
            let command = pending.payload["command"] as? [String: Any]
        else {
            return false
        }
        let actionStartedAt = ProcessInfo.processInfo.systemUptime
        let helperResponse = ComputerControlService.execute(
            command: command,
            executionGate: executionGate,
            executionPermit: executionPermit
        ) ?? [
            "status": "error",
            "reason": executionGate.isCurrent(executionPermit)
                ? "computer_helper_failed"
                : "user_session_inactive",
        ]
        if command["command"] as? String == "capture",
           helperResponse["status"] as? String == "ok",
           let encoded = helperResponse["data_base64"] as? String,
           encoded.utf8.count <= 43_692,
           let image = Data(base64Encoded: encoded),
           (1 ... 32_768).contains(image.count) {
            let captureMilliseconds = max(
                0,
                Int(((ProcessInfo.processInfo.systemUptime - actionStartedAt) * 1_000).rounded())
            )
            Task.detached(priority: .utility) {
                try? await JarvisOperationalEvidenceRecorder.shared.recordScreenCapture(
                    milliseconds: captureMilliseconds,
                    payloadBytes: image.count
                )
            }
        }
        if command["command"] as? String == "act" {
            let verification = helperResponse["action_verification"] as? [String: Any]
            let verified = helperResponse["status"] as? String == "ok"
                && verification?["verified"] as? Bool == true
            let actionMilliseconds = max(
                0,
                Int(
                    ((ProcessInfo.processInfo.systemUptime - actionStartedAt) * 1_000)
                        .rounded()
                )
            )
            Task.detached(priority: .utility) {
                try? await JarvisOperationalEvidenceRecorder.shared.recordComputerAction(
                    verified: verified,
                    milliseconds: actionMilliseconds
                )
            }
        }
        if let pointerEvent = ComputerPointerEvent(
            command: command,
            response: helperResponse
        ) {
            DispatchQueue.main.async { @MainActor in
                if executionGate.isCurrent(executionPermit) {
                    JarvisPointerController.shared.present(pointerEvent, success: true)
                } else {
                    JarvisPointerController.shared.hide()
                }
            }
        }
        guard
            let completed = try? client.completeComputerCommand(
                commandID,
                response: helperResponse
            ),
            completed.ok,
            completed.payload["accepted"] as? Bool == true
        else {
            return false
        }
        return true
    }
}
