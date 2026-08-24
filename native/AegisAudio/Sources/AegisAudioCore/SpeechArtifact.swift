import CryptoKit
import Darwin
import Foundation

public enum SpeechArtifactReaderError: Error, Equatable {
    case invalidConfiguration
    case unsafeDirectory
    case unsafeArtifact
    case readFailed
    case digestMismatch
    case malformedAudio
}

public struct SpeechArtifactReader: Sendable {
    public static let defaultDirectory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Aegis/speech", isDirectory: true)

    private let directory: URL

    public init(directory: URL = Self.defaultDirectory) throws {
        guard
            directory.isFileURL,
            directory.path.hasPrefix("/"),
            directory.path.utf8.count < 1_024
        else {
            throw SpeechArtifactReaderError.invalidConfiguration
        }
        self.directory = directory.standardizedFileURL
    }

    public func read(_ artifact: IPCSpeechArtifactEvent) throws -> Data {
        try validateDirectory()
        let url = directory.appendingPathComponent(artifact.fileName, isDirectory: false)
        guard
            url.deletingLastPathComponent().standardizedFileURL == directory,
            url.lastPathComponent == artifact.fileName
        else {
            throw SpeechArtifactReaderError.unsafeArtifact
        }

        var pathMetadata = stat()
        guard lstat(url.path, &pathMetadata) == 0 else {
            throw SpeechArtifactReaderError.readFailed
        }
        try validateArtifact(pathMetadata, expectedBytes: artifact.byteCount)

        let descriptor = Darwin.open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else {
            throw SpeechArtifactReaderError.readFailed
        }
        defer { close(descriptor) }

        var openedMetadata = stat()
        guard fstat(descriptor, &openedMetadata) == 0 else {
            throw SpeechArtifactReaderError.readFailed
        }
        try validateArtifact(openedMetadata, expectedBytes: artifact.byteCount)
        guard
            openedMetadata.st_dev == pathMetadata.st_dev,
            openedMetadata.st_ino == pathMetadata.st_ino
        else {
            throw SpeechArtifactReaderError.unsafeArtifact
        }

        var data = Data(count: artifact.byteCount)
        try data.withUnsafeMutableBytes { storage in
            guard let base = storage.baseAddress else {
                throw SpeechArtifactReaderError.readFailed
            }
            var offset = 0
            while offset < storage.count {
                let count = Darwin.read(
                    descriptor,
                    base.advanced(by: offset),
                    storage.count - offset
                )
                if count > 0 {
                    offset += count
                } else if count < 0, errno == EINTR {
                    continue
                } else {
                    throw SpeechArtifactReaderError.readFailed
                }
            }
        }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard digest == artifact.sha256 else {
            throw SpeechArtifactReaderError.digestMismatch
        }
        guard
            data.starts(with: [0x52, 0x49, 0x46, 0x46]),
            data.dropFirst(8).starts(with: [0x57, 0x41, 0x56, 0x45])
        else {
            throw SpeechArtifactReaderError.malformedAudio
        }
        return data
    }

    private func validateDirectory() throws {
        var metadata = stat()
        guard
            lstat(directory.path, &metadata) == 0,
            metadata.st_uid == geteuid(),
            (metadata.st_mode & S_IFMT) == S_IFDIR,
            (metadata.st_mode & 0o077) == 0
        else {
            throw SpeechArtifactReaderError.unsafeDirectory
        }
    }

    private func validateArtifact(_ metadata: stat, expectedBytes: Int) throws {
        guard
            metadata.st_uid == geteuid(),
            (metadata.st_mode & S_IFMT) == S_IFREG,
            (metadata.st_mode & 0o077) == 0,
            metadata.st_size == off_t(expectedBytes),
            (44 ... IPCSpeechArtifactEvent.maximumAudioBytes).contains(expectedBytes)
        else {
            throw SpeechArtifactReaderError.unsafeArtifact
        }
    }
}
