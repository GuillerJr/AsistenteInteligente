import Foundation
import NaturalLanguage

private struct EmbeddingRequest: Decodable {
    let texts: [String]
    let inputType: String

    enum CodingKeys: String, CodingKey {
        case texts
        case inputType = "input_type"
    }
}

private struct EmbeddingResponse: Encodable {
    let modelID: String
    let vectors: [[Double]]

    enum CodingKeys: String, CodingKey {
        case modelID = "model_id"
        case vectors
    }
}

private struct StatusResponse: Encodable {
    let available: Bool
    let dimensions: Int?
    let modelID: String

    enum CodingKeys: String, CodingKey {
        case available
        case dimensions
        case modelID = "model_id"
    }
}

@main
private enum JarvisLocalEmbedding {
    private static let modelID = "apple/natural-language-sentence-es-v1"
    private static let maximumInputBytes = 131_072
    private static let maximumTextBytes = 16_384
    private static let maximumBatchSize = 16

    static func main() {
        let embedding = NLEmbedding.sentenceEmbedding(for: .spanish)
        if CommandLine.arguments.dropFirst() == ["--status"] {
            write(
                StatusResponse(
                    available: embedding != nil,
                    dimensions: embedding?.dimension,
                    modelID: modelID
                )
            )
            exit(embedding == nil ? 69 : 0)
        }
        let data = FileHandle.standardInput.readDataToEndOfFile()
        guard
            let embedding,
            !data.isEmpty,
            data.count <= maximumInputBytes,
            let request = try? JSONDecoder().decode(EmbeddingRequest.self, from: data),
            (1 ... maximumBatchSize).contains(request.texts.count),
            request.inputType == "passage" || request.inputType == "query",
            request.texts.allSatisfy(valid)
        else {
            exit(64)
        }
        var vectors: [[Double]] = []
        vectors.reserveCapacity(request.texts.count)
        for text in request.texts {
            guard let vector = embedding.vector(for: text), vector.count == embedding.dimension else {
                exit(69)
            }
            let squaredNorm = vector.reduce(0) { $0 + $1 * $1 }
            guard squaredNorm.isFinite, squaredNorm > 0 else { exit(65) }
            let norm = squaredNorm.squareRoot()
            let normalized = vector.map { $0 / norm }
            guard normalized.allSatisfy(\.isFinite) else { exit(65) }
            vectors.append(normalized)
        }
        write(EmbeddingResponse(modelID: modelID, vectors: vectors))
    }

    private static func valid(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && value.utf8.count <= maximumTextBytes
            && !value.unicodeScalars.contains(where: { $0.value == 0 })
    }

    private static func write<T: Encodable>(_ value: T) {
        guard var data = try? JSONEncoder().encode(value), data.count <= 1_048_576 else {
            exit(65)
        }
        data.append(0x0A)
        try? FileHandle.standardOutput.write(contentsOf: data)
    }
}
