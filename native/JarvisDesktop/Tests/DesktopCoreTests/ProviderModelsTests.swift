import XCTest
@testable import DesktopCore

final class ProviderModelsTests: XCTestCase {
    private func row(_ id: String, name: String = "Modelo", isDefault: Bool = false) -> RPCValue {
        .object(["id": .string(id), "display_name": .string(name),
                 "is_default": .bool(isDefault), "accepts_images": .bool(true)])
    }

    func testCatalogKeepsOrderAndDefaultWithoutChoosingAModel() throws {
        let models = try XCTUnwrap(ProviderModel.catalog(.object([
            "connected": .bool(true), "models": .array([row("gpt-a", isDefault: true), row("gpt-b")]),
        ])))
        XCTAssertEqual(models.map(\.id), ["gpt-a", "gpt-b"])
        XCTAssertEqual(models.map(\.isDefault), [true, false])
    }

    func testMalformedDuplicatedOrDisconnectedCatalogIsRejected() {
        XCTAssertNil(ProviderModel.catalog(.object(["connected": .bool(true),
                                                    "models": .array([row("gpt-a"), row("gpt-a")])])))
        XCTAssertNil(ProviderModel.catalog(.object(["connected": .bool(true),
                                                    "models": .array([row("")])])))
        XCTAssertNil(ProviderModel.catalog(.object(["connected": .bool(false),
                                                    "models": .array([row("gpt-a")])])))
        XCTAssertNil(ProviderModel.catalog(.object(["models": .array([])])))
        XCTAssertEqual(ProviderModel.catalog(.object(["connected": .bool(false), "models": .array([])])), [])
    }
}
