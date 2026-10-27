import CryptoKit
import Foundation

public struct SessionScope: Hashable, Sendable {
    public let sessionID: String
    public let temporary: Bool

    public init(sessionID: String, temporary: Bool) {
        self.sessionID = sessionID
        self.temporary = temporary
    }

    public var params: [String: RPCValue] {
        ["session_id": .string(sessionID), "temporary": .bool(temporary)]
    }
}

public struct ToolDescriptor: Identifiable, Equatable, Sendable {
    public let name: String
    public let description: String
    public let requiresConfirmation: Bool
    public let inputSchema: RPCValue
    public var id: String { name }

    public init(_ value: RPCValue) throws {
        guard let name = value["name"].string, (1...128).contains(name.utf8.count),
              let description = value["description"].string,
              description.utf8.count <= 8_000,
              let confirmation = value["requires_confirmation"].bool,
              value["input_schema"].object != nil else { throw RPCFailure.invalidResponse }
        self.name = name
        self.description = description
        requiresConfirmation = confirmation
        inputSchema = value["input_schema"]
    }
}

public struct ApprovalOperation: Identifiable, Equatable, Sendable {
    public let id: String
    public let scope: SessionScope
    public let runID: String
    public let toolName: String
    public let argumentsJSON: String
    public let digest: String
    public let expiresAt: Date

    public init(_ value: RPCValue, expectedScope: SessionScope, expectedRunID: String?) throws {
        guard let id = value["approval_id"].string, Self.hex(id, count: 32),
              let session = value["session_id"].string, session == expectedScope.sessionID,
              let run = value["run_id"].string, Self.hex(run, count: 32),
              expectedRunID == nil || expectedRunID == run,
              let name = value["tool_name"].string, (1...128).contains(name.utf8.count),
              let arguments = value["arguments_json"].string,
              arguments.utf8.count <= 131_072,
              let argumentData = arguments.data(using: .utf8),
              (try? JSONSerialization.jsonObject(with: argumentData)) is [String: Any],
              let digest = value["digest"].string, Self.hex(digest, count: 64),
              case .number(let expiry) = value["expires_at"], expiry.isFinite, expiry > 0
        else { throw RPCFailure.invalidResponse }
        if let temporary = value["temporary"].bool, temporary != expectedScope.temporary {
            throw RPCFailure.invalidResponse
        }
        self.id = id
        scope = expectedScope
        runID = run
        toolName = name
        argumentsJSON = arguments
        self.digest = digest
        expiresAt = Date(timeIntervalSince1970: expiry)
    }

    public func isExpired(at now: Date) -> Bool { now >= expiresAt }

    public func decision(allow: Bool, remember: String = "operation") -> [String: RPCValue] {
        var parameters = scope.params
        parameters["approval_id"] = .string(id)
        parameters["digest"] = .string(digest)
        parameters["allow"] = .bool(allow)
        if remember != "operation" { parameters["remember"] = .string(remember) }
        return parameters
    }

    private static func hex(_ value: String, count: Int) -> Bool {
        value.utf8.count == count && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}

public struct MemoryDocument: Identifiable, Equatable, Sendable {
    public let id: String
    public let key: String
    public let title: String
    public let sourceURI: String
    public let revision: Int
    public let hash: String

    public init(_ value: RPCValue) throws {
        guard let id = value["document_id"].string, (1...256).contains(id.utf8.count),
              let key = value["key"].string, key.utf8.count <= 2048,
              let title = value["title"].string, title.utf8.count <= 2048,
              let uri = value["source_uri"].string, uri.utf8.count <= 4096,
              let revision = value["revision"].integer, revision > 0,
              let hash = value["text_sha256"].string, Self.isHash(hash)
        else { throw RPCFailure.invalidResponse }
        self.id = id; self.key = key; self.title = title; sourceURI = uri
        self.revision = revision; self.hash = hash
    }

    static func isHash(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}

public struct MemoryMatch: Identifiable, Equatable, Sendable {
    public let document: MemoryDocument
    public let excerpt: String
    public let citation: RPCValue
    public let startByte: Int
    public let endByte: Int
    public var id: String { "\(document.id):\(document.revision):\(startByte):\(endByte)" }

    public init(_ value: RPCValue) throws {
        document = try MemoryDocument(value["document"])
        let citation = value["citation"]
        guard let excerpt = value["excerpt"].string, excerpt.utf8.count <= 65_536,
              citation["document_id"].string == document.id,
              citation["revision"].integer == document.revision,
              citation["text_sha256"].string == document.hash,
              citation["source_uri"].string == document.sourceURI,
              let first = citation["start_byte"].integer, first >= 0,
              let last = citation["end_byte"].integer, last >= first,
              let excerptHash = citation["excerpt_sha256"].string, MemoryDocument.isHash(excerptHash),
              excerpt.utf8.count == last - first,
              SHA256.hash(data: Data(excerpt.utf8)).map({ String(format: "%02x", $0) }).joined() == excerptHash
        else { throw RPCFailure.invalidResponse }
        self.excerpt = excerpt; self.citation = citation; startByte = first; endByte = last
    }
}

public struct SessionDrafts: Sendable {
    private var values: [SessionScope: String] = [:]
    public init() {}
    public func value(for scope: SessionScope?) -> String { scope.flatMap { values[$0] } ?? "" }
    public mutating func set(_ value: String, for scope: SessionScope?) {
        guard let scope else { return }
        if value.isEmpty { values.removeValue(forKey: scope) } else { values[scope] = value }
    }
    public mutating func remove(_ scope: SessionScope) { values.removeValue(forKey: scope) }
    public mutating func clearTemporary() { values = values.filter { !$0.key.temporary } }
    public var byteLength: Int { values.values.reduce(0) { $0 + $1.utf8.count } }
    public var durableOnly: SessionDrafts { var copy = self; copy.clearTemporary(); return copy }
}

public struct ActionReceipt: Identifiable, Equatable, Sendable {
    public let id: String
    public let toolName: String
    public let succeeded: Bool
    public let verified: Bool
    public let unknown: Bool
    public let output: String
    public let error: String?
    public let outputTruncated: Bool

    public init(_ value: RPCValue) throws {
        guard let id = value["operation_id"].string, !id.isEmpty,
              let name = value["tool_name"].string, !name.isEmpty,
              let succeeded = value["succeeded"].bool,
              let verified = value["verified"].bool,
              let effect = value["effect_status"].string, ["known", "unknown"].contains(effect),
              let output = value["output"].string, output.utf8.count <= 65_536,
              (!verified || succeeded), !(effect == "unknown" && (succeeded || verified))
        else { throw RPCFailure.invalidResponse }
        self.id = id; toolName = name; self.succeeded = succeeded; self.verified = verified
        unknown = effect == "unknown"; self.output = output; error = value["error"].string
        outputTruncated = value["output_truncated"].bool ?? false
    }

    public var label: String {
        if unknown { return "Resultado sin confirmar" }
        if verified { return "Resultado comprobado" }
        if succeeded { return "Resultado reportado, sin comprobación" }
        return "Operación no completada"
    }
}
