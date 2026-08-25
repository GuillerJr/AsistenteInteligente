import AppKit
import Foundation

enum ComputerControlCapabilityState: Equatable, Sendable {
    case ready
    case screenCaptureMissing
    case accessibilityMissing
    case permissionsMissing
    case helperUnavailable
}

enum ComputerControlService {
    static func inspect(bundle: Bundle = .main) -> ComputerControlCapabilityState {
        let helper = helperBinaryURL(bundle: bundle)
        guard isSafeHelper(helper) else { return .helperUnavailable }
        let process = Process()
        let input = Pipe()
        let output = Pipe()
        process.executableURL = helper
        process.currentDirectoryURL = helper.deletingLastPathComponent()
        process.arguments = []
        process.environment = [
            "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": FileManager.default.temporaryDirectory.path,
        ]
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        let completed = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in completed.signal() }
        do {
            try process.run()
            input.fileHandleForWriting.write(
                Data(#"{"command":"status","protocol_version":"1.0"}"#.utf8)
            )
            try input.fileHandleForWriting.close()
        } catch {
            return .helperUnavailable
        }
        guard completed.wait(timeout: .now() + 2) == .success else {
            process.terminate()
            return .helperUnavailable
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        guard
            process.terminationStatus == 0,
            data.count <= 4_096,
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            payload["status"] as? String == "ok",
            let screenCapture = payload["screen_capture"] as? Bool,
            let accessibility = payload["accessibility"] as? Bool
        else {
            return .helperUnavailable
        }
        return switch (screenCapture, accessibility) {
        case (true, true): .ready
        case (false, true): .screenCaptureMissing
        case (true, false): .accessibilityMissing
        case (false, false): .permissionsMissing
        }
    }

    @MainActor
    static func requestPermissions(bundle: Bundle = .main) {
        let helperApp = helperAppURL(bundle: bundle)
        guard
            (try? helperApp.resourceValues(forKeys: [
                .isDirectoryKey, .isSymbolicLinkKey,
            ]))?.isDirectory == true,
            (try? helperApp.resourceValues(forKeys: [.isSymbolicLinkKey]))?.isSymbolicLink != true
        else {
            return
        }
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = false
        configuration.addsToRecentItems = false
        configuration.arguments = ["--request-permissions"]
        NSWorkspace.shared.openApplication(
            at: helperApp,
            configuration: configuration
        )
    }

    private static func helperAppURL(bundle: Bundle) -> URL {
        bundle.bundleURL.appending(
            path: "Contents/Helpers/JarvisComputerHelper.app",
            directoryHint: .isDirectory
        )
    }

    private static func helperBinaryURL(bundle: Bundle) -> URL {
        helperAppURL(bundle: bundle).appending(
            path: "Contents/MacOS/JarvisComputerHelper"
        )
    }

    private static func isSafeHelper(_ url: URL) -> Bool {
        guard
            let values = try? url.resourceValues(forKeys: [
                .isRegularFileKey, .isSymbolicLinkKey,
            ]),
            values.isRegularFile == true,
            values.isSymbolicLink != true
        else {
            return false
        }
        return FileManager.default.isExecutableFile(atPath: url.path)
    }
}
