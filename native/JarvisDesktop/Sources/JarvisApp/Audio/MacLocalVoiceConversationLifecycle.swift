import AppKit
import DesktopCore
import Foundation

/// Own this only for an explicitly started Talk session. It observes lifecycle
/// throughout model/playback phases as well as capture, and retires itself when
/// Talk becomes idle. It never requests permission or restarts a session.
@MainActor final class MacLocalVoiceConversationLifecycle {
    private weak var talk: LocalVoiceConversationController?
    private weak var voice: LocalVoiceController?
    private let clock: any LocalVoiceClock
    private let permissionIssue: @MainActor () -> LocalVoiceIssue?
    private var tokens: [(NotificationCenter, NSObjectProtocol)] = []
    private var timer: (any LocalVoiceCancellation)?
    private var active = true

    init(talk: LocalVoiceConversationController, voice: LocalVoiceController,
         center: NotificationCenter = .default, workspaceCenter: NotificationCenter? = nil,
         clock: (any LocalVoiceClock)? = nil,
         permissionIssue: (@MainActor () -> LocalVoiceIssue?)? = nil) {
        self.talk = talk; self.voice = voice; self.clock = clock ?? SystemLocalVoiceClock()
        self.permissionIssue = permissionIssue ?? { LocalCaptureMonitor.permissionFailure() }
        let workspaceCenter = workspaceCenter ?? NSWorkspace.shared.notificationCenter
        observe(center, NSApplication.didResignActiveNotification, allowAuthorizationPrompt: true)
        observe(workspaceCenter, NSWorkspace.willSleepNotification)
        observe(workspaceCenter, NSWorkspace.sessionDidResignActiveNotification)
        inspect()
    }

    private func observe(_ center: NotificationCenter, _ name: Notification.Name, allowAuthorizationPrompt: Bool = false) {
        let token = center.addObserver(forName: name, object: nil, queue: nil) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.active, let talk = self.talk, talk.isActive, talk.phase != .stopping else { return }
                if allowAuthorizationPrompt && self.voice?.phase == .authorizing { return }
                talk.stop(reason: .interrupted)
            }
        }
        tokens.append((center, token))
    }

    private func inspect() {
        guard active else { return }
        guard let talk, talk.isActive else { close(); return }
        if talk.phase != .stopping && voice?.phase != .authorizing, permissionIssue() != nil {
            talk.stop(reason: .permissionRevoked)
        }
        timer = clock.schedule(after: 0.5) { [weak self] in self?.inspect() }
    }

    func close() {
        active = false; timer?.cancel(); timer = nil
        for (center, token) in tokens { center.removeObserver(token) }
        tokens = []
    }

    deinit {
        for (center, token) in tokens { center.removeObserver(token) }
        let timer = timer
        Task { @MainActor in timer?.cancel() }
    }
}
