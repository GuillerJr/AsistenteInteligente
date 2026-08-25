import AegisAudioCore
import AppKit
import ApplicationServices
import Foundation

private final class BoundedProcessOutput: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()
    private var overflow = false

    func append(_ chunk: Data) {
        lock.lock()
        defer { lock.unlock() }
        guard !overflow else { return }
        if data.count + chunk.count > 65_536 {
            overflow = true
            data.removeAll(keepingCapacity: false)
            return
        }
        data.append(chunk)
    }

    func value() -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return overflow ? nil : data
    }
}

enum ComputerControlService {
    static func inspect(bundle: Bundle = .main) -> ComputerControlCapabilityState {
        let hostAccessibility = AXIsProcessTrusted()
        guard let payload = execute(
            command: ["command": "status", "protocol_version": "1.0"],
            bundle: bundle,
            timeoutSeconds: 2
        ) else {
            return .helperUnavailable
        }
        guard
            payload["status"] as? String == "ok",
            let screenCapture = payload["screen_capture"] as? Bool,
            let helperAccessibility = payload["accessibility"] as? Bool
        else {
            return .helperUnavailable
        }
        let accessibility = hostAccessibility && helperAccessibility
        return switch (screenCapture, accessibility) {
        case (true, true): .ready
        case (false, true): .screenCaptureMissing
        case (true, false): .accessibilityMissing
        case (false, false): .permissionsMissing
        }
    }

    static func execute(
        command: [String: Any],
        bundle: Bundle = .main,
        timeoutSeconds: TimeInterval = 21
    ) -> [String: Any]? {
        guard
            JSONSerialization.isValidJSONObject(command),
            let commandData = try? JSONSerialization.data(
                withJSONObject: command,
                options: [.sortedKeys]
            ),
            !commandData.isEmpty,
            commandData.count <= 8_192,
            timeoutSeconds.isFinite,
            (1 ... 22).contains(timeoutSeconds)
        else {
            return nil
        }
        let helper = helperBinaryURL(bundle: bundle)
        guard isSafeHelper(helper) else { return nil }
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
        let bufferedOutput = BoundedProcessOutput()
        let outputCompleted = DispatchSemaphore(value: 0)
        output.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty {
                handle.readabilityHandler = nil
                outputCompleted.signal()
            } else {
                bufferedOutput.append(chunk)
            }
        }
        let completed = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in completed.signal() }
        do {
            try process.run()
            input.fileHandleForWriting.write(commandData)
            try input.fileHandleForWriting.close()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            return nil
        }
        guard completed.wait(timeout: .now() + timeoutSeconds) == .success else {
            process.terminate()
            output.fileHandleForReading.readabilityHandler = nil
            return nil
        }
        guard outputCompleted.wait(timeout: .now() + 1) == .success else {
            output.fileHandleForReading.readabilityHandler = nil
            return nil
        }
        output.fileHandleForReading.readabilityHandler = nil
        guard
            let data = bufferedOutput.value(),
            !data.isEmpty,
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let status = payload["status"] as? String,
            (status == "ok" && process.terminationStatus == 0)
                || (status == "error" && process.terminationStatus != 0)
        else {
            return nil
        }
        return payload
    }

    @MainActor
    static func requestPermission(
        _ request: ComputerControlPermissionRequest,
        bundle: Bundle = .main
    ) {
        let argument: String
        switch request {
        case .screenCapture:
            argument = "--request-screen-capture"
        case .accessibility:
            _ = AXIsProcessTrustedWithOptions(
                ["AXTrustedCheckOptionPrompt": true] as CFDictionary
            )
            argument = "--request-accessibility"
        }
        openHelper(argument: argument, bundle: bundle)
    }

    private static func openHelper(argument: String, bundle: Bundle) {
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
        configuration.activates = true
        configuration.addsToRecentItems = false
        configuration.arguments = [argument]
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
        guard FileManager.default.isExecutableFile(atPath: url.path) else {
            return false
        }
        let verification = Process()
        verification.executableURL = URL(fileURLWithPath: "/usr/bin/codesign")
        verification.arguments = ["--verify", "--strict", url.path]
        verification.standardInput = FileHandle.nullDevice
        verification.standardOutput = FileHandle.nullDevice
        verification.standardError = FileHandle.nullDevice
        let completed = DispatchSemaphore(value: 0)
        verification.terminationHandler = { _ in completed.signal() }
        do {
            try verification.run()
        } catch {
            return false
        }
        guard completed.wait(timeout: .now() + 2) == .success else {
            verification.terminate()
            return false
        }
        return verification.terminationStatus == 0
    }
}
