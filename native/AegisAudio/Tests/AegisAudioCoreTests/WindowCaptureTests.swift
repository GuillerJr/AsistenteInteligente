import CoreGraphics
import Testing
@testable import AegisAudioCore

@Test func windowCaptureSelectsTheExactAuthorizedGeometry() throws {
    let target = CGRect(x: 120, y: 80, width: 900, height: 620)
    let selected = WindowCapturePlan.select(
        accessibilityFrame: target,
        candidates: [
            WindowGeometryCandidate(index: 4, frame: CGRect(x: 0, y: 0, width: 1440, height: 900)),
            WindowGeometryCandidate(index: 8, frame: target.insetBy(dx: -2, dy: -2)),
            WindowGeometryCandidate(index: 9, frame: CGRect(x: 500, y: 200, width: 500, height: 400)),
        ]
    )

    #expect(selected == 8)
}

@Test func windowCaptureRejectsUnrelatedWindowGeometry() {
    let selected = WindowCapturePlan.select(
        accessibilityFrame: CGRect(x: 20, y: 20, width: 400, height: 300),
        candidates: [
            WindowGeometryCandidate(
                index: 1,
                frame: CGRect(x: 900, y: 500, width: 400, height: 300)
            ),
        ]
    )

    #expect(selected == nil)
}

@Test func windowCapturePreservesRetinaPixelDimensions() throws {
    let dimensions = try #require(WindowCapturePlan.pixelDimensions(
        frame: CGRect(x: 100, y: 50, width: 720, height: 450),
        displayBounds: CGRect(x: 0, y: 0, width: 1_440, height: 900),
        displayPixelWidth: 2_880,
        displayPixelHeight: 1_800
    ))

    #expect(dimensions.width == 1_440)
    #expect(dimensions.height == 900)
}

@Test func adaptiveWindowJPEGUsesExactQualitySchedule() {
    #expect(LocalImageEncoder.windowCaptureQualities == [
        0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65,
        0.60, 0.55, 0.50, 0.45, 0.40, 0.35, 0.30,
    ])
}
