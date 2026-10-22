import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class ProjectStoreRecoveryTests: XCTestCase {
    private func listing() throws -> Data {
        try JSONSerialization.data(withJSONObject: ["projects": [[
            "project_id": UUID().uuidString.lowercased(), "name": "Proyecto sintético",
            "color": "clay", "icon": "book", "instructions": "Referencia sintética.",
            "files": [], "chats": 1,
        ]]])
    }

    func testStoreErrorPreservesLastGoodProjectsAndReturnsSpanishNotice() throws {
        let chat = JarvisChatStore()
        XCTAssertNil(try chat.applyProjectListing(listing()))
        let before = chat.projects
        let message = "No pude leer los proyectos guardados. Conservé los datos sin cambiarlos."
        let error = try JSONSerialization.data(withJSONObject: ["error": message])
        XCTAssertEqual(try chat.applyProjectListing(error), message)
        XCTAssertEqual(chat.projects, before)
    }

    func testMalformedListingPreservesLastGoodProjects() throws {
        let chat = JarvisChatStore()
        XCTAssertNil(try chat.applyProjectListing(listing()))
        let before = chat.projects
        for malformed in ["{}", "{\"error\":\"\"}", "{\"projects\":{}}",
                          "{\"projects\":[{\"name\":\"incompleto\"}]}"] {
            XCTAssertThrowsError(try chat.applyProjectListing(Data(malformed.utf8)))
            XCTAssertEqual(chat.projects, before)
        }
    }

    func testValidEmptyListingClearsProjectsAfterDeletion() throws {
        let chat = JarvisChatStore()
        XCTAssertNil(try chat.applyProjectListing(listing()))
        XCTAssertFalse(chat.projects.isEmpty)
        XCTAssertNil(try chat.applyProjectListing(Data("{\"projects\":[]}".utf8)))
        XCTAssertTrue(chat.projects.isEmpty)
    }
}
