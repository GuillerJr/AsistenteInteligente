import Foundation
#if canImport(FoundationModels)
import FoundationModels
#endif

private struct LocalBrainRequest: Decodable {
    let instructions: String
    let prompt: String
    let maximumResponseTokens: Int?
    let temperature: Double?
}

private struct StatusResponse: Encodable {
    let available: Bool
}

private struct StreamResponse: Encodable {
    let type: String
    let content: String
}

@main
private enum JarvisLocalBrain {
    private static let maximumInputBytes = 24_576
    private static let maximumOutputBytes = 24_576
    private static let maximumResponseTokens = 4_096
    private static let maximumTemperature = 2.0

    static func main() async {
        if CommandLine.arguments.dropFirst() == ["--status"] {
            let available = modelAvailable()
            write(StatusResponse(available: available))
            exit(available ? 0 : 1)
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
                let session = LanguageModelSession(
                    model: .default,
                    tools: [],
                    instructions: request.instructions
                )
                let options = GenerationOptions(
                    temperature: request.temperature,
                    maximumResponseTokens: request.maximumResponseTokens
                )
                var latest = ""
                for try await snapshot in session.streamResponse(
                    to: request.prompt,
                    options: options
                ) {
                    let content = snapshot.content
                    guard
                        content.hasPrefix(latest),
                        content.utf8.count <= maximumOutputBytes
                    else {
                        exit(65)
                    }
                    if content != latest {
                        latest = content
                        write(StreamResponse(type: "snapshot", content: content))
                    }
                }
                let normalized = latest.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !normalized.isEmpty else { exit(65) }
                write(StreamResponse(type: "completed", content: latest))
                return
            } catch {
                exit(69)
            }
        }
        #endif
        exit(69)
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
}
