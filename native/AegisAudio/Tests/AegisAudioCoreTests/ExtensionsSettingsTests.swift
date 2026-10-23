import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Extensions settings contracts")
struct ExtensionsSettingsTests {
    private let skill = """
    {"skill_id":"review-docs","name":"Review docs","description":"Review public docs",
     "role":"planner","origin":"plugin","content_sha256":"abc","enabled":true,
     "remote_allowed":false,"builtin_public":false,"approval_required":true,
     "allowed_tools":["read_docs"],"starter_tools":[],"preview_available":true,
     "effective_enabled":false,"loaded":false,"restart_required":true,"plugin_id":"docs-pack"}
    """

    @Test func unloadedPluginSkillDoesNotAppearActive() throws {
        let decoded = try JSONDecoder().decode(JarvisExtensionSkill.self, from: Data(skill.utf8))
        #expect(decoded.enabled)
        #expect(!decoded.active)
        #expect(decoded.statusTitle == "Pendiente de aplicar")
        #expect(decoded.originTitle == "Plugin")
    }

    @Test func sharingAndActivityAreSeparate() throws {
        let active = skill.replacingOccurrences(of: "\"effective_enabled\":false", with: "\"effective_enabled\":true")
            .replacingOccurrences(of: "\"restart_required\":true", with: "\"restart_required\":false")
        let decoded = try JSONDecoder().decode(JarvisExtensionSkill.self, from: Data(active.utf8))
        #expect(decoded.active)
        #expect(!decoded.remote_allowed)
        #expect(decoded.statusTitle == "Requiere autorización para Codex")
    }

    @Test func missingSharingDecisionDoesNotDecode() {
        let incomplete = skill.replacingOccurrences(of: "\"remote_allowed\":false,", with: "")
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(JarvisExtensionSkill.self, from: Data(incomplete.utf8))
        }
    }

    @Test func disabledSkillCanStillExposeRevocableConsent() throws {
        let disabled = skill.replacingOccurrences(of: "\"enabled\":true", with: "\"enabled\":false,\"remote_authorized\":true")
        let decoded = try JSONDecoder().decode(JarvisExtensionSkill.self, from: Data(disabled.utf8))
        #expect(!decoded.active)
        #expect(!decoded.remote_allowed)
        #expect(decoded.sharingAuthorized)
    }

    @Test func compromisedPluginWithoutChecksumRemainsInspectableMetadata() throws {
        let json = """
        {"plugin_id":"docs-pack","name":"docs-pack","description":"","version":"",
         "enabled":false,"active":false,"integrity":"compromised","skills":0,"tools":0,
         "restart_required":true}
        """
        let decoded = try JSONDecoder().decode(JarvisExtensionPlugin.self, from: Data(json.utf8))
        #expect(decoded.checksum_sha256 == nil)
        #expect(!decoded.active)
        #expect(decoded.integrity == "compromised")
    }

    @Test func snapshotIncludesRoleToolChoicesAndInvalidPolicy() throws {
        let json = """
        {"skills":[\(skill)],"plugins":[],"tool_options":{"planner":["read_docs"]},
         "busy":true,"restart_required":true,"policy_status":{"valid":false,"state":"invalid"}}
        """
        let decoded = try JSONDecoder().decode(JarvisExtensionsSnapshot.self, from: Data(json.utf8))
        #expect(decoded.tool_options["planner"] == ["read_docs"])
        #expect(decoded.busy)
        #expect(decoded.policy_status?.valid == false)
    }

    @Test func markdownPreviewAllowsUnassignedRoleAndPreservesFullProcedure() throws {
        let json = """
        {"source_sha256":"exact-source","format":"markdown","skill_id":"review-docs",
         "name":"Review docs","description":"Review public docs","instructions":["Apply the procedure"],
         "resources":["Step one\\nStep two"],"role":null,"trigger_phrases":[],"trigger_terms":[],
         "allowed_tools":[],"starter_tools":[],"diagnostics":[],"requires_disabled":false}
        """
        let decoded = try JSONDecoder().decode(JarvisSkillImportPreview.self, from: Data(json.utf8))
        #expect(decoded.role == nil)
        #expect(decoded.resources == ["Step one\nStep two"])
        #expect(decoded.allowed_tools.isEmpty)
    }
}
