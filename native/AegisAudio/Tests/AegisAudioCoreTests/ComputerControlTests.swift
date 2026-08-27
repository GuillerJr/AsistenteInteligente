import CoreGraphics
import Foundation
import Testing
@testable import AegisAudioCore

private let visualContext = String(repeating: "a", count: 64)
private let userInputCounter: UInt32 = 123_456

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
    #expect(ComputerControlCapturePolicy.bindsTargetProcess)
    #expect(ComputerControlCapturePolicy.bindsFocusedWindowDisplay)
    #expect(ComputerControlCapturePolicy.includeMenuBar == false)
    #expect(ComputerControlCapturePolicy.showCursor == false)
    #expect(ComputerControlCapturePolicy.captureAudio == false)
}

@Test func computerDisplayPlanSelectsTheLargestFocusedWindowOverlap() throws {
    let main = ComputerDisplayCandidate(
        identifier: 1,
        bounds: CGRect(x: 0, y: 0, width: 1_440, height: 900)
    )
    let external = ComputerDisplayCandidate(
        identifier: 2,
        bounds: CGRect(x: 1_440, y: 0, width: 1_120, height: 900)
    )

    let selected = try #require(
        ComputerDisplayPlan.select(
            windowPosition: CGPoint(x: 1_200, y: 80),
            windowSize: CGSize(width: 800, height: 600),
            candidates: [main, external]
        )
    )

    #expect(selected.identifier == external.identifier)
    #expect(selected.bounds == external.bounds)
}

@Test func computerDisplayPlanIsDeterministicAndFailsClosed() throws {
    let left = ComputerDisplayCandidate(
        identifier: 1,
        bounds: CGRect(x: 0, y: 0, width: 100, height: 100)
    )
    let right = ComputerDisplayCandidate(
        identifier: 2,
        bounds: CGRect(x: 100, y: 0, width: 100, height: 100)
    )
    let tie = try #require(
        ComputerDisplayPlan.select(
            windowPosition: CGPoint(x: 50, y: 0),
            windowSize: CGSize(width: 100, height: 100),
            candidates: [right, left]
        )
    )

    #expect(tie.identifier == left.identifier)
    #expect(
        ComputerDisplayPlan.select(
            windowPosition: CGPoint(x: 300, y: 0),
            windowSize: CGSize(width: 100, height: 100),
            candidates: [left, right]
        ) == nil
    )
    #expect(
        ComputerDisplayPlan.select(
            windowPosition: .zero,
            windowSize: .zero,
            candidates: [left]
        ) == nil
    )
    #expect(
        ComputerDisplayPlan.select(
            windowPosition: .zero,
            windowSize: CGSize(width: 100, height: 100),
            candidates: [left, left]
        ) == nil
    )
    #expect(
        ComputerDisplayPlan.select(
            windowPosition: .zero,
            windowSize: CGSize(width: 100, height: 100),
            candidates: [
                left,
                ComputerDisplayCandidate(identifier: 3, bounds: .zero),
            ]
        ) == nil
    )
}

@Test func computerEventDeliveryIsBoundToOneProcess() {
    #expect(ComputerEventDeliveryPolicy.bindsObservationGeometry)
    #expect(ComputerEventDeliveryPolicy.bindsProcessLifetime)
    #expect(ComputerEventDeliveryPolicy.bindsTargetProcess)
    #expect(ComputerEventDeliveryPolicy.yieldsToPhysicalUserInput)
    #expect(ComputerEventDeliveryPolicy.postsToGlobalHIDStream == false)
}

@Test func computerUserInputCounterBindsActionsToThePhysicalEventStream() {
    #expect(
        ComputerUserInputCounter.permitsAction(
            expected: userInputCounter,
            current: userInputCounter
        )
    )
    #expect(
        !ComputerUserInputCounter.permitsAction(
            expected: userInputCounter,
            current: userInputCounter + 1
        )
    )
}

@Test func computerVisualContextBindsProcessDisplayAndWindowGeometry() throws {
    let display = ComputerDisplayCandidate(
        identifier: 7,
        bounds: CGRect(x: 1_440, y: 0, width: 1_120, height: 900)
    )
    let launchDate = Date(timeIntervalSince1970: 1_800_000_000)
    let first = try #require(
        ComputerVisualContext.make(
            bundleIdentifier: "com.apple.Safari",
            processIdentifier: 42,
            launchDate: launchDate,
            display: display,
            windowPosition: CGPoint(x: 1_500, y: 80),
            windowSize: CGSize(width: 800, height: 600)
        )
    )
    let matching = ComputerVisualContext.make(
        bundleIdentifier: "com.apple.Safari",
        processIdentifier: 42,
        launchDate: launchDate,
        display: display,
        windowPosition: CGPoint(x: 1_500, y: 80),
        windowSize: CGSize(width: 800, height: 600)
    )
    let moved = ComputerVisualContext.make(
        bundleIdentifier: "com.apple.Safari",
        processIdentifier: 42,
        launchDate: launchDate,
        display: display,
        windowPosition: CGPoint(x: 1_501, y: 80),
        windowSize: CGSize(width: 800, height: 600)
    )
    let restarted = ComputerVisualContext.make(
        bundleIdentifier: "com.apple.Safari",
        processIdentifier: 43,
        launchDate: launchDate,
        display: display,
        windowPosition: CGPoint(x: 1_500, y: 80),
        windowSize: CGSize(width: 800, height: 600)
    )
    let otherDisplay = ComputerVisualContext.make(
        bundleIdentifier: "com.apple.Safari",
        processIdentifier: 42,
        launchDate: launchDate,
        display: ComputerDisplayCandidate(identifier: 8, bounds: display.bounds),
        windowPosition: CGPoint(x: 1_500, y: 80),
        windowSize: CGSize(width: 800, height: 600)
    )

    #expect(first.count == 64)
    #expect(first == matching)
    #expect(first != moved)
    #expect(first != restarted)
    #expect(first != otherDisplay)
    #expect(
        ComputerVisualContext.make(
            bundleIdentifier: "com.apple.Safari",
            processIdentifier: 42,
            launchDate: launchDate,
            display: display,
            windowPosition: .zero,
            windowSize: .zero
        ) == nil
    )
}

@Test func computerAccessibilityPolicyBoundsUnresponsiveApplications() {
    #expect(ComputerControlAccessibilityPolicy.actionMessagingTimeoutSeconds == 1)
    #expect(ComputerControlAccessibilityPolicy.perceptionMessagingTimeoutSeconds == 0.15)
    #expect(ComputerControlAccessibilityPolicy.perceptionBudgetMilliseconds == 500)
    #expect(ComputerControlAccessibilityPolicy.maximumWindows == 8)
    #expect(ComputerControlAccessibilityPolicy.maximumItems == 32)
    #expect(ComputerControlAccessibilityPolicy.maximumElements == 256)
    #expect(ComputerControlAccessibilityPolicy.maximumDepth == 8)
    #expect(ComputerControlAccessibilityPolicy.maximumChildrenPerElement == 24)
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
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )

    let command = try ComputerControlCommand.decode(data)

    #expect(command.action == "click")
    #expect(command.x == 500)
    #expect(command.y == 420)
    #expect(command.target == "Documentación")
    #expect(command.expectedUserInputCounter == userInputCounter)
}

@Test func computerControlRejectsUnknownFieldsAndMalformedActions() {
    let unknown = Data(
        """
        {"command":"status","protocol_version":"1.0","unexpected":true}
        """.utf8
    )
    let incomplete = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","protocol_version":"1.0","x":500,"y":420}
        """.utf8
    )
    let staleContext = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(String(repeating: "A", count: 64))","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )
    let missingContext = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )
    let missingUserInputCounter = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_visual_context":"\(visualContext)","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )
    let nonASCIIContext = Data(
        """
        {"action":"click","button":"left","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(String(repeating: "０", count: 64))","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )

    #expect(throws: ComputerControlCommandError.invalidCommand) {
        try ComputerControlCommand.decode(unknown)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(incomplete)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(staleContext)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(missingContext)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(missingUserInputCounter)
    }
    #expect(throws: ComputerControlCommandError.invalidAction) {
        try ComputerControlCommand.decode(nonASCIIContext)
    }
}

@Test func computerControlRejectsClicksThatNeedTheUserPointer() {
    let rightClick = Data(
        """
        {"action":"click","button":"right","click_count":1,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
        """.utf8
    )
    let doubleClick = Data(
        """
        {"action":"click","button":"left","click_count":2,"command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","protocol_version":"1.0","target":"Documentación","x":500,"y":420}
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
    let response: [String: Any] = [
        "status": "ok", "display_identifier": 7,
    ]

    #expect(ComputerPointerEvent(command: click, response: response)?.displayIdentifier == 7)
    #expect(ComputerPointerEvent(command: click, response: response)?.normalizedX == 420)
    #expect(ComputerPointerEvent(command: click, response: response)?.normalizedY == 360)
    #expect(ComputerPointerEvent(command: rightClick, response: response) == nil)
    #expect(ComputerPointerEvent(command: unboundClick, response: response) == nil)
    #expect(ComputerPointerEvent(command: click, response: ["status": "error"]) == nil)
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

@Test func computerTextInputPlanPreservesUnicodeGraphemes() throws {
    let text = String(repeating: "a", count: 19) + "🤖" + "éxito"
    let chunks = ComputerTextInputPlan.chunks(text)
    let second = try #require(chunks.dropFirst().first)

    #expect(chunks.map(\.count) == [19, 7])
    #expect(String(decoding: chunks.flatMap { $0 }, as: UTF16.self) == text)
    let robot = Array("🤖".utf16)
    #expect(second.starts(with: robot))
}

@Test func computerTextInputPlanIsBoundedForPlainText() {
    let chunks = ComputerTextInputPlan.chunks(String(repeating: "a", count: 41))

    #expect(chunks.map(\.count) == [20, 20, 1])
}

@Test func computerTextInputPlanRejectsInvalidChunkLimit() {
    #expect(ComputerTextInputPlan.chunks("texto", maximumUTF16Units: 0).isEmpty)
}

@Test func computerScrollPlanMapsOnlyBoundedDirections() throws {
    let down = try #require(ComputerScrollPlan(direction: "down", amount: 3))
    let left = try #require(ComputerScrollPlan(direction: "left", amount: 2))

    #expect(down.verticalDelta == -3)
    #expect(down.horizontalDelta == 0)
    #expect(left.verticalDelta == 0)
    #expect(left.horizontalDelta == 2)
    #expect(ComputerScrollPlan(direction: "diagonal", amount: 3) == nil)
    #expect(ComputerScrollPlan(direction: "up", amount: 9) == nil)
}

@Test func computerScrollPlanTargetsOnlyAVisibleWindowCenter() {
    let display = CGRect(x: 0, y: 0, width: 1_440, height: 900)

    #expect(
        ComputerScrollPlan.target(
            windowPosition: CGPoint(x: 100, y: 80),
            windowSize: CGSize(width: 800, height: 600),
            displayBounds: display
        ) == CGPoint(x: 500, y: 380)
    )
    #expect(
        ComputerScrollPlan.target(
            windowPosition: CGPoint(x: 1_200, y: 80),
            windowSize: CGSize(width: 800, height: 600),
            displayBounds: CGRect(x: 1_440, y: 0, width: 1_120, height: 900)
        ) == CGPoint(x: 1_720, y: 380)
    )
    #expect(
        ComputerScrollPlan.target(
            windowPosition: CGPoint(x: 1_500, y: 80),
            windowSize: CGSize(width: 800, height: 600),
            displayBounds: display
        ) == nil
    )
    #expect(
        ComputerScrollPlan.target(
            windowPosition: .zero,
            windowSize: .zero,
            displayBounds: display
        ) == nil
    )
}

@Test func computerControlAllowsOnlyBoundedNavigationShortcuts() throws {
    let left = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"left","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let addressBar = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"l","modifiers":["command"],"protocol_version":"1.0"}
        """.utf8
    )
    let quit = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"q","modifiers":["command"],"protocol_version":"1.0"}
        """.utf8
    )
    let delete = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"delete","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let enter = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"enter","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let space = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"space","modifiers":[],"protocol_version":"1.0"}
        """.utf8
    )
    let unmodifiedLetter = Data(
        """
        {"action":"key","command":"act","expected_bundle_identifier":"com.apple.Safari","expected_user_input_counter":\(userInputCounter),"expected_visual_context":"\(visualContext)","key":"a","modifiers":[],"protocol_version":"1.0"}
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
