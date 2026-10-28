import CryptoKit
import Foundation

public struct PluginDescriptor: Identifiable, Equatable, Sendable {
    public let id: String
    public let agentID: String
    public let packageID: String
    public let name: String
    public let description: String
    public let version: String
    public let revision: Int
    public let hash: String
    public let enabled: Bool
    public let skills: Int
    public let resources: Int
    public let hooks: Int
    public let mcp: Int
    public let source: String
    public let publisher: String
    public init(_ value: RPCValue) throws {
        guard let id = value["plugin_id"].string, id.range(of: "^plugin_[a-f0-9]{32}$", options: .regularExpression) != nil,
              let agent = value["agent_id"].string, ManagementValidation.identifier(agent, agent: true),
              let package = value["package_id"].string, ManagementValidation.text(package, limit: 128),
              let name = value["name"].string, ManagementValidation.text(name, limit: 256),
              let description = value["description"].string, ManagementValidation.text(description, limit: 4096, empty: true),
              let version = value["version"].string, ManagementValidation.text(version, limit: 80),
              let revision = value["revision"].integer, revision > 0,
              let hash = value["sha256"].string, ManagementValidation.hash(hash), let enabled = value["enabled"].bool,
              let skills = value["skills_count"].integer, (0...128).contains(skills),
              let resources = value["resources_count"].integer, (0...512).contains(resources),
              let hooks = value["hooks_count"].integer, (0...128).contains(hooks),
              let mcp = value["mcp_count"].integer, (0...8).contains(mcp),
              let source = value["source_uri"].string, ManagementValidation.text(source, limit: 4096),
              let publisher = value["publisher"].string, ManagementValidation.text(publisher, limit: 256),
              value["source_verified"].bool == false else { throw RPCFailure.invalidResponse }
        self.id = id; agentID = agent; packageID = package; self.name = name; self.description = description
        self.version = version; self.revision = revision; self.hash = hash; self.enabled = enabled
        self.skills = skills; self.resources = resources; self.hooks = hooks; self.mcp = mcp; self.source = source; self.publisher = publisher
    }
    public var revisionParams: [String: RPCValue] {
        ["agent_id": .string(agentID), "plugin_id": .string(id), "expected_revision": .number(Double(revision)), "expected_sha256": .string(hash)]
    }
}

public struct ExtensionFileDescriptor: Identifiable, Equatable, Sendable {
    public let path: String
    public let bytes: Int
    public let hash: String
    public var id: String { path }
    public init(_ value: RPCValue) throws {
        guard let path = value["path"].string, Self.validPath(path), let bytes = value["bytes"].integer, (0...1_048_576).contains(bytes),
              let hash = value["sha256"].string, ManagementValidation.hash(hash) else { throw RPCFailure.invalidResponse }
        self.path = path; self.bytes = bytes; self.hash = hash
    }
    public static func validPath(_ path: String) -> Bool {
        ManagementValidation.text(path, limit: 512) && !path.hasPrefix("/") && !path.contains("\\")
            && path.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }
}
public struct ExtensionResource: Equatable, Sendable {
    public let path: String
    public let data: Data
    public let hash: String
    public init(_ value: RPCValue, expectedPath: String) throws {
        let file = try ExtensionFileDescriptor(value)
        guard file.path == expectedPath, let raw = value["content_base64"].string, raw.utf8.count <= 1_398_104,
              let data = Data(base64Encoded: raw), data.count == file.bytes,
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == file.hash else { throw RPCFailure.invalidResponse }
        path = expectedPath; self.data = data; hash = file.hash
    }
    public var text: String? { String(data: data, encoding: .utf8) }
}
public struct PluginPackagePreview: Equatable, Identifiable, Sendable {
    public let value: RPCValue
    public let name: String
    public let version: String
    public let hash: String
    public let files: [ExtensionFileDescriptor]
    public var id: String { hash }
    public init(_ value: RPCValue) throws {
        guard let name = value["name"].string, ManagementValidation.text(name, limit: 256),
              let version = value["version"].string, ManagementValidation.text(version, limit: 80),
              let hash = value["sha256"].string, ManagementValidation.hash(hash), value["source_verified"].bool == false,
              let object = value.object, object["content_base64"] == nil,
              try JSONEncoder().encode(value).count <= 262144 else { throw RPCFailure.invalidResponse }
        files = try ManagementValidation.rows(value["files"], limit: 512, parse: ExtensionFileDescriptor.init)
        guard Set(files.map(\.path)).count == files.count else { throw RPCFailure.invalidResponse }
        self.value = value; self.name = name; self.version = version; self.hash = hash
    }
}
public struct PluginReview: Equatable, Sendable {
    public let value: RPCValue
    public let id: String
    public let digest: String
    public let action: String
    public let agentID: String
    public let expiresAt: Date
    public init(_ value: RPCValue, expectedAgent: String, expectedAction: String) throws {
        guard let id = value["review_id"].string, ManagementValidation.identifier(id),
              let digest = value["digest"].string, ManagementValidation.hash(digest),
              value["agent_id"].string == expectedAgent, value["action"].string == expectedAction,
              case .number(let nanoseconds) = value["expires_ns"], nanoseconds.isFinite, nanoseconds > 0,
              try JSONEncoder().encode(value).count <= 524288 else { throw RPCFailure.invalidResponse }
        self.value = value; self.id = id; self.digest = digest; action = expectedAction; agentID = expectedAgent
        expiresAt = Date(timeIntervalSince1970: nanoseconds / 1_000_000_000)
    }
}
public struct ExtensionVersion: Identifiable, Equatable, Sendable {
    public let version: String
    public let hash: String
    public let current: Bool
    public let revision: Int?
    public var id: String { version + ":" + hash + ":" + String(revision ?? 0) }
    public init(_ value: RPCValue) throws {
        guard let version = value["version"].string, ManagementValidation.text(version, limit: 80, empty: true),
              let hash = value["sha256"].string, ManagementValidation.hash(hash), let current = value["current"].bool else { throw RPCFailure.invalidResponse }
        self.version = version; self.hash = hash; self.current = current
        if case .null = value["revision"] { revision = nil }
        else if let revision = value["revision"].integer, revision > 0 { self.revision = revision }
        else { throw RPCFailure.invalidResponse }
    }
}

public struct ExtensionCleanupResult: Equatable, Sendable {
    public let receipt: RPCValue
    public let closed: Bool
    public init(_ value: RPCValue) throws {
        guard value["receipt"]["operation"].string == "extensions_cleanup",
              let status = value["receipt"]["status"].string, ["closed", "cleanup_pending"].contains(status),
              let confirmed = value["receipt"]["cleanup_confirmed"].bool,
              value["receipt"]["replayed_mutation"].bool == false,
              let plugins = value["plugins_pending"].bool, let skills = value["skills_pending"].bool,
              confirmed == (status == "closed"), !confirmed || (!plugins && !skills) else { throw RPCFailure.invalidResponse }
        receipt = value["receipt"]; closed = confirmed
    }
}

public struct PluginHookDescriptor: Equatable, Identifiable, Sendable {
    public let event: String
    public let hash: String
    public let serverID: String?
    public let tool: String?
    public let promptID: String?
    public let arguments: RPCValue
    public var id: String { hash }
    public init(_ value: RPCValue) throws {
        guard let event = value["event"].string, ManagementValidation.text(event, limit: 64),
              let hash = value["sha256"].string, ManagementValidation.hash(hash) else { throw RPCFailure.invalidResponse }
        self.event = event; self.hash = hash
        if let server = value["mcp_server_id"].string, let tool = value["tool"].string,
           ManagementValidation.text(server, limit: 24), ManagementValidation.text(tool, limit: 128), value["arguments"].object != nil {
            serverID = server; self.tool = tool; arguments = value["arguments"]; promptID = nil
        } else if let prompt = value["prompt_id"].string, ManagementValidation.text(prompt, limit: 128) {
            promptID = prompt; serverID = nil; tool = nil; arguments = .object([:])
        } else { throw RPCFailure.invalidResponse }
    }
}
