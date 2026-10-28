import Foundation

public extension ManagementClient {
    func cleanupExtensions() async throws -> ExtensionCleanupResult { try await ExtensionCleanupResult(call("extensions.cleanup")) }
    func skillMetadata(_ skill: SkillDescriptor) async throws -> SkillInvocationMetadata {
        let result = try await call("skills.metadata", params: skill.revisionParams)
        return try SkillInvocationMetadata(result["metadata"])
    }
    func skillVersions(_ skill: SkillDescriptor) async throws -> [ExtensionVersion] {
        let result = try await call("skills.versions", params: ["agent_id": .string(skill.agentID), "skill_id": .string(skill.id)])
        return try ManagementValidation.rows(result["versions"], limit: 4, parse: ExtensionVersion.init)
    }
    func skillResource(_ skill: SkillDescriptor, path: String) async throws -> ExtensionResource {
        var params = skill.revisionParams; params["path"] = .string(path)
        let result = try await call("skills.resource", params: params)
        return try ExtensionResource(result["resource"], expectedPath: path)
    }
    func skillInvocation(_ skill: SkillDescriptor, arguments: [String: RPCValue]) async throws -> SkillInvocation {
        var params = skill.revisionParams; params["arguments"] = .object(arguments)
        let result = try await call("skills.invoke", params: params)
        let invocation = try SkillInvocation(result["invocation"], skill: skill)
        guard invocation.arguments == .object(arguments) else { throw RPCFailure.invalidResponse }
        return invocation
    }
    func inspectSkillLibrary(path: String) async throws -> SkillLibraryPreview {
        let result = try await call("skills.inspect_library", params: ["path": .string(path)])
        return try SkillLibraryPreview(result["library"])
    }
}
