import Foundation

public enum SkillImportMode: Equatable, Sendable {
    case manifestOnly
    case withResources
}

/// One immutable sheet item owns the exact manifest, resource inventory, agent
/// and import mode. SwiftUI must not resolve them from independent @State values
/// when it later presents the sheet or handles its confirmation button.
public struct SkillImportReviewSnapshot: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let preview: SkillPreviewSnapshot
    public let library: SkillLibraryPreview?
    public let agentID: String
    public let mode: SkillImportMode

    public init(preview: SkillPreviewSnapshot, library: SkillLibraryPreview?,
                agentID: String, mode: SkillImportMode) throws {
        guard ManagementValidation.identifier(agentID, agent: true) else {
            throw RPCFailure.invalidResponse
        }
        switch mode {
        case .manifestOnly:
            guard library == nil else { throw RPCFailure.invalidResponse }
        case .withResources:
            guard let library, library.skillHash == preview.hash,
                  library.name == preview.name,
                  Set(preview.resources).count == preview.resources.count,
                  Set(library.files.map(\.path)) == Set(preview.resources) else {
                throw RPCFailure.invalidResponse
            }
        }
        id = UUID()
        self.preview = preview
        self.library = library
        self.agentID = agentID
        self.mode = mode
    }
}
