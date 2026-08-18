import CryptoKit
import Darwin
import Foundation
import Security

public enum LocalIPCError: Error, Equatable {
    case invalidConfiguration
    case keychainCredentialUnavailable
    case invalidCredential
    case socketUnavailable
    case unsafeSocket
    case connectionFailed
    case frameTooLarge
    case malformedResponse
    case responseMismatch
    case responseAuthenticationFailed
    case staleResponse
}

public struct LocalIPCResponse {
    public let requestID: UUID
    public let ok: Bool
    public let payload: [String: Any]
    public let errorCode: String?
}

public struct VoiceSubmissionEvent: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let type: String
    public let jobID: UUID
    public let status: String

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let rawJobID = response.payload["job_id"] as? String,
            let jobID = UUID(uuidString: rawJobID),
            let status = response.payload["status"] as? String
        else {
            return nil
        }
        schemaVersion = "1.0"
        type = "ipc.voice_submitted"
        self.jobID = jobID
        self.status = status
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case jobID = "job_id"
        case status
    }
}

public struct IPCHealthEvent: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let type: String
    public let status: String

    public init?(response: LocalIPCResponse) {
        guard response.ok, let status = response.payload["status"] as? String else {
            return nil
        }
        schemaVersion = "1.0"
        type = "ipc.health"
        self.status = status
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case status
    }
}

public struct IPCStatusEvent: Codable, Equatable, Sendable {
    public let schemaVersion: String
    public let type: String
    public let state: String
    public let errorCode: String?

    public init(state: String, errorCode: String? = nil) {
        schemaVersion = "1.0"
        type = "ipc.status"
        self.state = state
        self.errorCode = errorCode
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case state
        case errorCode = "error_code"
    }
}

public struct MacOSIPCSecretStore: Sendable {
    public let service: String
    public let account: String

    public init(service: String = "ai.aegis.ipc-auth", account: String = "default") throws {
        guard
            Self.isSafeKeychainAttribute(service),
            Self.isSafeKeychainAttribute(account)
        else {
            throw LocalIPCError.invalidConfiguration
        }
        self.service = service
        self.account = account
    }

    public func get() throws -> Data {
        let output = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = [
            "find-generic-password", "-a", account, "-s", service, "-w",
        ]
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            throw LocalIPCError.keychainCredentialUnavailable
        }
        let deadline = Date().addingTimeInterval(5)
        while process.isRunning, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        if process.isRunning {
            process.terminate()
        }
        process.waitUntilExit()
        let stored = output.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0, stored.count <= 65 else {
            throw LocalIPCError.keychainCredentialUnavailable
        }
        guard
            let encoded = String(data: stored, encoding: .utf8)?.trimmingCharacters(
                in: .newlines
            ),
            let secret = Self.decodeCanonicalSecret(encoded)
        else {
            throw LocalIPCError.invalidCredential
        }
        return secret
    }

    static func decodeCanonicalSecret(_ value: String) -> Data? {
        guard
            value.utf8.count == 64,
            value.utf8.allSatisfy({ (48 ... 57).contains($0) || (97 ... 102).contains($0) })
        else {
            return nil
        }
        var decoded = Data(capacity: 32)
        var index = value.startIndex
        for _ in 0 ..< 32 {
            let next = value.index(index, offsetBy: 2)
            guard let byte = UInt8(value[index ..< next], radix: 16) else {
                return nil
            }
            decoded.append(byte)
            index = next
        }
        return decoded
    }

    private static func isSafeKeychainAttribute(_ value: String) -> Bool {
        !value.isEmpty
            && value.utf8.count <= 128
            && value.unicodeScalars.allSatisfy {
                !CharacterSet.controlCharacters.contains($0)
            }
    }
}

public final class LocalIPCClient {
    public static let defaultSocketPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Aegis/aegis.sock").path

    private let socketPath: String
    private let secret: Data
    private let maxFrameBytes: Int
    private let timeoutSeconds: TimeInterval
    private let clockSkewSeconds: TimeInterval
    private let now: () -> Date
    private let requestID: () -> UUID
    private let nonce: () throws -> String

    public convenience init(
        socketPath: String = LocalIPCClient.defaultSocketPath,
        secretStore: MacOSIPCSecretStore,
        maxFrameBytes: Int = 65_536,
        timeoutSeconds: TimeInterval = 5,
        clockSkewSeconds: TimeInterval = 30
    ) throws {
        try self.init(
            socketPath: socketPath,
            secret: secretStore.get(),
            maxFrameBytes: maxFrameBytes,
            timeoutSeconds: timeoutSeconds,
            clockSkewSeconds: clockSkewSeconds
        )
    }

    public convenience init(
        socketPath: String = LocalIPCClient.defaultSocketPath,
        secret: Data,
        maxFrameBytes: Int = 65_536,
        timeoutSeconds: TimeInterval = 5,
        clockSkewSeconds: TimeInterval = 30
    ) throws {
        try self.init(
            socketPath: socketPath,
            secret: secret,
            maxFrameBytes: maxFrameBytes,
            timeoutSeconds: timeoutSeconds,
            clockSkewSeconds: clockSkewSeconds,
            now: Date.init,
            requestID: UUID.init,
            nonce: Self.randomNonce
        )
    }

    init(
        socketPath: String,
        secret: Data,
        maxFrameBytes: Int,
        timeoutSeconds: TimeInterval,
        clockSkewSeconds: TimeInterval,
        now: @escaping () -> Date,
        requestID: @escaping () -> UUID,
        nonce: @escaping () throws -> String
    ) throws {
        guard
            !socketPath.isEmpty,
            socketPath.hasPrefix("/"),
            socketPath.utf8.count < MemoryLayout.size(ofValue: sockaddr_un().sun_path),
            secret.count == 32,
            (4_096 ... 1_048_576).contains(maxFrameBytes),
            timeoutSeconds.isFinite,
            (0.1 ... 30).contains(timeoutSeconds),
            clockSkewSeconds.isFinite,
            (5 ... 300).contains(clockSkewSeconds)
        else {
            throw LocalIPCError.invalidConfiguration
        }
        self.socketPath = socketPath
        self.secret = secret
        self.maxFrameBytes = maxFrameBytes
        self.timeoutSeconds = timeoutSeconds
        self.clockSkewSeconds = clockSkewSeconds
        self.now = now
        self.requestID = requestID
        self.nonce = nonce
    }

    public func health() throws -> LocalIPCResponse {
        try call(method: "health")
    }

    public func submitVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        conversationID: UUID? = nil
    ) throws -> LocalIPCResponse {
        guard transcript.isFinal, transcript.onDevice else {
            throw LocalIPCError.invalidConfiguration
        }
        let transcriptData = try JSONEncoder().encode(transcript)
        guard
            let transcriptObject = try JSONSerialization.jsonObject(with: transcriptData)
                as? [String: Any]
        else {
            throw LocalIPCError.invalidConfiguration
        }
        var payload: [String: Any] = ["transcript": transcriptObject]
        if let conversationID {
            payload["conversation_id"] = conversationID.uuidString.lowercased()
        }
        return try call(method: "voice.submit", payload: payload)
    }

    public func call(
        method: String,
        payload: [String: Any] = [:]
    ) throws -> LocalIPCResponse {
        guard
            method.range(
                of: #"^[a-z][a-z0-9_.-]{1,63}$"#,
                options: .regularExpression
            ) != nil,
            JSONSerialization.isValidJSONObject(payload)
        else {
            throw LocalIPCError.invalidConfiguration
        }
        try validateSocket()
        let current = now()
        let currentRequestID = requestID()
        let currentNonce = try nonce()
        guard currentNonce.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil else {
            throw LocalIPCError.invalidConfiguration
        }
        let body: [String: Any] = [
            "protocol_version": "1.0",
            "request_id": currentRequestID.uuidString.lowercased(),
            "method": method,
            "timestamp": Self.formatTimestamp(current),
            "nonce": currentNonce,
            "payload": payload,
        ]
        var envelope = body
        envelope["auth_tag"] = try Self.authenticationTag(body: body, secret: secret)
        var frame = try Self.canonicalJSON(envelope)
        frame.append(0x0A)
        guard frame.count <= maxFrameBytes else {
            throw LocalIPCError.frameTooLarge
        }

        let responseFrame = try exchange(frame)
        return try parseResponse(
            responseFrame,
            requestID: currentRequestID,
            requestNonce: currentNonce
        )
    }

    static func canonicalJSON(_ object: Any) throws -> Data {
        guard JSONSerialization.isValidJSONObject(object) else {
            throw LocalIPCError.invalidConfiguration
        }
        return Data(try canonicalJSONString(object).utf8)
    }

    static func authenticationTag(body: [String: Any], secret: Data) throws -> String {
        guard secret.count == 32 else {
            throw LocalIPCError.invalidCredential
        }
        let canonical = try canonicalJSON(body)
        let code = HMAC<SHA256>.authenticationCode(
            for: canonical,
            using: SymmetricKey(data: secret)
        )
        return Data(code).map { String(format: "%02x", $0) }.joined()
    }

    static func formatTimestamp(_ date: Date) -> String {
        let microsecondsSinceEpoch = Int64((date.timeIntervalSince1970 * 1_000_000).rounded())
        let secondsSinceEpoch = microsecondsSinceEpoch / 1_000_000
        let microseconds = Int(abs(microsecondsSinceEpoch % 1_000_000))
        let wholeSeconds = Date(timeIntervalSince1970: TimeInterval(secondsSinceEpoch))
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .iso8601)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        let base = formatter.string(from: wholeSeconds)
        if microseconds == 0 {
            return "\(base)+00:00"
        }
        return String(format: "%@.%06d+00:00", base, microseconds)
    }

    private func validateSocket() throws {
        var metadata = stat()
        guard lstat(socketPath, &metadata) == 0 else {
            throw LocalIPCError.socketUnavailable
        }
        guard
            metadata.st_uid == geteuid(),
            (metadata.st_mode & S_IFMT) == S_IFSOCK,
            (metadata.st_mode & 0o077) == 0
        else {
            throw LocalIPCError.unsafeSocket
        }
    }

    private func exchange(_ request: Data) throws -> Data {
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else {
            throw LocalIPCError.connectionFailed
        }
        defer { close(descriptor) }
        var timeout = timeval(
            tv_sec: Int(timeoutSeconds),
            tv_usec: Int32((timeoutSeconds.truncatingRemainder(dividingBy: 1)) * 1_000_000)
        )
        guard
            setsockopt(
                descriptor,
                SOL_SOCKET,
                SO_SNDTIMEO,
                &timeout,
                socklen_t(MemoryLayout<timeval>.size)
            ) == 0,
            setsockopt(
                descriptor,
                SOL_SOCKET,
                SO_RCVTIMEO,
                &timeout,
                socklen_t(MemoryLayout<timeval>.size)
            ) == 0
        else {
            throw LocalIPCError.connectionFailed
        }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8CString)
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            pathBytes.withUnsafeBytes { source in
                destination.copyBytes(from: source)
            }
        }
        let addressLength = socklen_t(MemoryLayout<sa_family_t>.size + pathBytes.count)
        address.sun_len = UInt8(addressLength)
        let connected = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(descriptor, $0, addressLength)
            }
        }
        guard connected == 0 else {
            throw LocalIPCError.connectionFailed
        }
        try writeAll(request, to: descriptor)
        return try readFrame(from: descriptor)
    }

    private func writeAll(_ data: Data, to descriptor: Int32) throws {
        try data.withUnsafeBytes { rawBuffer in
            guard let base = rawBuffer.baseAddress else {
                throw LocalIPCError.connectionFailed
            }
            var offset = 0
            while offset < data.count {
                let count = Darwin.write(descriptor, base.advanced(by: offset), data.count - offset)
                if count > 0 {
                    offset += count
                } else if count < 0, errno == EINTR {
                    continue
                } else {
                    throw LocalIPCError.connectionFailed
                }
            }
        }
    }

    private func readFrame(from descriptor: Int32) throws -> Data {
        var response = Data()
        var buffer = [UInt8](repeating: 0, count: 4_096)
        while true {
            let count = buffer.withUnsafeMutableBytes { storage in
                Darwin.read(descriptor, storage.baseAddress, storage.count)
            }
            if count > 0 {
                response.append(contentsOf: buffer.prefix(count))
                guard response.count <= maxFrameBytes else {
                    throw LocalIPCError.frameTooLarge
                }
                if let newline = response.firstIndex(of: 0x0A) {
                    guard newline == response.index(before: response.endIndex) else {
                        throw LocalIPCError.malformedResponse
                    }
                    response.removeLast()
                    return response
                }
            } else if count == 0 {
                throw LocalIPCError.malformedResponse
            } else if errno == EINTR {
                continue
            } else {
                throw LocalIPCError.connectionFailed
            }
        }
    }

    private func parseResponse(
        _ data: Data,
        requestID: UUID,
        requestNonce: String
    ) throws -> LocalIPCResponse {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            Set(object.keys) == [
                "protocol_version", "request_id", "request_nonce", "timestamp", "ok",
                "payload", "error_code", "auth_tag",
            ],
            object["protocol_version"] as? String == "1.0",
            let rawRequestID = object["request_id"] as? String,
            let responseRequestID = UUID(uuidString: rawRequestID),
            let responseNonce = object["request_nonce"] as? String,
            let timestamp = object["timestamp"] as? String,
            let responseDate = Self.parseTimestamp(timestamp),
            let signedTimestamp = Self.canonicalTimestampText(timestamp),
            let ok = Self.boolean(object["ok"]),
            let payload = object["payload"] as? [String: Any],
            let authTag = object["auth_tag"] as? String,
            authTag.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil
        else {
            throw LocalIPCError.malformedResponse
        }
        guard responseRequestID == requestID, responseNonce == requestNonce else {
            throw LocalIPCError.responseMismatch
        }
        guard abs(now().timeIntervalSince(responseDate)) < clockSkewSeconds else {
            throw LocalIPCError.staleResponse
        }
        var signedBody = object
        signedBody.removeValue(forKey: "auth_tag")
        signedBody["timestamp"] = signedTimestamp
        let expected = try Self.authenticationTag(body: signedBody, secret: secret)
        guard Self.constantTimeEqual(authTag, expected) else {
            throw LocalIPCError.responseAuthenticationFailed
        }
        let errorCode: String?
        if object["error_code"] is NSNull {
            errorCode = nil
        } else if let value = object["error_code"] as? String,
                  value.range(of: #"^[a-z][a-z0-9_]{2,63}$"#, options: .regularExpression) != nil {
            errorCode = value
        } else {
            throw LocalIPCError.malformedResponse
        }
        guard (ok && errorCode == nil) || (!ok && errorCode != nil) else {
            throw LocalIPCError.malformedResponse
        }
        return LocalIPCResponse(
            requestID: requestID,
            ok: ok,
            payload: payload,
            errorCode: errorCode
        )
    }

    private static func parseTimestamp(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let parsed = formatter.date(from: value) {
            return parsed
        }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }

    private static func canonicalTimestampText(_ value: String) -> String? {
        if value.hasSuffix("Z") {
            return String(value.dropLast()) + "+00:00"
        }
        if value.hasSuffix("+00:00") {
            return value
        }
        return nil
    }

    private static func canonicalJSONString(_ value: Any) throws -> String {
        if let dictionary = value as? [String: Any] {
            let fields = try dictionary.keys.sorted().map { key in
                "\(try encodedString(key)):\(try canonicalJSONString(dictionary[key]!))"
            }
            return "{\(fields.joined(separator: ","))}"
        }
        if let array = value as? [Any] {
            return "[\(try array.map(canonicalJSONString).joined(separator: ","))]"
        }
        if let string = value as? String {
            return try encodedString(string)
        }
        if value is NSNull {
            return "null"
        }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                return number.boolValue ? "true" : "false"
            }
            let objectiveCType = String(cString: number.objCType)
            if objectiveCType == "f" || objectiveCType == "d" {
                let floatingPoint = number.doubleValue
                guard floatingPoint.isFinite else {
                    throw LocalIPCError.invalidConfiguration
                }
                return String(floatingPoint)
            }
            return number.stringValue
        }
        throw LocalIPCError.invalidConfiguration
    }

    private static func encodedString(_ value: String) throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = .withoutEscapingSlashes
        let encoded = try encoder.encode(value)
        guard let result = String(data: encoded, encoding: .utf8) else {
            throw LocalIPCError.invalidConfiguration
        }
        return result
    }

    private static func boolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) == CFBooleanGetTypeID() else {
            return nil
        }
        return number.boolValue
    }

    private static func constantTimeEqual(_ first: String, _ second: String) -> Bool {
        let left = Array(first.utf8)
        let right = Array(second.utf8)
        guard left.count == right.count else {
            return false
        }
        var difference: UInt8 = 0
        for index in left.indices {
            difference |= left[index] ^ right[index]
        }
        return difference == 0
    }

    private static func randomNonce() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 16)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw LocalIPCError.invalidConfiguration
        }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }
}
