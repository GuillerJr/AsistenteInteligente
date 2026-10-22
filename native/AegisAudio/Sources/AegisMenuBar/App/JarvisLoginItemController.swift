import Foundation
import ServiceManagement

/// SMAppService is the sole source of truth for login startup, including migration.
@MainActor
final class JarvisLoginItemController {
    static let shared = JarvisLoginItemController()
    enum Status: Equatable { case disabled, enabled, requiresApproval }

    var status: Status {
        switch SMAppService.mainApp.status {
        case .enabled: .enabled
        case .requiresApproval: .requiresApproval
        default: .disabled
        }
    }

    @discardableResult
    func setEnabled(_ enabled: Bool) throws -> Status {
        if enabled { try SMAppService.mainApp.register() }
        else { try SMAppService.mainApp.unregister() }
        // Retiring the legacy job also honors an explicit choice to turn startup off.
        Task { await retireLegacyAgent() }
        return status
    }

    func openApprovalSettings() { SMAppService.openSystemSettingsLoginItems() }

    func migrateLegacyIfNeeded() async {
        guard Self.isLegacyAgent(legacyURL) else { return }
        do {
            if status == .disabled { try SMAppService.mainApp.register() }
            if status == .enabled || status == .requiresApproval { await retireLegacyAgent() }
        } catch {
            // Keep the old preference on disk if macOS rejects registration. The next
            // launch can retry; no credential or privacy prompt is answered here.
        }
    }

    private var legacyURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/ai.aegis.menubar.autostart.plist")
    }

    static func isLegacyAgent(_ url: URL) -> Bool {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              let size = attributes[.size] as? NSNumber, size.intValue <= 16_384,
              let data = try? Data(contentsOf: url),
              let value = try? PropertyListSerialization.propertyList(from: data, format: nil),
              let dictionary = value as? [String: Any],
              dictionary["Label"] as? String == "ai.aegis.menubar.autostart" else { return false }
        return true
    }

    private func retireLegacyAgent() async {
        guard Self.isLegacyAgent(legacyURL) else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["bootout", "gui/\(getuid())/ai.aegis.menubar.autostart"]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            for _ in 0..<40 {
                if !process.isRunning { break }
                try await Task.sleep(for: .milliseconds(50))
            }
            guard !process.isRunning else { process.terminate(); return }
            // bootout also reports an error when the legacy job is already unloaded.
            if Self.isLegacyAgent(legacyURL) { try FileManager.default.removeItem(at: legacyURL) }
        } catch { return }
    }
}
