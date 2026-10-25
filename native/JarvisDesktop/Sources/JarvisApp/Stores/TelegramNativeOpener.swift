import AppKit
import DesktopCore
import Foundation

/// Opens directly in a known Telegram application, never through a browser or
/// the default arbitrary URL handler. Bundle IDs are routing metadata, not code
/// signing attestation; macOS remains responsible for trust of installed apps.
@MainActor final class TelegramNativeOpener: TelegramNativeURLOpener {
    private func application() -> URL? {
        for identifier in ["ru.keepcoder.Telegram", "org.telegram.desktop"] {
            if let app = NSWorkspace.shared.urlForApplication(withBundleIdentifier: identifier),
               Bundle(url: app)?.bundleIdentifier == identifier { return app }
        }
        return nil
    }
    func canOpenTelegram() -> Bool { application() != nil }
    func openTelegram(_ sensitiveURL: URL) async -> Bool {
        guard sensitiveURL.scheme == "tg", let app = application() else { return false }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        configuration.addsToRecentItems = false
        configuration.promptsUserIfNeeded = false
        configuration.allowsRunningApplicationSubstitution = false
        return await withCheckedContinuation { continuation in
            let receipt = OpenReceipt(continuation)
            let deadline = Task { @MainActor in
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                receipt.finish(false)
            }
            NSWorkspace.shared.open([sensitiveURL], withApplicationAt: app, configuration: configuration) { _, error in
                // Never retain/log localized errors: Launch Services may embed
                // the sensitive URL in a diagnostic's metadata or description.
                let confirmed = error == nil
                Task { @MainActor in deadline.cancel(); receipt.finish(confirmed) }
            }
        }
    }

    @MainActor private final class OpenReceipt {
        private var continuation: CheckedContinuation<Bool, Never>?
        init(_ continuation: CheckedContinuation<Bool, Never>) { self.continuation = continuation }
        func finish(_ success: Bool) {
            let pending = continuation; continuation = nil
            pending?.resume(returning: success)
        }
    }
}
