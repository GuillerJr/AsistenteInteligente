@preconcurrency import AVFoundation
import AppKit
import DesktopCore
import Foundation
@preconcurrency import Speech

/// No notification can restart a microphone. The owner must explicitly start a
/// new capture after a real input change, revocation, focus loss or system sleep.
@MainActor final class LocalCaptureMonitor {
    typealias Delivery = @Sendable (@escaping @MainActor @Sendable () -> Void) -> Void
    private let engine: AnyObject
    private let configuration: LocalCaptureInputConfiguration
    private let inputState: @MainActor () -> LocalCaptureInputState?
    private let center: NotificationCenter
    private let workspaceCenter: NotificationCenter
    private let clock: any LocalVoiceClock
    private let deliver: Delivery
    private let permissionIssue: @MainActor () -> LocalVoiceIssue?
    private let failed: @MainActor (LocalVoiceIssue) -> Void
    private var tokens: [(NotificationCenter, NSObjectProtocol)] = []
    private var timer: (any LocalVoiceCancellation)?
    private var active = false
    private var generation: UInt64 = 0

    init(engine: AnyObject, configuration: LocalCaptureInputConfiguration,
         inputState: @escaping @MainActor () -> LocalCaptureInputState?,
         center: NotificationCenter = .default, workspaceCenter: NotificationCenter? = nil,
         clock: (any LocalVoiceClock)? = nil,
         deliver: @escaping Delivery = { action in Task { @MainActor in action() } },
         permissionIssue: @escaping @MainActor () -> LocalVoiceIssue?,
         failed: @escaping @MainActor (LocalVoiceIssue) -> Void) {
        self.engine = engine; self.configuration = configuration; self.inputState = inputState
        self.center = center; self.workspaceCenter = workspaceCenter ?? NSWorkspace.shared.notificationCenter
        self.clock = clock ?? SystemLocalVoiceClock(); self.deliver = deliver
        self.permissionIssue = permissionIssue; self.failed = failed
    }

    func start() {
        guard !active else { return }
        active = true; generation &+= 1
        let ticket = generation
        // Register only for this active epoch. A queued callback captures its
        // original ticket and cannot retire a later explicit capture.
        observe(center, name: .AVAudioEngineConfigurationChange, object: engine, ticket: ticket)
        observe(center, name: AVCaptureDevice.wasDisconnectedNotification, object: nil, ticket: ticket, audioOnly: true)
        observe(center, name: NSApplication.didResignActiveNotification, object: nil, ticket: ticket, issue: .interrupted)
        observe(workspaceCenter, name: NSWorkspace.willSleepNotification, object: nil, ticket: ticket, issue: .interrupted)
        observe(workspaceCenter, name: NSWorkspace.sessionDidResignActiveNotification, object: nil, ticket: ticket, issue: .interrupted)
        inspect(ticket)
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
                         ticket: UInt64, issue: LocalVoiceIssue? = nil, audioOnly: Bool = false) {
        let delivery = deliver
        let token = center.addObserver(forName: name, object: object, queue: nil) { [weak self] notification in
            if audioOnly, let device = notification.object as? AVCaptureDevice, !device.hasMediaType(.audio) { return }
            delivery { [weak self] in
                guard let self, self.active, self.generation == ticket else { return }
                if let issue { self.terminate(issue) }
                else { self.inspectInput() }
            }
        }
        tokens.append((center, token))
    }

    private func inspect(_ ticket: UInt64) {
        guard active, generation == ticket else { return }
        inspectInput()
        guard active, generation == ticket else { return }
        timer = clock.schedule(after: 0.5) { [weak self] in self?.inspect(ticket) }
    }

    private func inspectInput() {
        guard active else { return }
        if let issue = permissionIssue() { terminate(issue); return }
        guard let current = inputState(), current.isRunning,
              current.configuration == configuration else { terminate(.inputChanged); return }
        // A same-input notification while the engine still runs is not a route
        // change. No sleep, debounce window, second start or retry is needed.
    }

    private func terminate(_ issue: LocalVoiceIssue) {
        guard active else { return }
        stop(); failed(issue)
    }

    static func activeCaptureFailure() -> LocalVoiceIssue? {
        permissionFailure() ?? (NSApp?.isActive == true ? nil : .interrupted)
    }

    static func permissionFailure() -> LocalVoiceIssue? {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else { return .speechDenied }
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { return .microphoneDenied }
        return nil
    }
}
