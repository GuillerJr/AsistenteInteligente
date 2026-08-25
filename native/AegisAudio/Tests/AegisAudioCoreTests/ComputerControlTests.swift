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

@Test func computerControlAcceptsOneStrictNormalizedClick() throws {
    let data = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","x":500,"y":420}
        """.utf8
    )

    let command = try ComputerControlCommand.decode(data)

    #expect(command.action == "click")
    #expect(command.x == 500)
    #expect(command.y == 420)
}

@Test func computerControlRejectsUnknownFieldsAndMalformedActions() {
    let unknown = Data(
        """
        {"command":"status","protocol_version":"1.0","unexpected":true}
        """.utf8
    )
    let incomplete = Data(
        """
        {"action":"click","command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","x":500,"y":420}
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
        {"action":"click","button":"right","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","x":500,"y":420}
        """.utf8
    )
    let doubleClick = Data(
        """
        {"action":"click","button":"left","click_count":2,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","x":500,"y":420}
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
        "command": "act", "x": 420, "y": 360,
    ]
    let rightClick: [String: Any] = [
        "action": "click", "button": "right", "click_count": 1,
        "command": "act", "x": 420, "y": 360,
    ]

    #expect(ComputerPointerEvent(command: click)?.normalizedX == 420)
    #expect(ComputerPointerEvent(command: click)?.normalizedY == 360)
    #expect(ComputerPointerEvent(command: rightClick) == nil)
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
}

@Test func computerControlAllowsOnlyBoundedNavigationShortcuts() throws {
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

    #expect(try ComputerControlCommand.decode(addressBar).key == "l")
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(quit)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(delete)
    }
}
