import CoreGraphics
import Foundation
import Testing
@testable import AegisAudioCore

@Test func computerPermissionPlanRequestsOneMissingCapabilityAtATime() {
    #expect(ComputerControlCapabilityState.ready.nextPermissionRequest == nil)
    #expect(ComputerControlCapabilityState.helperUnavailable.nextPermissionRequest == nil)
    #expect(
        ComputerControlCapabilityState.screenCaptureMissing.nextPermissionRequest == .screenCapture
    )
    #expect(
        ComputerControlCapabilityState.permissionsMissing.nextPermissionRequest == .screenCapture
    )
    #expect(
        ComputerControlCapabilityState.accessibilityMissing.nextPermissionRequest == .accessibility
    )
}

@Test func computerCapturePolicyExcludesUnrelatedSystemSurfaces() {
    #expect(ComputerControlCapturePolicy.includeMenuBar == false)
    #expect(ComputerControlCapturePolicy.showCursor == false)
    #expect(ComputerControlCapturePolicy.captureAudio == false)
}

@Test func computerVisualFingerprintIsDeterministicAndDetectsStructure() throws {
    let firstImage = try #require(makeFingerprintImage(brightLeft: true))
    let matchingImage = try #require(makeFingerprintImage(brightLeft: true))
    let changedImage = try #require(makeFingerprintImage(brightLeft: false))

    let first = try #require(ComputerVisualFingerprint.make(from: firstImage))
    let matching = try #require(ComputerVisualFingerprint.make(from: matchingImage))
    let changed = try #require(ComputerVisualFingerprint.make(from: changedImage))

    #expect(first.count == 64)
    #expect(first == matching)
    #expect(first != changed)
}

@Test func computerControlAcceptsOneStrictNormalizedClick() throws {
    let data = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )

    let command = try ComputerControlCommand.decode(data)

    #expect(command.action == "click")
    #expect(command.x == 500)
    #expect(command.y == 420)
    #expect(command.target == "Documentación")
}

@Test func computerControlRejectsUnknownFieldsAndMalformedActions() {
    let unknown = Data(
        """
        {"command":"status","protocol_version":"1.0","unexpected":true}
        """.utf8
    )
    let incomplete = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","x":500,"y":420}
        """.utf8
    )

    #expect(throws: ComputerControlCommandError.invalidCommand) {
        try ComputerControlCommand.decode(unknown)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(incomplete)
    }
}

@Test func computerControlRejectsClicksThatNeedTheUserPointer() {
    let rightClick = Data(
        """
        {"action":"click","button":"right","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )
    let doubleClick = Data(
        """
        {"action":"click","button":"left","click_count":2,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )

    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(rightClick)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(doubleClick)
    }
}

@Test func computerPointerEventAcceptsOnlyStrictLeftClicks() {
    let click: [String: Any] = [
        "action": "click", "button": "left", "click_count": 1,
        "command": "act", "target": "Documentación", "x": 420, "y": 360,
    ]
    let rightClick: [String: Any] = [
        "action": "click", "button": "right", "click_count": 1,
        "command": "act", "target": "Documentación", "x": 420, "y": 360,
    ]
    let unboundClick: [String: Any] = [
        "action": "click", "button": "left", "click_count": 1,
        "command": "act", "x": 420, "y": 360,
    ]

    #expect(ComputerPointerEvent(command: click)?.normalizedX == 420)
    #expect(ComputerPointerEvent(command: click)?.normalizedY == 360)
    #expect(ComputerPointerEvent(command: rightClick) == nil)
    #expect(ComputerPointerEvent(command: unboundClick) == nil)
}

@Test func computerControlRejectsRestrictedApplicationsAtNativeBoundary() {
    let data = Data(
        """
        {"bundle_identifier":"com.apple.Terminal","command":"activate","protocol_version":"1.0"}
        """.utf8
    )

    #expect(throws: ComputerControlCommandError.restrictedApplication) {
        try ComputerControlCommand.decode(data)
    }
}

@Test func computerControlSafetyBlocksSensitiveTargetsAndSecureTextRoles() {
    #expect(ComputerControlSafety.isSensitiveElementText("Confirm purchase") == true)
    #expect(ComputerControlSafety.isSensitiveElementText("Eliminar archivo") == true)
    #expect(ComputerControlSafety.isSensitiveElementText("Documentación") == false)
    #expect(ComputerControlSafety.isAllowedTextRole("AXTextField") == true)
    #expect(ComputerControlSafety.isAllowedTextRole("AXButton") == false)
    #expect(
        ComputerControlSafety.isSafePrintableText("Documentación", maximumLength: 256) == true
    )
    #expect(
        ComputerControlSafety.isSafePrintableText(
            "Abrir\u{202E}Ajustes",
            maximumLength: 256
        ) == false
    )
}

@Test func computerControlAllowsOnlyBoundedNavigationShortcuts() throws {
    let left = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"left","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let addressBar = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"l","modifiers":["command"],"protocol_version":"1.0"}
        """.utf8
    )
    let quit = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"q","modifiers":["command"],"protocol_version":"1.0"}
        """.utf8
    )
    let delete = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"delete","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let enter = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"enter","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let space = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"space","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let unmodifiedLetter = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","key":"a","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )

    #expect(try ComputerControlCommand.decode(left).key == "left")
    #expect(try ComputerControlCommand.decode(addressBar).key == "l")
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(quit)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(delete)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(enter)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(space)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(unmodifiedLetter)
    }
}

private func makeFingerprintImage(brightLeft: Bool) -> CGImage? {
    let width = 170
    let height = 160
    guard let context = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width * 4,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        return nil
    }
    context.setFillColor(CGColor(gray: brightLeft ? 1 : 0, alpha: 1))
    context.fill(CGRect(x: 0, y: 0, width: width / 2, height: height))
    context.setFillColor(CGColor(gray: brightLeft ? 0 : 1, alpha: 1))
    context.fill(CGRect(x: width / 2, y: 0, width: width / 2, height: height))
    return context.makeImage()
}
