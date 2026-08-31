import Foundation

public enum MLXEngineMethod: String, Codable, Sendable {
    case status
    case generate
    case verifyDraft = "verify_draft"
    case resetConversation = "reset_conversation"
}

public struct MLXLocalEngineRequest: Codable, Sendable, Equatable {
    public let protocolVersion: String
    public let requestID: UUID
    public let method: MLXEngineMethod
    public let conversationID: String?
    public let systemInstructions: String?
    public let prompt: String?
    public let maximumTokens: Int?
    public let temperature: Double?
    public let draftTokenIDs: [Int]?

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case method
        case conversationID = "conversation_id"
        case systemInstructions = "system_instructions"
        case prompt
        case maximumTokens = "maximum_tokens"
        case temperature
        case draftTokenIDs = "draft_token_ids"
    }

    public init(
        protocolVersion: String = MLXLocalEngineProtocol.version,
        requestID: UUID,
        method: MLXEngineMethod,
        conversationID: String? = nil,
        systemInstructions: String? = nil,
        prompt: String? = nil,
        maximumTokens: Int? = nil,
        temperature: Double? = nil,
        draftTokenIDs: [Int]? = nil
    ) {
        self.protocolVersion = protocolVersion
        self.requestID = requestID
        self.method = method
        self.conversationID = conversationID
        self.systemInstructions = systemInstructions
        self.prompt = prompt
        self.maximumTokens = maximumTokens
        self.temperature = temperature
        self.draftTokenIDs = draftTokenIDs
    }
}

public struct MLXDraftVerification: Codable, Sendable, Equatable {
    public let acceptedTokenCount: Int
    public let correctionTokenID: Int?
    public let verifiedTokenCount: Int

    enum CodingKeys: String, CodingKey {
        case acceptedTokenCount = "accepted_token_count"
        case correctionTokenID = "correction_token_id"
        case verifiedTokenCount = "verified_token_count"
    }

    public init(
        acceptedTokenCount: Int,
        correctionTokenID: Int?,
        verifiedTokenCount: Int
    ) {
        self.acceptedTokenCount = acceptedTokenCount
        self.correctionTokenID = correctionTokenID
        self.verifiedTokenCount = verifiedTokenCount
    }
}

public struct MLXLocalEngineResponse: Codable, Sendable, Equatable {
    public let protocolVersion: String
    public let requestID: UUID
    public let success: Bool
    public let modelID: String
    public let content: String?
    public let cacheReused: Bool
    public let verification: MLXDraftVerification?
    public let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case success
        case modelID = "model_id"
        case content
        case cacheReused = "cache_reused"
        case verification
        case errorCode = "error_code"
    }

    public init(
        requestID: UUID,
        success: Bool,
        modelID: String,
        content: String? = nil,
        cacheReused: Bool = false,
        verification: MLXDraftVerification? = nil,
        errorCode: String? = nil
    ) {
        self.protocolVersion = MLXLocalEngineProtocol.version
        self.requestID = requestID
        self.success = success
        self.modelID = modelID
        self.content = content
        self.cacheReused = cacheReused
        self.verification = verification
        self.errorCode = errorCode
    }
}

public enum MLXLocalEngineProtocol {
    public static let version = "1.0"
    public static let maximumRequestBytes = 65_536
    public static let maximumResponseBytes = 262_144
    public static let maximumSessions = 2
    public static let maximumPromptBytes = 24_576
    public static let maximumSystemInstructionBytes = 8_192
    public static let maximumGeneratedTokens = 1_024
    public static let maximumDraftTokens = 64
    public static let maximumKVTokens = 4_096

    public static func validate(_ request: MLXLocalEngineRequest) throws {
        guard request.protocolVersion == version else {
            throw MLXLocalEngineValidationError.incompatibleProtocol
        }
        switch request.method {
        case .status:
            break
        case .resetConversation:
            _ = try validatedConversationID(request.conversationID)
        case .generate:
            _ = try validatedConversationID(request.conversationID)
            try validateText(
                request.systemInstructions,
                maximumBytes: maximumSystemInstructionBytes,
                required: false
            )
            try validateText(
                request.prompt,
                maximumBytes: maximumPromptBytes,
                required: true
            )
            guard let maximumTokens = request.maximumTokens,
                  (1 ... maximumGeneratedTokens).contains(maximumTokens)
            else {
                throw MLXLocalEngineValidationError.invalidTokenBudget
            }
            guard let temperature = request.temperature,
                  temperature.isFinite,
                  (0.0 ... 2.0).contains(temperature)
            else {
                throw MLXLocalEngineValidationError.invalidTemperature
            }
            guard request.draftTokenIDs == nil else {
                throw MLXLocalEngineValidationError.unexpectedField
            }
        case .verifyDraft:
            try validateText(
                request.systemInstructions,
                maximumBytes: maximumSystemInstructionBytes,
                required: false
            )
            try validateText(
                request.prompt,
                maximumBytes: maximumPromptBytes,
                required: true
            )
            guard let draftTokenIDs = request.draftTokenIDs,
                  !draftTokenIDs.isEmpty,
                  draftTokenIDs.count <= maximumDraftTokens,
                  draftTokenIDs.allSatisfy({ $0 >= 0 })
            else {
                throw MLXLocalEngineValidationError.invalidDraft
            }
        }
    }

    public static func validatedModelID(_ value: String) throws -> String {
        let pattern = #"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"#
        guard value.range(of: pattern, options: .regularExpression) != nil else {
            throw MLXLocalEngineValidationError.invalidModelID
        }
        return value
    }

    public static func validatedConversationID(_ value: String?) throws -> String {
        guard let value,
              value.range(
                  of: #"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"#,
                  options: .regularExpression
              ) != nil
        else {
            throw MLXLocalEngineValidationError.invalidConversationID
        }
        return value
    }

    public static func frame(_ payload: Data, maximumBytes: Int) throws -> Data {
        guard !payload.isEmpty, payload.count <= maximumBytes else {
            throw MLXLocalEngineValidationError.frameSize
        }
        var length = UInt32(payload.count).bigEndian
        var result = Data(bytes: &length, count: MemoryLayout<UInt32>.size)
        result.append(payload)
        return result
    }

    public static func decodeFrameLength(_ data: Data, maximumBytes: Int) throws -> Int {
        guard data.count == MemoryLayout<UInt32>.size else {
            throw MLXLocalEngineValidationError.frameSize
        }
        let value = data.reduce(UInt32.zero) { ($0 << 8) | UInt32($1) }
        guard value > 0, value <= UInt32(maximumBytes) else {
            throw MLXLocalEngineValidationError.frameSize
        }
        return Int(value)
    }

    private static func validateText(
        _ value: String?,
        maximumBytes: Int,
        required: Bool
    ) throws {
        guard let value else {
            if required {
                throw MLXLocalEngineValidationError.missingPrompt
            }
            return
        }
        let size = value.lengthOfBytes(using: .utf8)
        guard size <= maximumBytes, (!required || size > 0) else {
            throw MLXLocalEngineValidationError.textSize
        }
    }
}

public enum MLXLocalEngineValidationError: Error, Sendable {
    case incompatibleProtocol
    case invalidConversationID
    case invalidDraft
    case invalidModelID
    case invalidTemperature
    case invalidTokenBudget
    case missingPrompt
    case textSize
    case unexpectedField
    case frameSize
}
