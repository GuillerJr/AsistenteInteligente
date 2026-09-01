import Foundation
import Testing
@testable import AegisAudioCore

@Test
func activeVisionObservationUsesMetadataOnlyBridgeKeys() throws {
    let observation = ActiveVisionObservation(
        source: .screen,
        bundleIdentifier: "com.google.Chrome",
        pixelWidth: 1_000,
        pixelHeight: 800,
        frameSequence: 4,
        capturedMonotonicNanoseconds: 99,
        sceneSummary: "",
        elements: [
            ActiveVisionElement(
                kind: "text",
                label: "Buscar",
                confidence: 0.9,
                bounds: ActiveVisionRect(x: 0.1, y: 0.2, width: 0.3, height: 0.1)
            ),
        ]
    )

    let object = try #require(
        JSONSerialization.jsonObject(with: JSONEncoder().encode(observation))
            as? [String: Any]
    )
    #expect(object["pixel_width"] as? Int == 1_000)
    #expect(object["captured_monotonic_ns"] as? Int == 99)
    #expect(object["image_data"] == nil)
}

@Test
func activeVisionBridgeRequiresTheIPCKeySize() {
    #expect(throws: LocalIPCError.invalidCredential) {
        _ = try SignedActiveVisionBridge(secret: Data(repeating: 0, count: 31))
    }
}
