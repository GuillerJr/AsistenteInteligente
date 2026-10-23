import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Permissions; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func startPrivacyChangeMonitoring() {
        guard privacyObservers.isEmpty else { return }
        let center = NSWorkspace.shared.notificationCenter
        privacyObservers = [
            center.addObserver(
                forName: NSWorkspace.didDeactivateApplicationNotification,
                object: nil,
                queue: .main
            ) { [weak self] notification in
                let bundleIdentifier = (
                    notification.userInfo?[NSWorkspace.applicationUserInfoKey]
                        as? NSRunningApplication
                )?.bundleIdentifier
                Task { @MainActor [weak self] in
                    guard bundleIdentifier == systemSettingsBundleIdentifier else { return }
                    self?.relaunchAfterPrivacyChangeIfNeeded()
                }
            },
        ]
    }

    func requestMicrophone() async {
        guard MicrophonePermission.current == .notDetermined else {
            openPrivacySettings("Privacy_Microphone")
            beginPrivacyRefresh(for: .microphone)
            return
        }
        let previousMicrophonePermission = microphonePermission
        activateForPermissionPrompt()
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { _ in
                continuation.resume()
            }
        }
        refreshPermissions()
        reconcileWakeWordPermission(from: previousMicrophonePermission)
        beginPrivacyRefresh(for: .microphone)
    }

    func requestSpeechRecognition() async {
        guard SpeechRecognitionPermission.current == .notDetermined else {
            openPrivacySettings("Privacy_SpeechRecognition")
            beginPrivacyRefresh(for: .speech)
            return
        }
        activateForPermissionPrompt()
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { _ in
                continuation.resume()
            }
        }
        refreshPermissions()
        beginPrivacyRefresh(for: .speech)
    }

    func requestScreenCapture() {
        guard !ScreenCaptureService.isAuthorized else {
            refreshPermissions()
            return
        }
        let defaults = UserDefaults.standard
        if defaults.bool(forKey: screenCaptureRequestDefaultsKey) {
            openPrivacySettings("Privacy_ScreenCapture")
            beginPrivacyRefresh(for: .screenCapture)
            return
        }
        defaults.set(true, forKey: screenCaptureRequestDefaultsKey)
        activateForPermissionPrompt()
        _ = ScreenCaptureService.requestAccess()
        screenCaptureAuthorized = ScreenCaptureService.isAuthorized
        beginPrivacyRefresh(for: .screenCapture)
    }

    func requestComputerControlAccess() async {
        await refreshComputerControlCapability()
        guard let request = computerControlCapability.nextPermissionRequest else { return }
        armRelaunchAfterPrivacyChange()
        activateForPermissionPrompt()
        ComputerControlService.requestPermission(request)
        beginPrivacyRefresh(for: .computerControl)
        do {
            try await Task.sleep(for: .seconds(1))
        } catch {
            return
        }
        await refreshComputerControlCapability()
    }

    func refreshComputerControlCapability() async {
        let capability = await Task.detached(priority: .utility) {
            ComputerControlService.inspect()
        }.value
        guard capability != computerControlCapability else { return }
        computerControlCapability = capability
        computerControlLogger.info(
            "capability_changed state=\(String(describing: capability), privacy: .public)"
        )
        persistReadinessSnapshot()
    }

    func refreshPrivacyCapabilities() async {
        refreshPermissions()
        await refreshComputerControlCapability()
        logPrivacyCapabilities(event: "refreshed")
    }

    func requestUndeterminedPermissions() async {
        if MicrophonePermission.current == .notDetermined {
            await requestMicrophone()
        }
        if SpeechRecognitionPermission.current == .notDetermined {
            await requestSpeechRecognition()
        }
    }

    func requestAllPrivacyPermissions() async {
        if MicrophonePermission.current != .authorized {
            await requestMicrophone()
            guard MicrophonePermission.current == .authorized else { return }
        }
        if SpeechRecognitionPermission.current != .authorized {
            await requestSpeechRecognition()
            guard SpeechRecognitionPermission.current == .authorized else { return }
        }
        if !ScreenCaptureService.isAuthorized {
            requestScreenCapture()
            guard ScreenCaptureService.isAuthorized else { return }
        }
        await requestComputerControlAccess()
    }

    func openMicrophoneSettings() {
        openPrivacySettings("Privacy_Microphone")
    }

    func openSpeechSettings() {
        openPrivacySettings("Privacy_SpeechRecognition")
    }

    func refreshPermissions() {
        microphonePermission = .current
        speechPermission = .current
        screenCaptureAuthorized = ScreenCaptureService.isAuthorized
        persistReadinessSnapshot()
    }

    func persistReadinessSnapshot() {
        do {
            try JarvisReadinessStore.persist(
                JarvisReadinessSnapshot(
                    daemon: daemonReadinessValue,
                    security: securityState.rawValue,
                    provider: providerState.rawValue,
                    localBrainAvailable: localBrainAvailable,
                    microphone: microphonePermission.rawValue,
                    speechRecognition: speechPermission.rawValue,
                    screenCaptureAuthorized: screenCaptureAuthorized,
                    computerControl: computerControlReadinessValue,
                    wakeWord: wakeWordCapability.rawValue,
                    wakeWordEnabled: wakeWordOptedIn,
                    speakerIdentity: speakerIdentityCapability.rawValue,
                    voiceOutput: voiceOutputState.rawValue
                )
            )
        } catch {
            privacyLogger.error("readiness_snapshot_write_failed")
        }
    }

    private var daemonReadinessValue: String {
        switch daemonState {
        case .unknown: "unknown"
        case .checking: "checking"
        case .online: "online"
        case .offline: "offline"
        case .securityFailure: "security_failure"
        }
    }

    private var computerControlReadinessValue: String {
        switch computerControlCapability {
        case .ready: "ready"
        case .screenCaptureMissing: "screen_capture_missing"
        case .accessibilityMissing: "accessibility_missing"
        case .permissionsMissing: "permissions_missing"
        case .helperUnavailable: "helper_unavailable"
        }
    }

    private func activateForPermissionPrompt() {
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func logPrivacyCapabilities(event: String) {
        privacyLogger.info(
            "privacy_\(event, privacy: .public) microphone=\(self.microphonePermission.rawValue, privacy: .public) speech=\(self.speechPermission.rawValue, privacy: .public) screen=\(self.screenCaptureAuthorized, privacy: .public) control=\(String(describing: self.computerControlCapability), privacy: .public)"
        )
    }

    private func openPrivacySettings(_ pane: String) {
        guard let url = URL(
            string: "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?\(pane)"
        ) else {
            return
        }
        armRelaunchAfterPrivacyChange()
        NSWorkspace.shared.open(url)
    }

    private func beginPrivacyRefresh(for target: PrivacyRefreshTarget) {
        privacyRefreshTask?.cancel()
        privacyRefreshTask = Task { @MainActor [weak self] in
            guard let self else { return }
            for _ in 0 ..< 60 {
                guard !Task.isCancelled else { return }
                let previousMicrophonePermission = microphonePermission
                refreshPermissions()
                reconcileWakeWordPermission(from: previousMicrophonePermission)
                if case .computerControl = target {
                    await refreshComputerControlCapability()
                }
                let finished = switch target {
                case .microphone:
                    microphonePermission != .notDetermined
                case .speech:
                    speechPermission != .notDetermined
                case .screenCapture:
                    screenCaptureAuthorized
                case .computerControl:
                    computerControlCapability == .ready
                }
                if finished { return }
                do {
                    try await Task.sleep(for: .seconds(1))
                } catch {
                    return
                }
            }
        }
    }

    private func armRelaunchAfterPrivacyChange() {
        privacyRelaunchPending = true
    }

    private func relaunchAfterPrivacyChangeIfNeeded() {
        guard
            privacyRelaunchPending,
            !privacyRelaunchInProgress
        else { return }
        privacyRelaunchPending = false
        privacyRelaunchInProgress = true
        privacyRefreshTask?.cancel()

        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.createsNewApplicationInstance = true
        let currentProcessIdentifier = ProcessInfo.processInfo.processIdentifier
        NSWorkspace.shared.openApplication(
            at: Bundle.main.bundleURL,
            configuration: configuration
        ) { [weak self] application, _ in
            Task { @MainActor [weak self] in
                guard
                    let application,
                    application.processIdentifier != currentProcessIdentifier
                else {
                    self?.privacyRelaunchInProgress = false
                    return
                }
                do {
                    try await Task.sleep(for: .milliseconds(500))
                } catch {
                    self?.privacyRelaunchInProgress = false
                    return
                }
                guard !application.isTerminated else {
                    self?.privacyRelaunchInProgress = false
                    return
                }
                NSApplication.shared.terminate(nil)
            }
        }
    }
}
