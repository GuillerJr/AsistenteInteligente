import AegisAudioCore
import Darwin
import Foundation
import HuggingFace
import MLX
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import Tokenizers

private enum EngineFailure: Error {
    case invalidConfiguration
    case invalidRequest
    case responseTooLarge
}

private struct SessionEntry {
    let session: ChatSession
    let instructions: String?
    var lastUsed: ContinuousClock.Instant
}

private actor MLXRuntime {
    private let modelID: String
    private let draftModelID: String?
    private let draftModelBytes: Int
    private var model: ModelContainer?
    private var sessions: [String: SessionEntry] = [:]
    private let clock = ContinuousClock()

    init(environment: [String: String]) throws {
        let configuredModel = environment["AEGIS_MLX_MODEL_ID"]
            ?? "mlx-community/Qwen2.5-3B-Instruct-4bit"
        modelID = try MLXLocalEngineProtocol.validatedModelID(configuredModel)
        if let configuredDraft = environment["AEGIS_MLX_DRAFT_MODEL_ID"],
           !configuredDraft.isEmpty
        {
            draftModelID = try MLXLocalEngineProtocol.validatedModelID(configuredDraft)
        } else {
            draftModelID = nil
        }
        let rawEstimate = environment["AEGIS_MLX_DRAFT_MODEL_BYTES"]
            .flatMap(Int.init) ?? 0
        guard rawEstimate >= 0, rawEstimate <= 8 * 1_024 * 1_024 * 1_024 else {
            throw EngineFailure.invalidConfiguration
        }
        draftModelBytes = rawEstimate
        guard (draftModelID == nil) == (draftModelBytes == 0) else {
            throw EngineFailure.invalidConfiguration
        }
    }

    func handle(_ request: MLXLocalEngineRequest) async -> MLXLocalEngineResponse {
        do {
            try MLXLocalEngineProtocol.validate(request)
            switch request.method {
            case .status:
                return MLXLocalEngineResponse(
                    requestID: request.requestID,
                    success: true,
                    modelID: modelID,
                    content: model == nil ? "ready" : "loaded",
                    cacheReused: false
                )
            case .resetConversation:
                let conversationID = try MLXLocalEngineProtocol.validatedConversationID(
                    request.conversationID
                )
                if let entry = sessions.removeValue(forKey: conversationID) {
                    await entry.session.clear()
                    Memory.clearCache()
                }
                return MLXLocalEngineResponse(
                    requestID: request.requestID,
                    success: true,
                    modelID: modelID,
                    content: "reset"
                )
            case .generate:
                return try await generate(request)
            case .verifyDraft:
                return try await verifyDraft(request)
            }
        } catch let error as MLXLocalEngineValidationError {
            return failure(requestID: request.requestID, code: validationCode(error))
        } catch is SpeculativeDecodingMemoryError {
            return failure(requestID: request.requestID, code: "memory_policy_denied")
        } catch {
            return failure(requestID: request.requestID, code: "inference_failed")
        }
    }

    private func generate(
        _ request: MLXLocalEngineRequest
    ) async throws -> MLXLocalEngineResponse {
        let conversationID = try MLXLocalEngineProtocol.validatedConversationID(
            request.conversationID
        )
        guard let prompt = request.prompt,
              let maximumTokens = request.maximumTokens,
              let temperature = request.temperature
        else {
            throw EngineFailure.invalidRequest
        }
        let container = try await loadModel()
        let cacheReused: Bool
        let session: ChatSession
        if let existing = sessions[conversationID],
           existing.instructions == request.systemInstructions
        {
            cacheReused = true
            session = existing.session
        } else {
            if let previous = sessions.removeValue(forKey: conversationID) {
                await previous.session.clear()
            }
            try await evictSessionIfNecessary()
            cacheReused = false
            session = ChatSession(
                container,
                instructions: request.systemInstructions,
                speculativeDecoding: speculativeConfiguration(),
                generateParameters: generationParameters(
                    maximumTokens: maximumTokens,
                    temperature: temperature
                )
            )
        }
        session.generateParameters = generationParameters(
            maximumTokens: maximumTokens,
            temperature: temperature
        )
        var content = ""
        for try await detail in session.streamDetails(to: prompt) {
            if case .chunk(let chunk) = detail {
                guard content.lengthOfBytes(using: .utf8)
                    + chunk.lengthOfBytes(using: .utf8)
                    <= MLXLocalEngineProtocol.maximumResponseBytes / 2
                else {
                    await session.clear()
                    sessions.removeValue(forKey: conversationID)
                    throw EngineFailure.responseTooLarge
                }
                content.append(chunk)
            }
        }
        sessions[conversationID] = SessionEntry(
            session: session,
            instructions: request.systemInstructions,
            lastUsed: clock.now
        )
        return MLXLocalEngineResponse(
            requestID: request.requestID,
            success: true,
            modelID: modelID,
            content: content,
            cacheReused: cacheReused
        )
    }

    private func verifyDraft(
        _ request: MLXLocalEngineRequest
    ) async throws -> MLXLocalEngineResponse {
        guard let prompt = request.prompt,
              let draftTokenIDs = request.draftTokenIDs
        else {
            throw EngineFailure.invalidRequest
        }
        let container = try await loadModel()
        var messages: [Chat.Message] = []
        if let instructions = request.systemInstructions, !instructions.isEmpty {
            messages.append(.system(instructions))
        }
        messages.append(.init(role: .user, content: prompt))
        let prepared = try await container.prepare(input: UserInput(chat: messages))
        let verification = try await container.perform(
            nonSendable: prepared
        ) { context, input in
            guard draftTokenIDs.allSatisfy({
                context.tokenizer.convertIdToToken($0) != nil
            }) else {
                throw EngineFailure.invalidRequest
            }
            let promptTokens = input.text.tokens.asType(.int32).flattened()
            guard promptTokens.size > 0 else {
                throw EngineFailure.invalidRequest
            }
            let draftTokens = MLXArray(draftTokenIDs).asType(.int32)
            let combined = concatenated([promptTokens, draftTokens])
            let output = context.model(
                LMInput.Text(tokens: combined[.newAxis]),
                cache: nil,
                state: nil
            )
            let start = promptTokens.size - 1
            let end = start + draftTokenIDs.count
            let predictions = output.logits[0..., start ..< end, 0...]
                .squeezed(axis: 0)
                .argMax(axis: -1)
                .asArray(Int.self)
            var accepted = 0
            while accepted < draftTokenIDs.count,
                  accepted < predictions.count,
                  predictions[accepted] == draftTokenIDs[accepted]
            {
                accepted += 1
            }
            return MLXDraftVerification(
                acceptedTokenCount: accepted,
                correctionTokenID: accepted < predictions.count
                    ? predictions[accepted]
                    : nil,
                verifiedTokenCount: predictions.count
            )
        }
        Memory.clearCache()
        return MLXLocalEngineResponse(
            requestID: request.requestID,
            success: true,
            modelID: modelID,
            cacheReused: false,
            verification: verification
        )
    }

    private func loadModel() async throws -> ModelContainer {
        if let model {
            return model
        }
        let configuration = ModelConfiguration(id: modelID)
        let loaded = try await #huggingFaceLoadModelContainer(configuration: configuration)
        model = loaded
        return loaded
    }

    private func speculativeConfiguration() -> SpeculativeDecodingConfig? {
        guard let draftModelID, draftModelBytes > 0 else {
            return nil
        }
        return SpeculativeDecodingConfig(
            draftModelBytes: draftModelBytes,
            numDraftTokens: 5,
            memoryPolicy: .recommendedWorkingSet
        ) {
            let configuration = ModelConfiguration(id: draftModelID)
            return try await #huggingFaceLoadModelContainer(configuration: configuration)
        }
    }

    private func generationParameters(
        maximumTokens: Int,
        temperature: Double
    ) -> GenerateParameters {
        GenerateParameters(
            maxTokens: maximumTokens,
            maxKVSize: MLXLocalEngineProtocol.maximumKVTokens,
            kvBits: 4,
            temperature: Float(temperature),
            topP: temperature == 0 ? 1.0 : 0.9,
            repetitionPenalty: 1.05,
            prefillStepSize: 512
        )
    }

    private func evictSessionIfNecessary() async throws {
        guard sessions.count >= MLXLocalEngineProtocol.maximumSessions,
              let oldest = sessions.min(by: { $0.value.lastUsed < $1.value.lastUsed })
        else {
            return
        }
        sessions.removeValue(forKey: oldest.key)
        await oldest.value.session.clear()
        Memory.clearCache()
    }

    private func failure(requestID: UUID, code: String) -> MLXLocalEngineResponse {
        MLXLocalEngineResponse(
            requestID: requestID,
            success: false,
            modelID: modelID,
            errorCode: code
        )
    }

    private func validationCode(_ error: MLXLocalEngineValidationError) -> String {
        switch error {
        case .incompatibleProtocol: "incompatible_protocol"
        case .invalidConversationID: "invalid_conversation_id"
        case .invalidDraft: "invalid_draft"
        case .invalidModelID: "invalid_model_id"
        case .invalidTemperature: "invalid_temperature"
        case .invalidTokenBudget: "invalid_token_budget"
        case .missingPrompt: "missing_prompt"
        case .textSize: "text_size_invalid"
        case .unexpectedField: "unexpected_field"
        case .frameSize: "frame_size_invalid"
        }
    }
}

private enum FramedStandardIO {
    static func readRequest() throws -> MLXLocalEngineRequest? {
        guard let header = try readExactly(4) else {
            return nil
        }
        let length = try MLXLocalEngineProtocol.decodeFrameLength(
            header,
            maximumBytes: MLXLocalEngineProtocol.maximumRequestBytes
        )
        guard let payload = try readExactly(length) else {
            throw EngineFailure.invalidRequest
        }
        return try JSONDecoder().decode(MLXLocalEngineRequest.self, from: payload)
    }

    static func writeResponse(_ response: MLXLocalEngineResponse) throws {
        let payload = try JSONEncoder().encode(response)
        let frame = try MLXLocalEngineProtocol.frame(
            payload,
            maximumBytes: MLXLocalEngineProtocol.maximumResponseBytes
        )
        FileHandle.standardOutput.write(frame)
        try FileHandle.standardOutput.synchronize()
    }

    private static func readExactly(_ count: Int) throws -> Data? {
        var result = Data()
        result.reserveCapacity(count)
        while result.count < count {
            guard let chunk = try FileHandle.standardInput.read(
                upToCount: count - result.count
            ), !chunk.isEmpty
            else {
                if result.isEmpty {
                    return nil
                }
                throw EngineFailure.invalidRequest
            }
            result.append(chunk)
        }
        return result
    }
}

@main
private enum JarvisMLXEngineMain {
    static func main() async {
        do {
            let runtime = try MLXRuntime(environment: ProcessInfo.processInfo.environment)
            if CommandLine.arguments.dropFirst() == ["--status"] {
                let request = MLXLocalEngineRequest(
                    requestID: UUID(),
                    method: .status
                )
                let response = await runtime.handle(request)
                let data = try JSONEncoder().encode(response)
                FileHandle.standardOutput.write(data)
                return
            }
            guard CommandLine.arguments.count == 1 else {
                exit(EX_USAGE)
            }
            while let request = try FramedStandardIO.readRequest() {
                let response = await runtime.handle(request)
                try FramedStandardIO.writeResponse(response)
            }
        } catch {
            exit(EX_PROTOCOL)
        }
    }
}
