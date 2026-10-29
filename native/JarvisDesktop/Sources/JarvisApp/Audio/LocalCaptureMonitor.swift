@preconcurrency import AVFoundation
import AppKit
import DesktopCore
import Foundation
@preconcurrency import Speech

/// Capture stops on revocation, device/configuration changes, loss of focus or
/// system sleep. No event can restart a microphone; a new explicit action is
/// required. Injected centers/clock keep the negative contract testable without
/// opening hardware or requesting system permissions.
@MainActor final class LocalCaptureMonitor {
    private let center: NotificationCenter
    private let workspaceCenter: NotificationCenter
    private let clock: any LocalVoiceClock
    private let permissionIssue: @MainActor () -> LocalVoiceIssue?
    private let failed: @MainActor (LocalVoiceIssue) -> Void
    private var tokens: [(NotificationCenter, NSObjectProtocol)] = []
    private var timer: (any LocalVoiceCancellation)?
    private var active = false
    private var generation: UInt64 = 0

    init(engine: AnyObject, center: NotificationCenter = .default,
         workspaceCenter: NotificationCenter? = nil,
         clock: (any LocalVoiceClock)? = nil,
         permissionIssue: @escaping @MainActor () -> LocalVoiceIssue?,
         failed: @escaping @MainActor (LocalVoiceIssue) -> Void) {
        let workspaceCenter = workspaceCenter ?? NSWorkspace.shared.notificationCenter
        self.center = center; self.workspaceCenter = workspaceCenter
        self.clock = clock ?? SystemLocalVoiceClock()
        self.permissionIssue = permissionIssue; self.failed = failed
        observe(center, name: .AVAudioEngineConfigurationChange, object: engine, issue: .inputChanged)
        observe(center, name: AVCaptureDevice.wasDisconnectedNotification, object: nil, issue: .inputChanged, audioOnly: true)
        observe(center, name: NSApplication.didResignActiveNotification, object: nil, issue: .interrupted)
        observe(workspaceCenter, name: NSWorkspace.willSleepNotification, object: nil, issue: .interrupted)
        observe(workspaceCenter, name: NSWorkspace.sessionDidResignActiveNotification, object: nil, issue: .interrupted)
    }

    func start() {
        guard !active else { return }
        active = true; generation &+= 1
        inspect(generation)
    }

    func stop() {
        active = false; generation &+= 1
        timer?.cancel(); timer = nil
        for (center, token) in tokens { center.removeObserver(token) }
        tokens = []
    }

    deinit {
        for (center, token) in tokens { center.removeObserver(token) }
        let timer = timer
        Task { @MainActor in timer?.cancel() }
    }

    private func observe(_ center: NotificationCenter, name: Notification.Name, object: AnyObject?,
                         issue: LocalVoiceIssue, audioOnly: Bool = false) {
        let token = center.addObserver(forName: name, object: object, queue: nil) { [weak self] notification in
            if audioOnly, let device = notification.object as? AVCaptureDevice, !device.hasMediaType(.audio) { return }
            Task { @MainActor [weak self] in self?.terminate(issue) }
        }
        tokens.append((center, token))
    }

    private func inspect(_ ticket: UInt64) {
        guard active, generation == ticket else { return }
        if let issue = permissionIssue() { terminate(issue); return }
        timer = clock.schedule(after: 0.5) { [weak self] in self?.inspect(ticket) }
    }

    private func terminate(_ issue: LocalVoiceIssue) {
        guard active else { return }
        stop(); failed(issue)
    }

    static func permissionFailure() -> LocalVoiceIssue? {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else { return .speechDenied }
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { return .microphoneDenied }
        return nil
    }
}
