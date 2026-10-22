import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Runtime; observable storage remains in MenuBarModel.
extension MenuBarModel {
    var hybridBrainReady: Bool {
        providerState == .configured
    }

    func startPowerMonitoring() {
        guard powerObservers.isEmpty else { return }
        thermalMonitor.start { [weak self] snapshot in
            self?.synchronizeDaemonRuntimeState(
                trigger: snapshot.transitionCause,
                snapshot: snapshot
            )
        }
        wakeWordDetector.monitorThermalState { [weak self] event in
            Task { @MainActor [weak self] in
                self?.reconcileWakeWordThermalState(event)
            }
        }
        let center = NSWorkspace.shared.notificationCenter
        powerObservers = [
            center.addObserver(
                forName: NSWorkspace.willSleepNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.suspendWakeWordForSystemSleep()
                }
            },
            center.addObserver(
                forName: NSWorkspace.didWakeNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.resumeWakeWordAfterSystemWake()
                }
            },
            center.addObserver(
                forName: NSWorkspace.sessionDidResignActiveNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    await self?.suspendForUserSessionLock()
                }
            },
            center.addObserver(
                forName: NSWorkspace.sessionDidBecomeActiveNotification,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.resumeAfterUserSessionUnlock()
                }
            },
            NotificationCenter.default.addObserver(
                forName: .NSProcessInfoPowerStateDidChange,
                object: ProcessInfo.processInfo,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.reconcileWakeWordEnergyState()
                }
            },
        ]
    }

    func initializeOperationalEvidence() async {
        do {
            _ = try await JarvisOperationalEvidenceRecorder.shared.current()
        } catch {
            logger.error("operational_evidence_unavailable")
        }
    }

    func registerBundledDaemonLaunchFailure() {
        daemonState = .securityFailure
        securityState = .unavailable
        voiceState = .failed
        securityLogger.fault("bundled_daemon_launch_failed")
    }

    func monitor() async {
        guard !monitoring else {
            return
        }
        monitoring = true
        defer { monitoring = false }
        while !Task.isCancelled {
            if userSessionAvailable {
                let previousMicrophonePermission = microphonePermission
                refreshPermissions()
                reconcileWakeWordPermission(from: previousMicrophonePermission)
            }
            let previousRuntimeAvailable = wakeWordRuntimeAvailable
            await refreshDaemon()
            reconcileWakeWordRuntime(from: previousRuntimeAvailable)
            do {
                try await Task.sleep(for: .seconds(10))
            } catch {
                return
            }
        }
    }

    func runBackgroundMonitoring() async {
        async let runtime: Void = monitor()
        async let swarm: Void = monitorSwarmActivity()
        async let computer: Void = monitorComputerBridge()
        async let scheduled: Void = monitorScheduledDeliveries()
        async let attention: Void = monitorAttention()
        _ = await (runtime, swarm, computer, scheduled, attention)
    }

    /// The health panel's report, read without touching the chat's error banner.
    func healthReport() async -> JarvisHealthReport? {
        guard userSessionAvailable, let ipcSecret else { return nil }
        return await Task.detached(priority: .userInitiated) {
            guard let response = try? LocalIPCClient(secret: ipcSecret).call(
                method: "health.status", payload: [:]), response.ok,
                  let data = try? JSONSerialization.data(withJSONObject: response.payload)
            else { return nil }
            return try? JSONDecoder().decode(JarvisHealthReport.self, from: data)
        }.value
    }

    func refreshDaemon() async {
        guard !runtimeProbeInProgress else { return }
        runtimeProbeInProgress = true
        defer { runtimeProbeInProgress = false }
        let previousDaemonState = daemonState
        let previousSecurityState = securityState
        if daemonState == .unknown {
            daemonState = .checking
        }
        if securityState == .unknown {
            securityState = .checking
        }
        if providerState == .unknown {
            providerState = .checking
        }
        let result = await Task.detached(priority: .utility) { [ipcSecret] in
            Self.probeDaemon(cachedSecret: ipcSecret)
        }.value
        daemonState = result.state
        if previousSecurityState != result.security {
            securityLogger.info(
                "audit_integrity_changed state=\(result.security.rawValue, privacy: .public)"
            )
        }
        securityState = result.security
        providerState = result.provider
        localBrainAvailable = result.localBrainAvailable
        ipcSecret = result.secret
        persistReadinessSnapshot()
        let currentPower = ThermalMonitor.currentSnapshot()
        if
            result.state == .online,
            result.security == .intact,
            previousDaemonState != .online || !result.runtimePowerStateKnown
                || result.runtimePower?.matches(
                    thermalState: currentPower.thermalState,
                    lowPowerMode: currentPower.lowPowerMode,
                    powerSource: currentPower.powerSource
                ) != true
        {
            // Reconcile a missed power notification or failed IPC delivery using
            // a fresh OS observation; never infer recovery from daemon connectivity.
            synchronizeDaemonRuntimeState(trigger: currentPower.transitionCause, snapshot: currentPower)
        }
    }

    private func monitorSwarmActivity() async {
        guard !swarmMonitoring else {
            return
        }
        swarmMonitoring = true
        var version = 0
        var retryMilliseconds = 250
        defer {
            hudActivity = [:]
            swarmMonitoring = false
        }
        while !Task.isCancelled {
            guard
                daemonState == .online,
                securityState == .intact,
                let ipcSecret
            else {
                hudActivity = [:]
                version = 0
                do {
                    try await Task.sleep(for: .milliseconds(500))
                } catch {
                    return
                }
                continue
            }

            let update = await Task.detached(priority: .utility) { @Sendable [
                version,
                ipcSecret,
            ] in
                Self.fetchSwarmUpdate(afterVersion: version, secret: ipcSecret)
            }.value
            guard !Task.isCancelled else { return }
            guard daemonState == .online, securityState == .intact else {
                hudActivity = [:]
                continue
            }
            if let update {
                version = update.version
                hudActivity = Dictionary(
                    uniqueKeysWithValues: update.agents.map { ($0.role, $0.activeJobs) }
                )
                retryMilliseconds = 250
                if update.changed {
                    swarmLogger.debug(
                        "activity_changed version=\(update.version, privacy: .public) agents=\(update.agents.count, privacy: .public)"
                    )
                }
                continue
            }

            hudActivity = [:]
            do {
                try await Task.sleep(for: .milliseconds(retryMilliseconds))
            } catch {
                return
            }
            retryMilliseconds = min(retryMilliseconds * 2, 5_000)
        }
    }

    nonisolated private static func probeDaemon(cachedSecret: Data?) -> ProbeResult {
        var resolvedSecret = cachedSecret
        do {
            if resolvedSecret == nil {
                resolvedSecret = try MacOSIPCSecretStore().get()
            }
            guard let resolvedSecret else {
                return ProbeResult(state: .offline, security: .unavailable, secret: nil)
            }
            let client = try LocalIPCClient(secret: resolvedSecret)
            let response = try client.runtimePreflight()
            if !response.ok, response.errorCode == "security_compromised" {
                let securityResponse = try client.securityStatus()
                let compromised = IPCSecurityStatusEvent(response: securityResponse)?.integrity
                    == .compromised
                return ProbeResult(
                    state: compromised ? .online : .securityFailure,
                    security: compromised ? .compromised : .unavailable,
                    secret: resolvedSecret
                )
            }
            let state: DaemonConnectionState =
                response.ok && response.payload["status"] as? String == "ok"
                ? .online : .offline
            guard state == .online else {
                return ProbeResult(
                    state: state,
                    security: .unavailable,
                    secret: resolvedSecret
                )
            }
            if cachedSecret == nil {
                // Pin this app's approval key as soon as the daemon is reachable so no
                // other local process can register first. Idempotent for the same key.
                _ = try? client.registerApprovalSigner()
            }
            let providerEvent = IPCProviderStatusEvent(response: response)
            let runtimePowerStateKnown =
                response.payload["runtime_power_state_known"] as? Bool ?? false
            let runtimePower = (try? client.call(method: "runtime.power.status"))
                .flatMap { IPCRuntimePowerStatus(response: $0) }
            let provider = providerEvent
                .map { ProviderReadinessState(rawValue: $0.credential.rawValue) ?? .unavailable }
                ?? .unavailable
            guard let security = IPCSecurityStatusEvent(response: response) else {
                return ProbeResult(
                    state: state,
                    security: .compromised,
                    provider: provider,
                    localBrainAvailable: providerEvent?.localModel == .available,
                    runtimePowerStateKnown: runtimePowerStateKnown,
                    secret: resolvedSecret
                )
            }
            return ProbeResult(
                state: state,
                security: security.integrity == .intact ? .intact : .compromised,
                provider: provider,
                localBrainAvailable: providerEvent?.localModel == .available,
                runtimePowerStateKnown: runtimePowerStateKnown,
                runtimePower: runtimePower,
                secret: resolvedSecret
            )
        } catch let error as LocalIPCError {
            switch error {
            case .invalidCredential, .responseMismatch, .responseAuthenticationFailed:
                return ProbeResult(state: .securityFailure, security: .unavailable, secret: nil)
            case .unsafeSocket, .staleResponse, .malformedResponse:
                return ProbeResult(
                    state: .securityFailure,
                    security: .unavailable,
                    secret: resolvedSecret
                )
            default:
                return ProbeResult(
                    state: .offline,
                    security: .unavailable,
                    secret: resolvedSecret
                )
            }
        } catch {
            return ProbeResult(
                state: .offline,
                security: .unavailable,
                secret: resolvedSecret
            )
        }
    }

    nonisolated private static func fetchSwarmUpdate(
        afterVersion: Int,
        secret: Data
    ) -> IPCSwarmActivityUpdate? {
        guard
            let response = try? LocalIPCClient(secret: secret).waitForSwarmActivity(
                afterVersion: afterVersion
            )
        else {
            return nil
        }
        return IPCSwarmActivityUpdate(response: response)
    }

    private struct ProbeResult: Sendable {
        let state: DaemonConnectionState
        let security: SecurityMonitorState
        let provider: ProviderReadinessState
        let localBrainAvailable: Bool
        let runtimePowerStateKnown: Bool
        let runtimePower: IPCRuntimePowerStatus?
        let secret: Data?

        init(
            state: DaemonConnectionState,
            security: SecurityMonitorState,
            provider: ProviderReadinessState = .unknown,
            localBrainAvailable: Bool = false,
            runtimePowerStateKnown: Bool = false,
            runtimePower: IPCRuntimePowerStatus? = nil,
            secret: Data?
        ) {
            self.state = state
            self.security = security
            self.provider = provider
            self.localBrainAvailable = localBrainAvailable
            self.runtimePowerStateKnown = runtimePowerStateKnown
            self.runtimePower = runtimePower
            self.secret = secret
        }
    }
}
