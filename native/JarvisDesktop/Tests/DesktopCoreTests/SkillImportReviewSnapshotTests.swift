import Foundation
import XCTest
@testable import DesktopCore

final class SkillImportReviewSnapshotTests: XCTestCase {
    private func preview(text: String = "Manifiesto sintético", resources: [String] = ["references/marcador.txt"]) throws -> SkillPreviewSnapshot {
        try SkillPreviewSnapshot(.object([
            "name": .string("Skill de prueba"), "description": .string("Datos sintéticos"),
            "text": .string(text), "sha256": .string(ManagementValidation.digest(text)),
            "source_path": .string("/private/tmp/jarvis-skill-fixture"),
            "resources": .array(resources.map(RPCValue.string))
        ]))
    }

    private func library(_ preview: SkillPreviewSnapshot,
                         paths: [String] = ["references/marcador.txt"]) throws -> SkillLibraryPreview {
        let hash = ManagementValidation.digest("MARCADOR DE RECURSO")
        return try SkillLibraryPreview(.object([
            "name": .string(preview.name), "skill_sha256": .string(preview.hash),
            "library_sha256": .string(ManagementValidation.digest("Biblioteca revisada")),
            "files": .array(paths.map { path in .object([
                "path": .string(path), "bytes": .number(19), "sha256": .string(hash)
            ]) }),
            "metadata": .object(["version": .string("1.0.0"), "user_invocable": .bool(true),
                                  "argument_hint": .string(""), "arguments": .array([]),
                                  "metadata": .object([:])])
        ]))
    }

    func testResourceModeCannotFallBackToManifestWhenIndependentLibraryStateIsAbsent() throws {
        let manifest = try preview()
        XCTAssertThrowsError(try SkillImportReviewSnapshot(preview: manifest, library: nil,
            agentID: "jarvis", mode: .withResources))
        let selection = try SkillImportReviewSnapshot(preview: manifest, library: library(manifest),
            agentID: "jarvis", mode: .withResources)
        XCTAssertEqual(selection.mode, .withResources)
        XCTAssertEqual(selection.library?.files.map(\.path), ["references/marcador.txt"])
    }

    func testSheetItemPreservesItsWholeSelectionAfterLaterSelectionsChange() throws {
        let original = try preview()
        var selectedLibrary: SkillLibraryPreview? = try library(original)
        var selectedAgent = "jarvis"
        var selectedManifest = original
        let sheet = try SkillImportReviewSnapshot(preview: selectedManifest, library: selectedLibrary,
            agentID: selectedAgent, mode: .withResources)
        selectedLibrary = nil
        selectedAgent = "otro"
        selectedManifest = try preview(text: "Otro manifiesto", resources: [])
        XCTAssertNil(selectedLibrary)
        XCTAssertEqual(selectedAgent, "otro")
        XCTAssertNotEqual(selectedManifest.hash, sheet.preview.hash)
        XCTAssertEqual(sheet.preview, original)
        XCTAssertEqual(sheet.agentID, "jarvis")
        XCTAssertEqual(sheet.library?.skillHash, original.hash)
        XCTAssertEqual(sheet.library?.files.count, 1)
    }

    func testWrongManifestOrIncompleteResourceInventoryCannotBecomeAnImportReview() throws {
        let manifest = try preview()
        let changed = try preview(text: "Otro contenido")
        for inventory in [try library(changed), try library(manifest, paths: []),
                          try library(manifest, paths: ["references/otro.txt"])] {
            XCTAssertThrowsError(try SkillImportReviewSnapshot(preview: manifest, library: inventory,
                agentID: "jarvis", mode: .withResources))
        }
    }

    func testManifestOnlyModeNeverAcquiresResourcesOrAnUnreviewedAgent() throws {
        let manifest = try preview()
        let plain = try SkillImportReviewSnapshot(preview: manifest, library: nil,
            agentID: "jarvis", mode: .manifestOnly)
        XCTAssertNil(plain.library)
        XCTAssertEqual(plain.mode, .manifestOnly)
        XCTAssertThrowsError(try SkillImportReviewSnapshot(preview: manifest, library: library(manifest),
            agentID: "jarvis", mode: .manifestOnly))
        XCTAssertThrowsError(try SkillImportReviewSnapshot(preview: manifest, library: nil,
            agentID: "../otro", mode: .manifestOnly))
    }
}
