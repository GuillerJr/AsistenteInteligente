import AegisAudioCore
import Darwin
import Foundation

private struct SpotlightRequest: Codable {
    let protocolVersion: String
    let requestID: UUID
    let operation: String
    let items: [SpotlightGraphItem]?
    let identifiers: [String]?

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case operation
        case items
        case identifiers
    }
}

private struct SpotlightResponse: Codable {
    let protocolVersion = "1.0"
    let requestID: UUID
    let success: Bool
    let affected: Int
    let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case success
        case affected
        case errorCode = "error_code"
    }
}

private enum HelperError: Error {
    case invalidFrame
    case invalidRequest
}

@main
private enum JarvisSpotlightIndexerMain {
    static func main() async {
        do {
            let request = try readRequest()
            guard request.protocolVersion == "1.0" else { throw HelperError.invalidRequest }
            let indexer = SpotlightIndexer()
            let affected: Int
            switch request.operation {
            case "upsert":
                guard let items = request.items, request.identifiers == nil else {
                    throw HelperError.invalidRequest
                }
                try await indexer.indexItems(items)
                affected = items.count
            case "delete":
                guard let identifiers = request.identifiers, request.items == nil else {
                    throw HelperError.invalidRequest
                }
                try await indexer.deleteItems(identifiers: identifiers)
                affected = identifiers.count
            case "reset_domain":
                guard request.identifiers == nil, request.items == nil else {
                    throw HelperError.invalidRequest
                }
                try await indexer.resetGraphDomain()
                affected = 0
            default:
                throw HelperError.invalidRequest
            }
            try writeResponse(
                SpotlightResponse(
                    requestID: request.requestID,
                    success: true,
                    affected: affected,
                    errorCode: nil
                )
            )
        } catch {
            try? writeResponse(
                SpotlightResponse(
                    requestID: UUID(),
                    success: false,
                    affected: 0,
                    errorCode: "spotlight_index_failed"
                )
            )
            exit(EX_SOFTWARE)
        }
    }

    private static func readRequest() throws -> SpotlightRequest {
        let input = FileHandle.standardInput
        guard let header = try input.read(upToCount: 4), header.count == 4 else {
            throw HelperError.invalidFrame
        }
        let length = header.reduce(UInt32.zero) { ($0 << 8) | UInt32($1) }
        guard length > 0, length <= 262_144 else { throw HelperError.invalidFrame }
        guard let body = try input.read(upToCount: Int(length)), body.count == Int(length) else {
            throw HelperError.invalidFrame
        }
        return try JSONDecoder().decode(SpotlightRequest.self, from: body)
    }

    private static func writeResponse(_ response: SpotlightResponse) throws {
        let body = try JSONEncoder().encode(response)
        var length = UInt32(body.count).bigEndian
        var frame = Data(bytes: &length, count: 4)
        frame.append(body)
        try FileHandle.standardOutput.write(contentsOf: frame)
    }
}
