import AegisAudioCore
import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

private struct LocalBrainRequest: Decodable {
    let instructions: String
    let prompt: String
    let maximumResponseTokens: Int?
    let temperature: Double?
    let toolAugmented: Bool?
}

private struct StatusResponse: Encodable {
    let available: Bool
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case available
        case protocolVersion = "protocol_version"
    }
}

private struct StreamResponse: Encodable {
    let type: String
    let content: String
}

private struct PersistentLocalBrainRequest: Decodable {
    let requestID: String
    let instructions: String
    let prompt: String
    let maximumResponseTokens: Int?
    let temperature: Double?
    let toolAugmented: Bool?

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case instructions
        case prompt
        case maximumResponseTokens
        case temperature
        case toolAugmented
    }

    var request: LocalBrainRequest {
        LocalBrainRequest(
            instructions: instructions,
            prompt: prompt,
            maximumResponseTokens: maximumResponseTokens,
            temperature: temperature,
            toolAugmented: toolAugmented
        )
    }
}

private struct PersistentReadyResponse: Encodable {
    let protocolVersion = "2.0"
    let type = "ready"

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case type
    }
}

private struct PersistentStreamResponse: Encodable {
    let requestID: String
    let type: String
    let content: String

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case type
        case content
    }
}

@main
private enum JarvisLocalBrain {
    private static let maximumInputBytes = 24_576
    private static let maximumResponseTokens = 4_096
    private static let maximumTemperature = 2.0
    private static let persistentProtocolVersion = "2.0"

    static func main() async {
        if CommandLine.arguments.dropFirst() == ["--status"] {
            let available = modelAvailable()
            write(
                StatusResponse(
                    available: available,
                    protocolVersion: persistentProtocolVersion
                )
            )
            exit(available ? 0 : 1)
        }
        if CommandLine.arguments.dropFirst() == ["--serve-stdio"] {
            await servePersistentRequests()
            return
        }
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard
            !data.isEmpty,
            data.count <= maximumInputBytes,
            let request = try? JSONDecoder().decode(LocalBrainRequest.self, from: data),
            valid(request.instructions),
            valid(request.prompt),
            valid(request.maximumResponseTokens),
            valid(request.temperature)
        else {
            exit(64)
        }
        #if canImport(FoundationModels)
        if #available(macOS 26.0, *), SystemLanguageModel.default.isAvailable {
            do {
                let helper = LocalInferenceHelper()
                if request.toolAugmented == true {
                    let secret = try MacOSIPCSecretStore().get()
                    let latest = try await helper.generateToolAugmented(
                        instructions: request.instructions,
                        prompt: request.prompt,
                        ipcSecret: secret,
                        maximumResponseTokens: request.maximumResponseTokens,
                        temperature: request.temperature
                    ) { content in
                        write(StreamResponse(type: "snapshot", content: content))
                    }
                    try writeCompleted(latest)
                    return
                }
                let latest = try await helper.generateDraft(
                    instructions: request.instructions,
                    prompt: request.prompt,
                    maximumResponseTokens: request.maximumResponseTokens,
                    temperature: request.temperature
                ) { content in
                    write(StreamResponse(type: "snapshot", content: content))
                }
                try writeCompleted(latest)
                return
            } catch {
                exit(69)
            }
        }
        #endif
        exit(69)
    }

    private static func servePersistentRequests() async {
        #if canImport(FoundationModels)
        guard #available(macOS 26.0, *), SystemLanguageModel.default.isAvailable else {
            exit(69)
        }
        let helper = LocalInferenceHelper()
        try? await helper.prewarm(
            instructions: "Responde en español con precisión, brevedad y privacidad local."
        )
        write(PersistentReadyResponse())
        while let line = readLine(strippingNewline: true) {
            guard
                !line.isEmpty,
                line.utf8.count <= maximumInputBytes,
                let encoded = line.data(using: .utf8),
                let envelope = try? JSONDecoder().decode(
                    PersistentLocalBrainRequest.self,
                    from: encoded
                ),
                UUID(uuidString: envelope.requestID) != nil,
                valid(envelope.request.instructions),
                valid(envelope.request.prompt),
                valid(envelope.request.maximumResponseTokens),
                valid(envelope.request.temperature)
            else {
                exit(64)
            }
            do {
                let request = envelope.request
                let latest: String
                if request.toolAugmented == true {
                    let secret = try MacOSIPCSecretStore().get()
                    latest = try await helper.generateToolAugmented(
                        instructions: request.instructions,
                        prompt: request.prompt,
                        ipcSecret: secret,
                        maximumResponseTokens: request.maximumResponseTokens,
                        temperature: request.temperature
                    ) { content in
                        write(
                            PersistentStreamResponse(
                                requestID: envelope.requestID,
                                type: "snapshot",
                                content: content
                            )
                        )
                    }
                } else {
                    latest = try await helper.generateDraft(
                        instructions: request.instructions,
                        prompt: request.prompt,
                        maximumResponseTokens: request.maximumResponseTokens,
                        temperature: request.temperature
                    ) { content in
                        write(
                            PersistentStreamResponse(
                                requestID: envelope.requestID,
                                type: "snapshot",
                                content: content
                            )
                        )
                    }
                }
                let normalized = latest.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !normalized.isEmpty else {
                    throw LocalInferenceHelperError.invalidModelOutput
                }
                write(
                    PersistentStreamResponse(
                        requestID: envelope.requestID,
                        type: "completed",
                        content: latest
                    )
                )
            } catch {
                write(
                    PersistentStreamResponse(
                        requestID: envelope.requestID,
                        type: "error",
                        content: ""
                    )
                )
            }
        }
        #else
        exit(69)
        #endif
    }

    private static func modelAvailable() -> Bool {
        #if canImport(FoundationModels)
        if #available(macOS 26.0, *) {
            return SystemLanguageModel.default.isAvailable
        }
        #endif
        return false
    }

    private static func valid(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= maximumInputBytes
            && !value.unicodeScalars.contains(where: { $0.value == 0 })
    }

    private static func valid(_ value: Int?) -> Bool {
        guard let value else { return true }
        return (1...maximumResponseTokens).contains(value)
    }

    private static func valid(_ value: Double?) -> Bool {
        guard let value else { return true }
        return value.isFinite && (0...maximumTemperature).contains(value)
    }

    private static func write<T: Encodable>(_ value: T) {
        guard var data = try? JSONEncoder().encode(value) else { exit(65) }
        data.append(0x0A)
        try? FileHandle.standardOutput.write(contentsOf: data)
    }

    private static func writeCompleted(_ content: String) throws {
        let normalized = content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { throw LocalInferenceHelperError.invalidModelOutput }
        write(StreamResponse(type: "completed", content: content))
    }
}
