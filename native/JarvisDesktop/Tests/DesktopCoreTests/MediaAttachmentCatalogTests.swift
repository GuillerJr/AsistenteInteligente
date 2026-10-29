import Foundation
import XCTest
@testable import DesktopCore

private func catalogRow(id: String, sequence: Int? = nil, imported: Int = 1_793_300_000_000,
                        source: String = "composer", sourceKind: String? = nil,
                        ingressRunID: String? = nil, revoked: Bool = false) -> RPCValue {
    var row: [String: RPCValue] = [
        "format": .string(sequence == nil ? "jarvis-attachment/1" : "jarvis-attachment/2"),
        "attachment_id": .string(id), "session_id": .string(String(repeating: "a", count: 32)),
        "workspace_id": .string(String(repeating: "c", count: 64)), "temporary": .bool(true),
        "name": .string("telegram-voice.ogg"), "mime": .string("audio/ogg"), "kind": .string("audio"),
        "byte_length": .number(12), "sha256": .string(String(repeating: "d", count: 64)),
        "source": .string(source), "revoked": .bool(revoked)
    ]
    if let sequence {
        row["imported_at_unix_ms"] = .number(Double(imported))
        row["catalog_sequence"] = .number(Double(sequence))
        row["ingress_run_id"] = ingressRunID.map(RPCValue.string) ?? .null
        row["source_kind"] = sourceKind.map(RPCValue.string) ?? .null
    }
    return .object(row)
}

private actor MediaCatalogRPCFixture {
    var rows: [RPCValue]
    init(_ rows: [RPCValue]) { self.rows = rows }
    func list() -> RPCValue {
        .object(["attachments": .array(rows), "workspace_files_included_in_profile_backup": .bool(false),
                 "temporary_storage": .string("memory_only")])
    }
}

final class MediaAttachmentCatalogTests: XCTestCase {
    private let first = String(repeating: "1", count: 32)
    private let second = String(repeating: "2", count: 32)
    private let third = String(repeating: "3", count: 32)

    func testLegacyKeepsUnknownChronologyAndProvenanceWithoutInventingAnImportTime() throws {
        let legacy = try MediaAttachment(catalogRow(id: first))
        XCTAssertNil(legacy.importedAtUnixMilliseconds); XCTAssertNil(legacy.catalogSequence)
        XCTAssertNil(legacy.ingressRunID); XCTAssertNil(legacy.sourceKind)
        XCTAssertEqual(legacy.source, "composer")
    }

    func testV2PreservesExplicitMetadataAndNeverAddsItToTheContentAuthorizationReview() throws {
        let run = String(repeating: "e", count: 32)
        let value = catalogRow(id: first, sequence: 7, source: "telegram", sourceKind: "voice", ingressRunID: run)
        let attachment = try MediaAttachment(value)
        XCTAssertEqual(attachment.catalogSequence, 7)
        XCTAssertEqual(attachment.importedAtUnixMilliseconds, 1_793_300_000_000)
        XCTAssertEqual(attachment.source, "telegram"); XCTAssertEqual(attachment.sourceKind, "voice")
        XCTAssertEqual(attachment.ingressRunID, run)
        XCTAssertEqual(Set(try XCTUnwrap(attachment.review.object).keys), ["attachment_id", "name", "byte_length", "sha256", "mime"])
        let local = try MediaAttachment(catalogRow(id: second, sequence: 8))
        XCTAssertNil(local.sourceKind); XCTAssertNil(local.ingressRunID)
        for kind in ["photo", "document", "audio", "voice", "video", "animation"] {
            XCTAssertEqual(try MediaAttachment(catalogRow(id: first, sequence: 9, sourceKind: kind)).sourceKind, kind)
        }
    }

    func testCatalogIntegersRequirePositiveFiniteJSONSafeWholeValues() throws {
        let baseline = try XCTUnwrap(catalogRow(id: first, sequence: 1).object)
        let invalid: [RPCValue] = [.number(0), .number(-1), .number(0.5), .number(.nan), .number(.infinity),
                                   .number(9_007_199_254_740_992), .bool(true), .string("1"), .null]
        for key in ["catalog_sequence", "imported_at_unix_ms"] {
            for value in invalid {
                var changed = baseline; changed[key] = value
                XCTAssertThrowsError(try MediaAttachment(.object(changed)), "\(key): \(value)")
            }
            var boundary = baseline; boundary[key] = .number(9_007_199_254_740_991)
            XCTAssertNoThrow(try MediaAttachment(.object(boundary)))
        }
    }

    func testVersionFieldsAndOptionalProvenanceAreStrict() throws {
        let baseline = try XCTUnwrap(catalogRow(id: first, sequence: 1).object)
        for key in ["imported_at_unix_ms", "catalog_sequence", "ingress_run_id", "source_kind"] {
            var changed = baseline; changed.removeValue(forKey: key)
            XCTAssertThrowsError(try MediaAttachment(.object(changed)), key)
        }
        var extra = baseline; extra["send_authorized"] = .bool(true)
        XCTAssertThrowsError(try MediaAttachment(.object(extra)))
        for format in ["jarvis-attachment/1", "jarvis-attachment/3"] {
            var changed = baseline; changed["format"] = .string(format)
            XCTAssertThrowsError(try MediaAttachment(.object(changed)))
        }
        for key in ["ingress_run_id", "source_kind"] {
            let bad: [RPCValue] = key == "ingress_run_id"
                ? [.string(""), .string(String(repeating: "F", count: 32)), .string(String(repeating: "a", count: 31)), .bool(true), .number(1)]
                : [.string("native"), .string("image"), .string("Voice"), .string(""), .bool(true), .number(1)]
            for value in bad {
                var changed = baseline; changed[key] = value
                XCTAssertThrowsError(try MediaAttachment(.object(changed)), "\(key): \(value)")
            }
        }
    }

    func testCatalogOrderUsesSessionSequenceEvenWhenClockAndUUIDDisagree() throws {
        let old = try MediaAttachment(catalogRow(id: first, sequence: 1, imported: 200))
        let newest = try MediaAttachment(catalogRow(id: third, sequence: 2, imported: 100))
        let legacy = try MediaAttachment(catalogRow(id: second))
        XCTAssertEqual(MediaAttachment.catalogOrder([legacy, old, newest]).map(\.id), [third, first, second])
        let sameTime = try MediaAttachment(catalogRow(id: first, sequence: 3, imported: 100))
        XCTAssertEqual(MediaAttachment.catalogOrder([newest, sameTime]).map(\.id), [first, third])
    }

    func testDefensiveTiesAndUnknownLegacyOrderAreStableWithoutInventingLatest() throws {
        let tiedFirst = try MediaAttachment(catalogRow(id: first, sequence: 1))
        let tiedSecond = try MediaAttachment(catalogRow(id: second, sequence: 1))
        let legacyFirst = try MediaAttachment(catalogRow(id: first))
        let legacySecond = try MediaAttachment(catalogRow(id: second))
        XCTAssertEqual(MediaAttachment.catalogOrder([tiedSecond, tiedFirst]).map(\.id), [first, second])
        XCTAssertEqual(MediaAttachment.catalogOrder([legacySecond, legacyFirst]).map(\.id), [first, second])
    }

    @MainActor func testRefreshShowsNewestArrivalFirstAndRevokedMetadataCannotSelectContent() async throws {
        let old = catalogRow(id: first, sequence: 1, source: "telegram", sourceKind: "voice")
        let newest = catalogRow(id: third, sequence: 3, source: "telegram", sourceKind: "voice")
        let revoked = catalogRow(id: second, sequence: 4, source: "telegram", sourceKind: "voice", revoked: true)
        let fixture = MediaCatalogRPCFixture([old, newest, revoked])
        let store = MediaAttachmentsStore(request: { method, _ in
            guard method == "media.list" else { throw RPCFailure.invalidResponse }
            return await fixture.list()
        }, isActive: { true })
        await store.setScope(SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true))
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(store.attachments.map(\.id), [second, third, first])
        XCTAssertEqual(store.attachments.filter { !$0.revoked }.first?.id, third)
        store.toggle(try MediaAttachment(revoked)); XCTAssertTrue(store.selectedIDs.isEmpty)
        store.toggle(try MediaAttachment(newest)); XCTAssertEqual(store.selectedIDs, [third])
        await store.refresh(); XCTAssertEqual(store.selectedIDs, [third])
    }

    @MainActor func testRefreshRejectsAmbiguousDuplicateSequenceIncludingRevokedRows() async {
        let fixture = MediaCatalogRPCFixture([
            catalogRow(id: first, sequence: 1), catalogRow(id: second, sequence: 1, revoked: true)
        ])
        let store = MediaAttachmentsStore(request: { method, _ in
            guard method == "media.list" else { throw RPCFailure.invalidResponse }
            return await fixture.list()
        }, isActive: { true })
        await store.setScope(SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true))
        XCTAssertNotNil(store.errorMessage); XCTAssertTrue(store.attachments.isEmpty)
        XCTAssertTrue(store.selectedIDs.isEmpty)
    }
}
