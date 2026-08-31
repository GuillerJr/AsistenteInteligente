import AegisAudioCore
import Darwin
import Foundation

private let maximumFrameBytes = 4_096

private struct AuthorizationRequest: Decodable {
    let protocolVersion: String
    let requestID: UUID
    let challenge: String
    let reason: String

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case challenge
        case reason
    }

    func validate() throws {
        guard
            protocolVersion == "1.0",
            challenge.range(
                of: #"^[0-9a-f]{64}$"#,
                options: .regularExpression
            ) != nil,
            !reason.isEmpty,
            reason.utf8.count <= 256,
            reason.unicodeScalars.allSatisfy({
                !CharacterSet.controlCharacters.contains($0)
            })
        else {
            throw BridgeError.invalidRequest
        }
    }
}

private struct AuthorizationResponse: Encodable {
    let ok: Bool
    let requestID: UUID
    let challenge: String

    enum CodingKeys: String, CodingKey {
        case ok
        case requestID = "request_id"
        case challenge
    }
}

private enum BridgeError: Error {
    case invalidFrame
    case invalidRequest
}

@main
private enum JarvisIOSBridgeMain {
    static func main() async {
        do {
            let request = try readRequest()
            let authorized: Bool
            do {
                try await MobileUserPresenceAuthorizer().authorize(reason: request.reason)
                authorized = true
            } catch {
                authorized = false
            }
            try writeResponse(
                AuthorizationResponse(
                    ok: authorized,
                    requestID: request.requestID,
                    challenge: request.challenge
                )
            )
            exit(EXIT_SUCCESS)
        } catch {
            exit(EXIT_FAILURE)
        }
    }

    private static func readRequest() throws -> AuthorizationRequest {
        let input = FileHandle.standardInput
        let header = try readExactly(4, from: input)
        let length = header.withUnsafeBytes { raw in
            Int(raw.loadUnaligned(as: UInt32.self).bigEndian)
        }
        guard (1 ... maximumFrameBytes).contains(length) else {
            throw BridgeError.invalidFrame
        }
        let payload = try readExactly(length, from: input)
        guard try input.read(upToCount: 1)?.isEmpty != false else {
            throw BridgeError.invalidFrame
        }
        let request = try JSONDecoder().decode(AuthorizationRequest.self, from: payload)
        try request.validate()
        return request
    }

    private static func writeResponse(_ response: AuthorizationResponse) throws {
        let payload = try JSONEncoder().encode(response)
        guard !payload.isEmpty, payload.count <= maximumFrameBytes else {
            throw BridgeError.invalidFrame
        }
        var length = UInt32(payload.count).bigEndian
        let header = withUnsafeBytes(of: &length) { Data($0) }
        FileHandle.standardOutput.write(header)
        FileHandle.standardOutput.write(payload)
        try FileHandle.standardOutput.synchronize()
    }

    private static func readExactly(_ count: Int, from handle: FileHandle) throws -> Data {
        var data = Data(capacity: count)
        while data.count < count {
            guard
                let chunk = try handle.read(upToCount: count - data.count),
                !chunk.isEmpty
            else {
                throw BridgeError.invalidFrame
            }
            data.append(chunk)
        }
        return data
    }
}
