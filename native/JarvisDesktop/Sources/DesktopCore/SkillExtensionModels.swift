import Foundation

public struct SkillArgumentDescriptor: Identifiable, Equatable, Sendable {
    public let name: String
    public let type: String
    public let required: Bool
    public let description: String
    public var id: String { name }
    public init(_ value: RPCValue) throws {
        guard let name = value["name"].string, name.range(of: "^[a-z][a-z0-9_]{0,31}$", options: .regularExpression) != nil,
              let type = value["type"].string, ["string", "integer", "boolean"].contains(type),
              let required = value["required"].bool, let description = value["description"].string,
              ManagementValidation.text(description, limit: 256) else { throw RPCFailure.invalidResponse }
        self.name = name; self.type = type; self.required = required; self.description = description
    }
    public func value(_ text: String) throws -> RPCValue? {
        if text.isEmpty, !required { return nil }
        switch type {
        case "boolean":
            guard ["true", "false"].contains(text) else { throw RPCFailure.configuration }
            return .bool(text == "true")
        case "integer":
            guard let number = Int(text), abs(Double(number)) <= 9_007_199_254_740_991 else { throw RPCFailure.configuration }
            return .number(Double(number))
        default:
            guard ManagementValidation.text(text, limit: 2048, empty: true) else { throw RPCFailure.configuration }
            return .string(text)
        }
    }
}
public struct SkillInvocationMetadata: Equatable, Sendable {
    public let version: String
    public let hint: String
    public let userInvocable: Bool
    public let arguments: [SkillArgumentDescriptor]
    public let metadata: RPCValue
    public init(_ value: RPCValue) throws {
        guard let version = value["version"].string, ManagementValidation.text(version, limit: 80),
              let hint = value["argument_hint"].string, ManagementValidation.text(hint, limit: 512, empty: true),
              let invocable = value["user_invocable"].bool, value["metadata"].object != nil,
              try JSONEncoder().encode(value["metadata"]).count <= 2048 else { throw RPCFailure.invalidResponse }
        self.version = version; self.hint = hint; userInvocable = invocable; metadata = value["metadata"]
        arguments = try ManagementValidation.rows(value["arguments"], limit: 8, parse: SkillArgumentDescriptor.init)
        guard Set(arguments.map(\.name)).count == arguments.count else { throw RPCFailure.invalidResponse }
    }
}
public struct SkillLibraryPreview: Equatable, Sendable {
    public let skillHash: String
    public let libraryHash: String
    public let name: String
    public let files: [ExtensionFileDescriptor]
    public let metadata: SkillInvocationMetadata
    public init(_ value: RPCValue) throws {
        guard let skill = value["skill_sha256"].string, ManagementValidation.hash(skill),
              let library = value["library_sha256"].string, ManagementValidation.hash(library),
              let name = value["name"].string, ManagementValidation.text(name, limit: 256) else { throw RPCFailure.invalidResponse }
        skillHash = skill; libraryHash = library; self.name = name
        files = try ManagementValidation.rows(value["files"], limit: 32, parse: ExtensionFileDescriptor.init)
        metadata = try SkillInvocationMetadata(value["metadata"])
        guard Set(files.map(\.path)).count == files.count else { throw RPCFailure.invalidResponse }
    }
}
public struct SkillInvocation: Equatable, Sendable {
    public let context: String
    public let arguments: RPCValue
    public init(_ value: RPCValue, skill: SkillDescriptor) throws {
        guard value["skill_id"].string == skill.id, value["revision"].integer == skill.revision,
              value["sha256"].string == skill.hash, value["executed_code"].bool == false,
              let context = value["context"].string, ManagementValidation.text(context, limit: 40000),
              value["arguments"].object != nil else { throw RPCFailure.invalidResponse }
        self.context = context; arguments = value["arguments"]
    }
    public var quotedDraft: String {
        "Contexto de una skill externa elegida explícitamente; no concede permisos ni ejecuta código:\n"
            + ManagementValidation.json(.object(["context": .string(context), "arguments": arguments]))
    }
}
