@preconcurrency import AVFoundation
import CryptoKit
import Foundation
import Security

public struct CapturedOwnerVoiceVariant: Sendable, Equatable {
    public let captureID: UUID
    public let ownerIdentifier: String
    public let pcm22050Mono: Data
    public let durationSeconds: Double

    public init(
        captureID: UUID,
        ownerIdentifier: String,
        pcm22050Mono: Data,
        durationSeconds: Double
    ) {
        self.captureID = captureID
        self.ownerIdentifier = ownerIdentifier
        self.pcm22050Mono = pcm22050Mono
        self.durationSeconds = durationSeconds
    }
}

public enum LocalVoiceAdapterError: Error, Sendable, Equatable {
    case invalidSample
    case keychainUnavailable
    case encryptionFailed
    case storageUnavailable
    case storageLimitReached
}

public final class OwnerVoiceSampleAccumulator: @unchecked Sendable {
    public static let outputSampleRate = 22_050.0
    public static let maximumDurationSeconds = 12.0

    private let lock = NSLock()
    private var samples = [Int16]()

    public init() {
        samples.reserveCapacity(Int(Self.outputSampleRate * 4))
    }

    public func append(_ buffer: AVAudioPCMBuffer) {
        guard
            let source = buffer.floatChannelData?[0],
            buffer.frameLength > 0,
            buffer.format.sampleRate >= 8_000
        else { return }
        let sourceCount = Int(buffer.frameLength)
        let rateRatio = Self.outputSampleRate / buffer.format.sampleRate
        let outputCount = max(1, Int((Double(sourceCount) * rateRatio).rounded(.down)))
        var converted = [Int16]()
        converted.reserveCapacity(outputCount)
        for outputIndex in 0 ..< outputCount {
            let sourcePosition = Double(outputIndex) / rateRatio
            let lower = min(Int(sourcePosition), sourceCount - 1)
            let upper = min(lower + 1, sourceCount - 1)
            let fraction = Float(sourcePosition - Double(lower))
            let value = source[lower] + (source[upper] - source[lower]) * fraction
            converted.append(Int16(max(-1, min(1, value)) * Float(Int16.max)))
        }
        lock.withLock {
            let capacity = Int(Self.outputSampleRate * Self.maximumDurationSeconds)
            let remaining = max(0, capacity - samples.count)
            if remaining > 0 {
                samples.append(contentsOf: converted.prefix(remaining))
            }
        }
    }

    public func finish(captureID: UUID, ownerIdentifier: String) -> CapturedOwnerVoiceVariant? {
        guard SpeakerIdentityCapability.isValidSpeakerLabel(ownerIdentifier) else { return nil }
        let captured = lock.withLock { samples }
        let duration = Double(captured.count) / Self.outputSampleRate
        guard (0.5 ... Self.maximumDurationSeconds).contains(duration) else { return nil }
        var pcm = Data(capacity: captured.count * 2)
        for sample in captured {
            var littleEndian = sample.littleEndian
            withUnsafeBytes(of: &littleEndian) { pcm.append(contentsOf: $0) }
        }
        return CapturedOwnerVoiceVariant(
            captureID: captureID,
            ownerIdentifier: ownerIdentifier,
            pcm22050Mono: pcm,
            durationSeconds: duration
        )
    }
}

public struct AuthorizedOwnerVocalVariantStore: Sendable {
    public static let keychainService = "ai.aegis.biometric-training"
    public static let keychainAccount = "default"
    public static let maximumSamples = 64
    public static let maximumEncryptedBytes = 32 * 1_024 * 1_024

    private let directory: URL

    public init(directory: URL? = nil) {
        self.directory = directory ?? FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(
                "Library/Application Support/Aegis/Biometrics/Training",
                isDirectory: true
            )
    }

    @discardableResult
    public func persist(_ sample: CapturedOwnerVoiceVariant) throws -> URL {
        guard
            SpeakerIdentityCapability.isValidSpeakerLabel(sample.ownerIdentifier),
            (0.5 ... OwnerVoiceSampleAccumulator.maximumDurationSeconds)
                .contains(sample.durationSeconds),
            !sample.pcm22050Mono.isEmpty,
            sample.pcm22050Mono.count.isMultiple(of: 2),
            sample.pcm22050Mono.count <= Int(
                OwnerVoiceSampleAccumulator.outputSampleRate
                    * OwnerVoiceSampleAccumulator.maximumDurationSeconds * 2
            )
        else { throw LocalVoiceAdapterError.invalidSample }
        try prepareDirectory()
        let existing = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: [.fileSizeKey],
            options: [.skipsHiddenFiles]
        ).filter { $0.pathExtension == "enc" }
        let totalBytes = try existing.reduce(0) { partial, url in
            partial + (try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0)
        }
        guard existing.count < Self.maximumSamples, totalBytes < Self.maximumEncryptedBytes else {
            throw LocalVoiceAdapterError.storageLimitReached
        }
        let caf = try Self.makeCAF(from: sample.pcm22050Mono)
        let key = try Self.encryptionKey()
        let owner = Data(sample.ownerIdentifier.utf8)
        guard owner.count <= 63 else { throw LocalVoiceAdapterError.invalidSample }
        let nonce = AES.GCM.Nonce()
        var header = Data("AEGBIO1\0".utf8)
        var uuid = sample.captureID.uuid
        withUnsafeBytes(of: &uuid) { header.append(contentsOf: $0) }
        header.append(UInt8(owner.count))
        header.append(owner)
        header.append(contentsOf: SHA256.hash(data: caf))
        let sealed: AES.GCM.SealedBox
        do {
            sealed = try AES.GCM.seal(caf, using: key, nonce: nonce, authenticating: header)
        } catch {
            throw LocalVoiceAdapterError.encryptionFailed
        }
        var envelope = header
        nonce.withUnsafeBytes { envelope.append(contentsOf: $0) }
        envelope.append(sealed.ciphertext)
        envelope.append(sealed.tag)
        let destination = directory
            .appendingPathComponent(sample.captureID.uuidString.lowercased())
            .appendingPathExtension("caf.enc")
        do {
            try envelope.write(to: destination, options: [.atomic, .withoutOverwriting])
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600],
                ofItemAtPath: destination.path
            )
        } catch {
            throw LocalVoiceAdapterError.storageUnavailable
        }
        return destination
    }

    private func prepareDirectory() throws {
        do {
            try FileManager.default.createDirectory(
                at: directory,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o700],
                ofItemAtPath: directory.path
            )
        } catch {
            throw LocalVoiceAdapterError.storageUnavailable
        }
    }

    private static func encryptionKey() throws -> SymmetricKey {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecAttrAccount: keychainAccount,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecSuccess,
           let encoded = result as? Data,
           let text = String(data: encoded, encoding: .utf8),
           let raw = Data(hexadecimal: text),
           raw.count == 32
        {
            return SymmetricKey(data: raw)
        }
        guard status == errSecItemNotFound else {
            throw LocalVoiceAdapterError.keychainUnavailable
        }
        let raw = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) }
        let encoded = Data(raw.map { String(format: "%02x", $0) }.joined().utf8)
        let add: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecAttrAccount: keychainAccount,
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecValueData: encoded,
        ]
        guard SecItemAdd(add as CFDictionary, nil) == errSecSuccess else {
            throw LocalVoiceAdapterError.keychainUnavailable
        }
        return SymmetricKey(data: raw)
    }

    private static func makeCAF(from pcm: Data) throws -> Data {
        guard !pcm.isEmpty, pcm.count.isMultiple(of: 2) else {
            throw LocalVoiceAdapterError.invalidSample
        }
        var output = Data()
        output.appendASCII("caff")
        output.appendBigEndian(UInt16(1))
        output.appendBigEndian(UInt16(0))
        output.appendASCII("desc")
        output.appendBigEndian(UInt64(32))
        output.appendBigEndian(OwnerVoiceSampleAccumulator.outputSampleRate.bitPattern)
        output.appendASCII("lpcm")
        output.appendBigEndian(UInt32(12))
        output.appendBigEndian(UInt32(2))
        output.appendBigEndian(UInt32(1))
        output.appendBigEndian(UInt32(2))
        output.appendBigEndian(UInt32(1))
        output.appendBigEndian(UInt32(16))
        output.appendASCII("data")
        output.appendBigEndian(UInt64(pcm.count + 4))
        output.appendBigEndian(UInt32(0))
        output.append(pcm)
        return output
    }
}

private extension Data {
    init?(hexadecimal: String) {
        guard hexadecimal.count == 64 else { return nil }
        var data = Data(capacity: 32)
        var index = hexadecimal.startIndex
        while index < hexadecimal.endIndex {
            let next = hexadecimal.index(index, offsetBy: 2)
            guard let value = UInt8(hexadecimal[index ..< next], radix: 16) else { return nil }
            data.append(value)
            index = next
        }
        self = data
    }

    mutating func appendASCII(_ string: String) {
        append(contentsOf: string.utf8)
    }

    mutating func appendBigEndian<T: FixedWidthInteger>(_ value: T) {
        var encoded = value.bigEndian
        Swift.withUnsafeBytes(of: &encoded) { append(contentsOf: $0) }
    }
}
