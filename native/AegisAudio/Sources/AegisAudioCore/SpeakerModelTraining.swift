import Darwin
import Foundation

public enum SpeakerModelTrainingError: Error, Equatable, Sendable {
    case helperUnavailable
    case modelExists
    case trainingFailed
    case unsafeStorage
    case invalidModel
}

enum SpeakerModelStorage {
    static let modelName = "JarvisSpeakerIdentity.mlmodelc"

    static func defaultModelURL(fileManager: FileManager = .default) -> URL {
        let base = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? fileManager.homeDirectoryForCurrentUser
        return base.appending(
            path: "Aegis/Models/\(modelName)",
            directoryHint: .isDirectory
        )
    }

    static func assetExists(_ url: URL, fileManager: FileManager = .default) -> Bool {
        if fileManager.fileExists(atPath: url.path) {
            return true
        }
        return (try? url.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) == true
    }

    static func secureModelDirectory(
        for modelURL: URL,
        fileManager: FileManager = .default
    ) -> Bool {
        let directory = modelURL.deletingLastPathComponent()
        guard
            let values = try? directory.resourceValues(forKeys: [
                .isDirectoryKey,
                .isSymbolicLinkKey,
            ]),
            values.isDirectory == true,
            values.isSymbolicLink != true,
            let attributes = try? fileManager.attributesOfItem(atPath: directory.path),
            let permissions = attributes[.posixPermissions] as? Int,
            let owner = (attributes[.ownerAccountID] as? NSNumber)?.intValue
        else {
            return false
        }
        return permissions & 0o077 == 0 && owner == Int(getuid())
    }
}

public struct SpeakerModelTrainer: Sendable {
    private let helperURL: URL
    private let datasetURL: URL
    private let modelURL: URL

    public init(bundle: Bundle = .main, fileManager: FileManager = .default) {
        helperURL = bundle.bundleURL.appending(
            path: "Contents/Helpers/jarvis-speaker-trainer"
        )
        let base = fileManager.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first ?? fileManager.homeDirectoryForCurrentUser
        datasetURL = base.appending(
            path: "Aegis/SpeakerEnrollment",
            directoryHint: .isDirectory
        )
        modelURL = SpeakerModelStorage.defaultModelURL(fileManager: fileManager)
    }

    init(helperURL: URL, datasetURL: URL, modelURL: URL) {
        self.helperURL = helperURL
        self.datasetURL = datasetURL
        self.modelURL = modelURL
    }

    public func train() throws {
        let manager = FileManager.default
        guard validHelper(fileManager: manager) else {
            throw SpeakerModelTrainingError.helperUnavailable
        }
        if SpeakerModelStorage.assetExists(modelURL, fileManager: manager) {
            throw SpeakerModelTrainingError.modelExists
        }

        let modelDirectory = modelURL.deletingLastPathComponent()
        if !manager.fileExists(atPath: modelDirectory.path) {
            try manager.createDirectory(
                at: modelDirectory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
        }
        guard SpeakerModelStorage.secureModelDirectory(for: modelURL, fileManager: manager) else {
            throw SpeakerModelTrainingError.unsafeStorage
        }

        let process = Process()
        process.executableURL = helperURL
        process.arguments = [datasetURL.path, modelURL.path]
        process.currentDirectoryURL = modelDirectory
        process.environment = [
            "HOME": manager.homeDirectoryForCurrentUser.path,
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": manager.temporaryDirectory.path,
        ]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            throw SpeakerModelTrainingError.trainingFailed
        }
        guard process.terminationReason == .exit, process.terminationStatus == 0 else {
            throw SpeakerModelTrainingError.trainingFailed
        }
        guard SpeakerIdentityCapability.inspect(modelURL: modelURL) == .ready else {
            try? manager.removeItem(at: modelURL)
            throw SpeakerModelTrainingError.invalidModel
        }
    }

    private func validHelper(fileManager: FileManager) -> Bool {
        guard let values = try? helperURL.resourceValues(forKeys: [
            .isRegularFileKey, .isSymbolicLinkKey,
        ]) else {
            return false
        }
        return values.isRegularFile == true
            && values.isSymbolicLink != true
            && fileManager.isExecutableFile(atPath: helperURL.path)
    }
}
