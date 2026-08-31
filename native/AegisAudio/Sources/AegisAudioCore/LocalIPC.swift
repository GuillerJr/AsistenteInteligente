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
    case streamAuthenticationFailed
    case streamSequenceInvalid
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
    public let conversationID: UUID?
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
        let conversationID: UUID?
        if let rawConversationID = response.payload["conversation_id"] as? String {
            guard let parsedConversationID = UUID(uuidString: rawConversationID) else {
                return nil
            }
            conversationID = parsedConversationID
        } else {
            conversationID = nil
        }
        schemaVersion = "1.0"
        type = "ipc.voice_submitted"
        self.jobID = jobID
        self.conversationID = conversationID
        self.status = status
    }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case type
        case jobID = "job_id"
        case conversationID = "conversation_id"
        case status
    }
}

public struct IPCConversationEvent: Equatable, Sendable {
    public let conversationID: UUID

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let rawConversationID = response.payload["conversation_id"] as? String,
            let conversationID = UUID(uuidString: rawConversationID)
        else {
            return nil
        }
        self.conversationID = conversationID
    }
}

public enum IPCJobState: String, Sendable {
    case queued
    case running
    case awaitingConfirmation = "awaiting_confirmation"
    case completed
    case failed
    case cancelled
}

public enum IPCBrainTarget: String, Sendable {
    case local
    case nvidia
    case deterministic
    case unknown
}

public struct IPCJobEvaluation: Equatable, Sendable {
    public let brain: IPCBrainTarget
    public let modelID: String?
    public let totalLatencyMilliseconds: Int
    public let wallLatencyMilliseconds: Int?
    public let confirmationWaitMilliseconds: Int
    public let firstPartialLatencyMilliseconds: Int?
    public let wallFirstPartialLatencyMilliseconds: Int?
    public let streamChunks: Int
    public let toolName: String?
    public let succeeded: Bool
    public let outcomeVerified: Bool
    public let voiceRequest: Bool
    public let ownerVerified: Bool

    init?(object: Any?) {
        guard
            let object = object as? [String: Any],
            let rawBrain = object["brain"] as? String,
            let brain = IPCBrainTarget(rawValue: rawBrain),
            let total = object["total_latency_ms"] as? Int,
            (0 ... 600_000).contains(total),
            let chunks = object["stream_chunks"] as? Int,
            (0 ... 100_000).contains(chunks),
            let succeeded = object["succeeded"] as? Bool,
            let outcomeVerified = object["outcome_verified"] as? Bool,
            let voiceRequest = object["voice_request"] as? Bool,
            let ownerVerified = object["owner_verified"] as? Bool,
            !ownerVerified || voiceRequest
        else { return nil }
        let modelID = object["model_id"] as? String
        let wallLatency = object["wall_latency_ms"] as? Int
        let confirmationWait = object["confirmation_wait_ms"] as? Int ?? 0
        let firstPartial = object["first_partial_latency_ms"] as? Int
        let wallFirstPartial = object["wall_first_partial_latency_ms"] as? Int
        let toolName = object["tool_name"] as? String
        guard
            modelID.map({ !$0.isEmpty && $0.utf8.count <= 256 }) ?? true,
            wallLatency.map({ (0 ... 600_000).contains($0) }) ?? true,
            (0 ... 600_000).contains(confirmationWait),
            wallLatency.map({ total + confirmationWait == $0 }) ?? (confirmationWait == 0),
            firstPartial.map({ (0 ... 600_000).contains($0) }) ?? true,
            wallFirstPartial.map({ (0 ... 600_000).contains($0) }) ?? true,
            wallLatency == nil || ((firstPartial == nil) == (wallFirstPartial == nil)),
            firstPartial.map({ first in
                wallFirstPartial.map({ first <= $0 }) ?? true
            }) ?? true,
            toolName.map({
                $0.range(of: #"^[a-z][a-z0-9_-]{2,63}$"#, options: .regularExpression) != nil
            }) ?? true
        else { return nil }
        self.brain = brain
        self.modelID = modelID
        totalLatencyMilliseconds = total
        wallLatencyMilliseconds = wallLatency
        confirmationWaitMilliseconds = confirmationWait
        firstPartialLatencyMilliseconds = firstPartial
        wallFirstPartialLatencyMilliseconds = wallFirstPartial
        streamChunks = chunks
        self.toolName = toolName
        self.succeeded = succeeded
        self.outcomeVerified = outcomeVerified
        self.voiceRequest = voiceRequest
        self.ownerVerified = ownerVerified
    }
}

public struct IPCPendingConfirmation: Equatable, Sendable {
    public let callDigest: String
    public let toolName: String
    public let summary: String
    public let expiresAt: Date

    init?(object: Any?) {
        guard
            let object = object as? [String: Any],
            let callDigest = object["call_digest"] as? String,
            callDigest.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
            let toolName = object["tool_name"] as? String,
            toolName.range(
                of: #"^[a-z][a-z0-9_-]{2,63}$"#,
                options: .regularExpression
            ) != nil,
            let summary = object["summary"] as? String,
            !summary.isEmpty,
            summary.utf8.count <= 512,
            let rawExpiry = object["expires_at"] as? String,
            rawExpiry.utf8.count <= 64,
            let expiresAt = Self.parseDate(rawExpiry)
        else {
            return nil
        }
        self.callDigest = callDigest
        self.toolName = toolName
        self.summary = summary
        self.expiresAt = expiresAt
    }

    private static func parseDate(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}

#if DEBUG
public extension IPCPendingConfirmation {
    static func debugPreview(
        toolName: String,
        summary: String,
        expiresAt: Date
    ) -> IPCPendingConfirmation? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return IPCPendingConfirmation(
            object: [
                "call_digest": String(repeating: "0", count: 64),
                "tool_name": toolName,
                "summary": summary,
                "expires_at": formatter.string(from: expiresAt),
            ]
        )
    }
}
#endif

public struct IPCJobStatusEvent: Equatable, Sendable {
    public static let maximumResultBytes = 24_576

    public let jobID: UUID
    public let state: IPCJobState
    public let result: String?
    public let errorCode: String?
    public let confirmation: IPCPendingConfirmation?
    public let partialResult: String?
    public let streamVersion: Int
    public let evaluation: IPCJobEvaluation?

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let rawJobID = response.payload["job_id"] as? String,
            let jobID = UUID(uuidString: rawJobID),
            let rawState = response.payload["status"] as? String,
            let state = IPCJobState(rawValue: rawState),
            response.payload["result"] == nil
                || response.payload["result"] is NSNull
                || response.payload["result"] is String,
            response.payload["error_code"] == nil
                || response.payload["error_code"] is NSNull
                || response.payload["error_code"] is String
        else {
            return nil
        }
        let result = response.payload["result"] as? String
        let errorCode = response.payload["error_code"] as? String
        let rawConfirmation = response.payload["confirmation"]
        let confirmation: IPCPendingConfirmation?
        if rawConfirmation == nil || rawConfirmation is NSNull {
            confirmation = nil
        } else {
            guard let parsed = IPCPendingConfirmation(object: rawConfirmation) else {
                return nil
            }
            confirmation = parsed
        }
        let partialResult = response.payload["partial_result"] as? String
        let streamVersion = response.payload["stream_version"] as? Int ?? 0
        let rawEvaluation = response.payload["evaluation"]
        let evaluation: IPCJobEvaluation?
        if rawEvaluation == nil || rawEvaluation is NSNull {
            evaluation = nil
        } else {
            guard let parsed = IPCJobEvaluation(object: rawEvaluation) else { return nil }
            evaluation = parsed
        }
        guard errorCode.map({
                $0.range(of: #"^[a-z][a-z0-9_]{2,63}$"#, options: .regularExpression) != nil
            }) ?? true,
            (0 ... 100_000).contains(streamVersion),
            partialResult.map({
                guard let encoded = try? JSONEncoder().encode($0) else { return false }
                return encoded.count <= Self.maximumResultBytes
            }) ?? true,
            (streamVersion > 0) == (partialResult != nil)
        else {
            return nil
        }
        if let result {
            guard
                let encoded = try? JSONEncoder().encode(result),
                encoded.count <= Self.maximumResultBytes
            else {
                return nil
            }
        }
        switch state {
        case .completed:
            guard
                result != nil,
                errorCode == nil,
                confirmation == nil,
                evaluation?.succeeded != false
            else { return nil }
        case .failed:
            guard
                result == nil,
                errorCode != nil,
                confirmation == nil,
                evaluation?.succeeded != true
            else { return nil }
        case .awaitingConfirmation:
            guard
                result == nil,
                errorCode == nil,
                confirmation != nil,
                evaluation == nil
            else { return nil }
        case .queued, .running:
            guard
                result == nil,
                errorCode == nil,
                confirmation == nil,
                evaluation == nil
            else { return nil }
        case .cancelled:
            guard
                result == nil,
                errorCode == nil,
                confirmation == nil,
                evaluation?.succeeded != true
            else { return nil }
        }
        self.jobID = jobID
        self.state = state
        self.result = result
        self.errorCode = errorCode
        self.confirmation = confirmation
        self.partialResult = partialResult
        self.streamVersion = streamVersion
        self.evaluation = evaluation
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

public enum IPCProviderCredentialState: String, Sendable {
    case configured
    case missing
    case unavailable
}

public enum IPCLocalModelState: String, Sendable {
    case available
    case unavailable
}

public struct IPCProviderStatusEvent: Equatable, Sendable {
    public let credential: IPCProviderCredentialState
    public let localModel: IPCLocalModelState

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            response.payload["provider"] as? String == "nvidia_nim",
            let rawCredential = response.payload["credential"] as? String,
            let credential = IPCProviderCredentialState(rawValue: rawCredential)
        else {
            return nil
        }
        self.credential = credential
        localModel = (response.payload["local_model"] as? String)
            .flatMap(IPCLocalModelState.init(rawValue:)) ?? .unavailable
    }
}

public enum IPCSecurityIntegrity: String, Sendable {
    case intact
    case compromised
}

public struct IPCSecurityStatusEvent: Equatable, Sendable {
    public let integrity: IPCSecurityIntegrity

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let rawState = response.payload["state"] as? String,
            let integrity = IPCSecurityIntegrity(rawValue: rawState)
        else {
            return nil
        }
        self.integrity = integrity
    }
}

public enum IPCSwarmAgentRole: String, Hashable, Sendable {
    case router
    case planner
    case criticalReasoner = "critical_reasoner"
    case codeSecurity = "code_security"
    case vision
    case omni
    case synthesizer
}

public struct IPCActiveAgent: Equatable, Sendable {
    public let role: IPCSwarmAgentRole
    public let activeJobs: Int

    init?(object: Any) {
        guard
            let object = object as? [String: Any],
            let rawRole = object["role"] as? String,
            let role = IPCSwarmAgentRole(rawValue: rawRole),
            let activeJobs = object["active_jobs"] as? Int,
            (1 ... 128).contains(activeJobs)
        else {
            return nil
        }
        self.role = role
        self.activeJobs = activeJobs
    }
}

public struct IPCSwarmActivityEvent: Equatable, Sendable {
    public let agents: [IPCActiveAgent]

    public init?(response: LocalIPCResponse) {
        guard response.ok, let agents = Self.parseAgents(response.payload) else {
            return nil
        }
        self.agents = agents
    }

    static func parseAgents(_ payload: [String: Any]) -> [IPCActiveAgent]? {
        guard
            let rawAgents = payload["agents"] as? [Any],
            rawAgents.count <= 7
        else {
            return nil
        }
        let agents = rawAgents.compactMap(IPCActiveAgent.init)
        let uniqueRoles = Set(agents.map(\.role))
        guard agents.count == rawAgents.count, uniqueRoles.count == agents.count else {
            return nil
        }
        return agents
    }
}

public struct IPCSwarmActivityUpdate: Equatable, Sendable {
    public let version: Int
    public let changed: Bool
    public let agents: [IPCActiveAgent]

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let version = response.payload["version"] as? Int,
            (0 ... 9_007_199_254_740_991).contains(version),
            let changed = response.payload["changed"] as? Bool,
            let agents = IPCSwarmActivityEvent.parseAgents(response.payload)
        else {
            return nil
        }
        self.version = version
        self.changed = changed
        self.agents = agents
    }
}

public struct IPCSpeechArtifactEvent: Equatable, Sendable {
    public static let maximumAudioBytes = 8_388_608

    public let token: String
    public let fileName: String
    public let sha256: String
    public let byteCount: Int

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let token = response.payload["token"] as? String,
            token.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil,
            let fileName = response.payload["file_name"] as? String,
            fileName == "jarvis-tts-\(token).wav",
            let sha256 = response.payload["sha256"] as? String,
            sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
            let byteCount = response.payload["byte_count"] as? Int,
            (44 ... Self.maximumAudioBytes).contains(byteCount)
        else {
            return nil
        }
        self.token = token
        self.fileName = fileName
        self.sha256 = sha256
        self.byteCount = byteCount
    }
}

public struct IPCSpeechStreamEvent: Equatable, Sendable {
    public static let maximumPCMBytes = 4_096
    public static let sampleRate = 22_050

    public let token: String
    public let sequence: Int
    public let pcm: Data
    public let done: Bool

    public init?(response: LocalIPCResponse) {
        guard
            response.ok,
            let token = response.payload["token"] as? String,
            token.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil,
            let sequence = response.payload["sequence"] as? Int,
            (1 ... 1_000_000).contains(sequence),
            let encoded = response.payload["pcm_base64"] as? String,
            encoded.utf8.count <= 5_464,
            let pcm = Data(base64Encoded: encoded),
            pcm.base64EncodedString() == encoded,
            pcm.count <= Self.maximumPCMBytes,
            pcm.count.isMultiple(of: 2),
            let done = response.payload["done"] as? Bool,
            !pcm.isEmpty || done,
            response.payload["sample_rate_hz"] as? Int == Self.sampleRate,
            response.payload["channels"] as? Int == 1,
            response.payload["sample_width_bytes"] as? Int == 2
        else {
            return nil
        }
        self.token = token
        self.sequence = sequence
        self.pcm = pcm
        self.done = done
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

enum IPCStreamFraming {
    static let magic = Data([0xAE, 0x15])
    static let headerBytes = 8
    static let authenticationTagBytes = 32
    static let maximumChunkBytes = 16_384

    enum FrameType: UInt8 {
        case start = 0x01
        case data = 0x02
        case end = 0x03
    }

    struct Header: Equatable {
        let type: FrameType
        let sequence: UInt16
        let payloadLength: Int
    }

    static func encodeFrames(
        _ payload: Data,
        secret: Data,
        legacyFrameBytes: Int,
        maxMessageBytes: Int
    ) throws -> [Data] {
        guard
            !payload.isEmpty,
            secret.count == 32,
            legacyFrameBytes > 0,
            maxMessageBytes >= legacyFrameBytes,
            payload.count <= maxMessageBytes
        else {
            throw LocalIPCError.frameTooLarge
        }
        if payload.count + 1 <= min(legacyFrameBytes, maximumChunkBytes) {
            var legacy = payload
            legacy.append(0x0A)
            return [legacy]
        }

        let sourceChunkCount = (payload.count + maximumChunkBytes - 1) / maximumChunkBytes
        let frameCount = sourceChunkCount == 1 ? 2 : sourceChunkCount
        guard frameCount <= Int(UInt16.max) + 1 else {
            throw LocalIPCError.frameTooLarge
        }

        let key = SymmetricKey(data: secret)
        var frames: [Data] = []
        frames.reserveCapacity(frameCount)
        for index in 0 ..< frameCount {
            let offset = index * maximumChunkBytes
            let chunk = index < sourceChunkCount
                ? payload.subdata(
                    in: offset ..< min(offset + maximumChunkBytes, payload.count)
                )
                : Data()
            let type: FrameType
            if index == 0 {
                type = .start
            } else if index == frameCount - 1 {
                type = .end
            } else {
                type = .data
            }
            let sequence = UInt16(index)
            let length = UInt16(chunk.count)
            var header = magic
            header.append(contentsOf: [
                type.rawValue,
                UInt8(sequence >> 8),
                UInt8(sequence & 0xFF),
                UInt8(length >> 8),
                UInt8(length & 0xFF),
                0x00,
            ])
            var authenticated = header
            authenticated.append(chunk)
            let tag = HMAC<SHA256>.authenticationCode(for: authenticated, using: key)
            var frame = authenticated
            frame.append(contentsOf: tag)
            frames.append(frame)
        }
        return frames
    }

    static func parseHeader(_ data: Data) throws -> Header {
        guard
            data.count == headerBytes,
            data.prefix(magic.count) == magic,
            data[7] == 0,
            let type = FrameType(rawValue: data[2])
        else {
            throw LocalIPCError.streamSequenceInvalid
        }
        let sequence = (UInt16(data[3]) << 8) | UInt16(data[4])
        let payloadLength = (Int(data[5]) << 8) | Int(data[6])
        guard payloadLength <= maximumChunkBytes else {
            throw LocalIPCError.frameTooLarge
        }
        return Header(type: type, sequence: sequence, payloadLength: payloadLength)
    }
}

public enum AudioRuntimeThermalState: String, Sendable {
    case nominal
    case fair
    case serious
    case critical
    case unknown

    public var allowsFullRuntime: Bool {
        self == .nominal || self == .fair
    }
}

public enum AudioRuntimeTransitionCause: String, Sendable {
    case thermalPause = "thermal_pause"
    case thermalThrottle = "thermal_throttle"
    case thermalRecovery = "thermal_recovery"
    case lowPowerMode = "low_power_mode"
    case lowPowerDisabled = "low_power_disabled"

    public var suspendsRuntime: Bool {
        self == .thermalPause || self == .thermalThrottle || self == .lowPowerMode
    }
}

public enum AudioRuntimePowerSource: String, Sendable {
    case ac
    case battery
    case ups
    case unknown
}

public enum TCCPrivacyPermission: String, Sendable {
    case screenRecording = "screen_recording"
    case accessibility
}

public enum TCCPrivacyOperation: String, Sendable {
    case screenTurn = "screen_turn"
    case computerControl = "computer_control"
}

public final class LocalIPCClient {
    public static let defaultSocketPath = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Aegis/aegis.sock").path

    private let socketPath: String
    private let secret: Data
    private let maxFrameBytes: Int
    private let maxMessageBytes: Int
    private let timeoutSeconds: TimeInterval
    private let clockSkewSeconds: TimeInterval
    private let now: () -> Date
    private let requestID: () -> UUID
    private let nonce: () throws -> String

    public convenience init(
        socketPath: String = LocalIPCClient.defaultSocketPath,
        secretStore: MacOSIPCSecretStore,
        maxFrameBytes: Int = 65_536,
        maxMessageBytes: Int = 1_048_576,
        timeoutSeconds: TimeInterval = 5,
        clockSkewSeconds: TimeInterval = 30
    ) throws {
        try self.init(
            socketPath: socketPath,
            secret: secretStore.get(),
            maxFrameBytes: maxFrameBytes,
            maxMessageBytes: maxMessageBytes,
            timeoutSeconds: timeoutSeconds,
            clockSkewSeconds: clockSkewSeconds
        )
    }

    public convenience init(
        socketPath: String = LocalIPCClient.defaultSocketPath,
        secret: Data,
        maxFrameBytes: Int = 65_536,
        maxMessageBytes: Int = 1_048_576,
        timeoutSeconds: TimeInterval = 5,
        clockSkewSeconds: TimeInterval = 30
    ) throws {
        try self.init(
            socketPath: socketPath,
            secret: secret,
            maxFrameBytes: maxFrameBytes,
            maxMessageBytes: maxMessageBytes,
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
        maxMessageBytes: Int,
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
            (maxFrameBytes ... 16_777_216).contains(maxMessageBytes),
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
        self.maxMessageBytes = maxMessageBytes
        self.timeoutSeconds = timeoutSeconds
        self.clockSkewSeconds = clockSkewSeconds
        self.now = now
        self.requestID = requestID
        self.nonce = nonce
    }

    public func health() throws -> LocalIPCResponse {
        try call(method: "health")
    }

    public func securityStatus() throws -> LocalIPCResponse {
        try call(method: "security.status")
    }

    public func providerStatus() throws -> LocalIPCResponse {
        try call(method: "provider.status")
    }

    public func runtimePreflight() throws -> LocalIPCResponse {
        try call(method: "runtime.preflight")
    }

    public func analyzeLocalizedVision(
        _ capture: LocalizedVisionCapture,
        targetDescription: String
    ) throws -> LocalizedVisionDecision {
        let normalized = targetDescription
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard
            !normalized.isEmpty,
            normalized.utf8.count <= 512,
            capture.imageData.count <= OnDemandVisionCapture.maximumEncodedBytes
        else {
            throw LocalIPCError.invalidConfiguration
        }
        let response = try call(
            method: "vision.localized.analyze",
            payload: [
                "image_png_base64": capture.imageData.base64EncodedString(),
                "target_description": normalized,
                "expected_x": Double(capture.expectedPointInCrop.x),
                "expected_y": Double(capture.expectedPointInCrop.y),
            ],
            responseTimeoutSeconds: 62
        )
        guard
            response.ok,
            let offsetX = response.payload["offset_x"] as? Double,
            let offsetY = response.payload["offset_y"] as? Double,
            let confidence = response.payload["confidence"] as? Double,
            let targetFound = response.payload["target_found"] as? Bool
        else {
            throw LocalIPCError.malformedResponse
        }
        return LocalizedVisionDecision(
            offsetX: offsetX,
            offsetY: offsetY,
            confidence: confidence,
            targetFound: targetFound
        )
    }

    public func notifyBiometricTrainingSampleReady(
        captureID: UUID
    ) throws -> LocalIPCResponse {
        try call(
            method: "biometric.training.sample_ready",
            payload: ["capture_id": captureID.uuidString.lowercased()]
        )
    }

    public func swarmActivity() throws -> LocalIPCResponse {
        try call(method: "swarm.activity")
    }

    public func waitForSwarmActivity(
        afterVersion: Int,
        timeoutMilliseconds: Int = 20_000
    ) throws -> LocalIPCResponse {
        guard
            (0 ... 9_007_199_254_740_991).contains(afterVersion),
            (100 ... 20_000).contains(timeoutMilliseconds)
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "swarm.wait",
            payload: [
                "after_version": afterVersion,
                "timeout_milliseconds": timeoutMilliseconds,
            ],
            responseTimeoutSeconds: TimeInterval(timeoutMilliseconds) / 1_000 + 2
        )
    }

    public func waitForComputerCommand(
        timeoutMilliseconds: Int = 20_000
    ) throws -> LocalIPCResponse {
        guard (100 ... 20_000).contains(timeoutMilliseconds) else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "computer.wait",
            payload: ["timeout_milliseconds": timeoutMilliseconds],
            responseTimeoutSeconds: TimeInterval(timeoutMilliseconds) / 1_000 + 2
        )
    }

    public func completeComputerCommand(
        _ commandID: UUID,
        response: [String: Any]
    ) throws -> LocalIPCResponse {
        guard
            JSONSerialization.isValidJSONObject(response),
            let status = response["status"] as? String,
            status == "ok" || status == "error",
            try Self.canonicalJSON(response).count <= 60_000
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "computer.complete",
            payload: [
                "command_id": commandID.uuidString.lowercased(),
                "response": response,
            ]
        )
    }

    public func synthesizeSpeech(_ text: String) throws -> LocalIPCResponse {
        let normalized = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard
            !normalized.isEmpty,
            normalized.unicodeScalars.count <= 2_000,
            normalized.utf8.count <= 8_192
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "speech.synthesize",
            payload: ["text": normalized],
            responseTimeoutSeconds: 33
        )
    }

    public func openSpeechStream(
        _ text: String,
        groupToken: String
    ) throws -> LocalIPCResponse {
        let normalized = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard
            !normalized.isEmpty,
            normalized.unicodeScalars.count <= 2_000,
            normalized.utf8.count <= 8_192,
            groupToken.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "speech.stream.open",
            payload: ["text": normalized, "group_token": groupToken],
            responseTimeoutSeconds: 33
        )
    }

    public func nextSpeechStream(
        token: String,
        afterSequence: Int
    ) throws -> LocalIPCResponse {
        guard
            token.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil,
            (1 ... 1_000_000).contains(afterSequence)
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "speech.stream.next",
            payload: [
                "token": token,
                "after_sequence": afterSequence,
            ],
            responseTimeoutSeconds: 33
        )
    }

    public func closeSpeechStream(_ token: String) throws -> LocalIPCResponse {
        guard token.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(method: "speech.stream.close", payload: ["token": token])
    }

    public func cancelSpeechStreams(groupToken: String) throws -> LocalIPCResponse {
        guard groupToken.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "speech.stream.cancel",
            payload: ["group_token": groupToken]
        )
    }

    public func releaseSpeechArtifact(_ token: String) throws -> LocalIPCResponse {
        guard token.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(method: "speech.release", payload: ["token": token])
    }

    public func createConversation() throws -> LocalIPCResponse {
        try call(method: "conversations.create")
    }

    public func submitVoiceTranscript(
        _ transcript: SpeechTranscriptEvent,
        conversationID: UUID? = nil,
        persistConversation: Bool = false
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
        if persistConversation {
            payload["persist_conversation"] = true
        }
        return try call(method: "voice.submit", payload: payload)
    }

    public func submitImage(
        text: String,
        image: LocalImageAttachment,
        voiceContext: SpeechTranscriptEvent? = nil,
        conversationID: UUID? = nil,
        persistConversation: Bool = false
    ) throws -> LocalIPCResponse {
        guard
            !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            text.unicodeScalars.count <= 4_096
        else {
            throw LocalIPCError.invalidConfiguration
        }
        var payload: [String: Any] = [
            "text": text,
            "image": [
                "media_type": image.mediaType,
                "data_base64": image.data.base64EncodedString(),
            ],
        ]
        if let voiceContext {
            guard voiceContext.isFinal, voiceContext.onDevice, voiceContext.text == text else {
                throw LocalIPCError.invalidConfiguration
            }
            var context: [String: Any] = [
                "schema_version": "1.0",
                "type": "speech.context",
                "capture_id": voiceContext.captureID.uuidString.lowercased(),
                "locale_identifier": voiceContext.localeIdentifier,
                "is_final": true,
                "on_device": true,
                "sole_speaker_profile": voiceContext.soleSpeakerProfile,
                "owner_speaker_profile": voiceContext.ownerSpeakerProfile,
                "owner_presence_verified": voiceContext.ownerPresenceVerified,
            ]
            if let speakerID = voiceContext.speakerID,
               let speakerConfidence = voiceContext.speakerConfidence
            {
                context["speaker_id"] = speakerID
                context["speaker_confidence"] = speakerConfidence
            }
            payload["voice_context"] = context
        }
        if let conversationID {
            payload["conversation_id"] = conversationID.uuidString.lowercased()
        }
        if persistConversation {
            payload["persist_conversation"] = true
        }
        return try call(method: "image.submit", payload: payload)
    }

    public func jobStatus(_ jobID: UUID) throws -> LocalIPCResponse {
        try call(method: "jobs.status", payload: ["job_id": jobID.uuidString.lowercased()])
    }

    public func waitForJobChange(
        _ jobID: UUID,
        afterStreamVersion: Int,
        timeoutMilliseconds: Int = 20_000
    ) throws -> LocalIPCResponse {
        guard
            (0 ... 100_000).contains(afterStreamVersion),
            (100 ... 20_000).contains(timeoutMilliseconds)
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "jobs.wait",
            payload: [
                "job_id": jobID.uuidString.lowercased(),
                "after_stream_version": afterStreamVersion,
                "timeout_milliseconds": timeoutMilliseconds,
            ],
            responseTimeoutSeconds: TimeInterval(timeoutMilliseconds) / 1_000 + 2
        )
    }

    public func approveJob(_ jobID: UUID, callDigest: String) throws -> LocalIPCResponse {
        guard
            callDigest.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "jobs.approve",
            payload: [
                "job_id": jobID.uuidString.lowercased(),
                "call_digest": callDigest,
            ]
        )
    }

    public func cancelJob(_ jobID: UUID) throws -> LocalIPCResponse {
        try call(method: "jobs.cancel", payload: ["job_id": jobID.uuidString.lowercased()])
    }

    public func reportTCCPermissionDenied(
        permission: TCCPrivacyPermission,
        operation: TCCPrivacyOperation,
        jobID: UUID? = nil
    ) throws -> LocalIPCResponse {
        var payload: [String: Any] = [
            "error_code": "tcc_permission_denied",
            "permission": permission.rawValue,
            "operation": operation.rawValue,
        ]
        if let jobID {
            payload["job_id"] = jobID.uuidString.lowercased()
        }
        return try call(method: "privacy.permission.denied", payload: payload)
    }

    public func updateAudioRuntimeState(
        sourceID: UUID,
        sequence: Int,
        cause: AudioRuntimeTransitionCause,
        thermalState: AudioRuntimeThermalState,
        lowPowerMode: Bool,
        powerSource: AudioRuntimePowerSource = .unknown
    ) throws -> LocalIPCResponse {
        guard (0 ... 9_007_199_254_740_991).contains(sequence) else {
            throw LocalIPCError.invalidConfiguration
        }
        switch cause {
        case .thermalPause, .thermalThrottle:
            guard !thermalState.allowsFullRuntime else {
                throw LocalIPCError.invalidConfiguration
            }
        case .lowPowerMode:
            guard lowPowerMode else {
                throw LocalIPCError.invalidConfiguration
            }
        case .thermalRecovery, .lowPowerDisabled:
            guard thermalState.allowsFullRuntime, !lowPowerMode else {
                throw LocalIPCError.invalidConfiguration
            }
        }
        return try call(
            method: cause.suspendsRuntime ? "audio.session.close" : "audio.session.resume",
            payload: [
                "source_id": sourceID.uuidString.lowercased(),
                "sequence": sequence,
                "cause": cause.rawValue,
                "thermal_state": thermalState.rawValue,
                "low_power_mode": lowPowerMode,
                "power_source": powerSource.rawValue,
            ]
        )
    }

    public func recordSystemAuditEvent(
        _ eventType: String,
        component: String,
        data: [String: Any]
    ) throws -> LocalIPCResponse {
        let allowedEvents = Set([
            "noise_floor_transition",
            "thermal_pause",
            "thermal_resume",
            "voice_interruption",
        ])
        guard
            allowedEvents.contains(eventType),
            component == "acoustic_sensor",
            data.count <= 16,
            data.allSatisfy({ key, value in
                guard
                    key.range(of: #"^[a-z][a-z0-9_]{1,63}$"#, options: .regularExpression)
                        != nil
                else {
                    return false
                }
                switch value {
                case let text as String:
                    return text.utf8.count <= 256
                case is Bool:
                    return true
                case let number as Int:
                    return (-9_007_199_254_740_991 ... 9_007_199_254_740_991)
                        .contains(number)
                case is NSNull:
                    return true
                default:
                    return false
                }
            })
        else {
            throw LocalIPCError.invalidConfiguration
        }
        return try call(
            method: "audit.system.event",
            payload: [
                "event_type": eventType,
                "component": component,
                "data": data,
            ]
        )
    }

    public func call(
        method: String,
        payload: [String: Any] = [:]
    ) throws -> LocalIPCResponse {
        try call(
            method: method,
            payload: payload,
            responseTimeoutSeconds: timeoutSeconds
        )
    }

    private func call(
        method: String,
        payload: [String: Any],
        responseTimeoutSeconds: TimeInterval
    ) throws -> LocalIPCResponse {
        guard
            method.range(
                of: #"^[a-z][a-z0-9_.-]{1,63}$"#,
                options: .regularExpression
            ) != nil,
            JSONSerialization.isValidJSONObject(payload),
            responseTimeoutSeconds.isFinite,
            (0.1 ... 35).contains(responseTimeoutSeconds)
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
        let payloadData = try Self.canonicalJSON(envelope)
        let frames = try IPCStreamFraming.encodeFrames(
            payloadData,
            secret: secret,
            legacyFrameBytes: maxFrameBytes,
            maxMessageBytes: maxMessageBytes
        )

        let responseFrame = try exchange(frames, timeoutSeconds: responseTimeoutSeconds)
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

    private func exchange(_ requestFrames: [Data], timeoutSeconds: TimeInterval) throws -> Data {
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
        for frame in requestFrames {
            try writeAll(frame, to: descriptor)
        }
        return try readMessage(from: descriptor)
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

    private func readMessage(from descriptor: Int32) throws -> Data {
        let prefix = try readExactly(2, from: descriptor)
        if prefix == IPCStreamFraming.magic {
            return try readStream(firstMagic: prefix, from: descriptor)
        }

        var response = prefix
        var buffer = [UInt8](repeating: 0, count: 4_096)
        while true {
            let count = buffer.withUnsafeMutableBytes { storage in
                Darwin.read(descriptor, storage.baseAddress, storage.count)
            }
            if count > 0 {
                response.append(contentsOf: buffer.prefix(count))
                guard response.count <= min(
                    maxFrameBytes,
                    IPCStreamFraming.maximumChunkBytes
                ) else {
                    throw LocalIPCError.frameTooLarge
                }
                if let newline = response.firstIndex(of: 0x0A) {
                    guard newline == response.index(before: response.endIndex) else {
                        throw LocalIPCError.malformedResponse
                    }
                    return Data(response[..<newline])
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

    private func readStream(firstMagic: Data, from descriptor: Int32) throws -> Data {
        var magic = firstMagic
        var expectedSequence: UInt16 = 0
        var response = Data()
        while true {
            let suffix = try readExactly(
                IPCStreamFraming.headerBytes - magic.count,
                from: descriptor
            )
            var header = magic
            header.append(suffix)
            let parsed = try IPCStreamFraming.parseHeader(header)
            guard parsed.sequence == expectedSequence else {
                throw LocalIPCError.streamSequenceInvalid
            }
            if expectedSequence == 0 {
                guard parsed.type == .start else {
                    throw LocalIPCError.streamSequenceInvalid
                }
            } else {
                guard parsed.type != .start else {
                    throw LocalIPCError.streamSequenceInvalid
                }
            }

            let payload = try readExactly(parsed.payloadLength, from: descriptor)
            let tag = try readExactly(IPCStreamFraming.authenticationTagBytes, from: descriptor)
            var authenticated = header
            authenticated.append(payload)
            guard HMAC<SHA256>.isValidAuthenticationCode(
                tag,
                authenticating: authenticated,
                using: SymmetricKey(data: secret)
            ) else {
                throw LocalIPCError.streamAuthenticationFailed
            }
            guard response.count <= maxMessageBytes - payload.count else {
                throw LocalIPCError.frameTooLarge
            }
            response.append(payload)
            if parsed.type == .end {
                guard !response.isEmpty else {
                    throw LocalIPCError.malformedResponse
                }
                return response
            }
            guard
                parsed.type == (expectedSequence == 0 ? .start : .data),
                expectedSequence < UInt16.max
            else {
                throw LocalIPCError.streamSequenceInvalid
            }
            expectedSequence += 1
            magic = try readExactly(2, from: descriptor)
            guard magic == IPCStreamFraming.magic else {
                throw LocalIPCError.streamSequenceInvalid
            }
        }
    }

    private func readExactly(_ byteCount: Int, from descriptor: Int32) throws -> Data {
        guard byteCount >= 0 else { throw LocalIPCError.malformedResponse }
        var result = Data(count: byteCount)
        var offset = 0
        while offset < byteCount {
            let count = result.withUnsafeMutableBytes { storage in
                Darwin.read(
                    descriptor,
                    storage.baseAddress?.advanced(by: offset),
                    byteCount - offset
                )
            }
            if count > 0 {
                offset += count
            } else if count == 0 {
                throw LocalIPCError.malformedResponse
            } else if errno == EINTR {
                continue
            } else {
                throw LocalIPCError.connectionFailed
            }
        }
        return result
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
