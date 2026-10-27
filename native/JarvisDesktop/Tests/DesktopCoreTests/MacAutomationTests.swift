import AppKit
import CoreGraphics
import Foundation
import XCTest
@testable import DesktopCore

@MainActor private final class MacFixtureDriver: MacAutomationDriver {
    let target = MacAutomationTarget(bundleID: "com.jarvis.fixture", pid: 123,
        launchID: String(repeating: "a", count: 64))
    var value = "BASE"
    var range = [4, 0]
    var geometry: [Double] = [10, 20, 600, 400]
    var calls: [String] = []
    var permission = true
    var changedIdentity = false
    var failAfterEffect = false
    var effect = MacAutomationEffect(verified: true, verification: "fixture_value_reread")
    var frameContinuation: CheckedContinuation<RPCValue, Error>?
    var holdFrame = false
    var readFailure = false
    var textView: NSTextView?
    var button: NSButton?
    var window: NSWindow?
    var customTree: MacAutomationTree?
    var customFrame: RPCValue?
    var holdBeforeEffect = false
    var effectContinuation: CheckedContinuation<Void, Never>?

    func status() -> RPCValue { .object(["accessibility": .bool(permission), "automatic_permission_prompt": .bool(false)]) }
    func resolve(bundleID: String) throws -> [MacAutomationTarget] {
        calls.append("resolve:" + bundleID); return bundleID == target.bundleID ? [target] : []
    }
    func validate(_ target: MacAutomationTarget) throws {
        guard !changedIdentity, target == self.target else { throw MacAutomationFailure("target_changed") }
        guard permission else { throw MacAutomationFailure("accessibility_permission_required") }
    }
    func windows(_ target: MacAutomationTarget) throws -> [MacAutomationWindow] {
        try validate(target); calls.append("windows"); return [MacAutomationWindow(id: "window", title: "Sólo prueba")]
    }
    func read(_ target: MacAutomationTarget, windowID: String) throws -> MacAutomationTree {
        try validate(target); guard windowID == "window", !readFailure else { throw MacAutomationFailure("target_changed") }
        calls.append("read")
        if let customTree { return customTree }
        let currentValue = textView?.string ?? value
        let currentRange = textView.map { [$0.selectedRange().location, $0.selectedRange().length] } ?? range
        return MacAutomationTree(nodes: [
            MacAutomationNode(id: "window-node", role: "AXWindow", label: "Sólo prueba", actions: ["click_at", "drag", "key"]),
            MacAutomationNode(id: "editor", parentID: "window-node", role: "AXTextArea", label: "Campo sintético",
                value: currentValue, actions: ["set_value", "type_text"], selectedRange: currentRange),
            MacAutomationNode(id: "secret", parentID: "window-node", role: "AXTextField", label: "NO PUBLICAR ETIQUETA",
                value: "NO PUBLICAR CLAVE", secure: true, actions: ["set_value"]),
            MacAutomationNode(id: "button", parentID: "window-node", role: "AXButton", label: "Incrementar", actions: ["press"])
        ], bounds: geometry)
    }
    func perform(_ target: MacAutomationTarget, windowID: String, nodeID: String,
                 action: MacAutomationAction, beforeEffect: @MainActor () async throws -> Void) async throws -> MacAutomationEffect {
        if holdBeforeEffect { await withCheckedContinuation { effectContinuation = $0 } }
        try await beforeEffect(); try validate(target); calls.append("effect:" + action.name)
        if action.name == "set_value" { value = action.value!; textView?.string = value }
        if action.name == "type_text" {
            let source = textView?.string ?? value
            XCTAssertEqual(action.expectedValue, source)
            XCTAssertEqual(action.expectedSelection, range)
            value = (source as NSString).replacingCharacters(in: NSRange(location: range[0], length: range[1]), with: action.value!)
            textView?.string = value
        }
        if action.name == "press" { button?.performClick(nil) }
        if failAfterEffect { readFailure = true }
        return effect
    }
    func frame(_ target: MacAutomationTarget, windowID: String) async throws -> RPCValue {
        calls.append("frame")
        if holdFrame { return try await withCheckedThrowingContinuation { frameContinuation = $0 } }
        if let customFrame { return customFrame }
        return .object(["data_url": .string("data:image/jpeg;base64,/9j/2Q=="), "storage": .string("memory_only")])
    }
    func reset() { calls.append("reset") }
}

@MainActor private final class MacFixtureCounter: NSObject {
    var count = 0
    @objc func increment(_ sender: Any?) { count += 1 }
}

final class MacAutomationTests: XCTestCase {
    @MainActor func testSystemPermissionsAndKeychainRemainOwnerOnly() throws {
        let driver = MacAutomationAXDriver()
        for bundle in MacAutomationAXDriver.ownerOnlyBundles {
            XCTAssertThrowsError(try driver.resolve(bundleID: bundle)) {
                XCTAssertEqual(($0 as? MacAutomationFailure)?.code, "owner_only_target")
            }
            XCTAssertThrowsError(try driver.validate(MacAutomationTarget(bundleID: bundle, pid: 123,
                launchID: String(repeating: "a", count: 64)))) {
                XCTAssertEqual(($0 as? MacAutomationFailure)?.code, "owner_only_target")
            }
        }
    }
    @MainActor private func op() -> RPCValue { .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()) }
    @MainActor private func arguments(_ driver: MacFixtureDriver, _ extra: [String: RPCValue] = [:]) -> [String: RPCValue] {
        ["operation_id": op(), "target": driver.target.wire, "window_ref": .string("window")].merging(extra) { _, v in v }
    }
    @MainActor private func observe(_ executor: MacAutomationExecutor, _ driver: MacFixtureDriver,
                                   frame: Bool = false) async throws -> RPCValue {
        try await executor.execute("desktop.observe", arguments: arguments(driver, ["include_frame": .bool(frame)]))
    }
    @MainActor private func action(_ snapshot: RPCValue, _ driver: MacFixtureDriver, node: String = "editor",
                                  kind: String = "set_value", extra: [String: RPCValue] = [:]) -> [String: RPCValue] {
        let elements = snapshot["elements"].array!
        let index = ["window-node": 0, "editor": 1, "secret": 2, "button": 3][node]!
        return arguments(driver, ["snapshot_id": snapshot["snapshot_id"], "ref": elements[index]["ref"],
            "action": .string(kind)].merging(extra) { _, v in v })
    }

    @MainActor func testStatusDoesNotChooseOrReadAnApplicationAndResolveIsExplicit() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let status = try await executor.execute("desktop.status", arguments: [:])
        XCTAssertEqual(status["automatic_permission_prompt"], .bool(false)); XCTAssertTrue(driver.calls.isEmpty)
        let result = try await executor.execute("desktop.resolve", arguments: ["operation_id": op(), "bundle_id": .string("com.jarvis.fixture")])
        XCTAssertEqual(result["targets"].array, [driver.target.wire]); XCTAssertEqual(driver.calls, ["resolve:com.jarvis.fixture"])
        let invalid = try await executor.execute("desktop.resolve", arguments: ["operation_id": op(), "bundle_id": .string("com.jarvis.fixture"), "all_apps": .bool(true)])
        XCTAssertEqual(invalid["succeeded"], .bool(false)); XCTAssertEqual(driver.calls.count, 1)
    }

    @MainActor func testExactTargetIdentityAndPermissionAreRequiredBeforeReading() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        driver.permission = false
        let denied = try await observe(executor, driver)
        XCTAssertEqual(denied["error_code"], .string("accessibility_permission_required")); XCTAssertTrue(driver.calls.isEmpty)
        driver.permission = true; driver.changedIdentity = true
        let changed = try await observe(executor, driver)
        XCTAssertEqual(changed["error_code"], .string("target_changed")); XCTAssertTrue(driver.calls.isEmpty)
    }

    @MainActor func testObservationHasOpaqueRefsAndNeverExposesProtectedValuesOrLabels() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let snapshot = try await observe(executor, driver)
        let bytes = try JSONEncoder().encode(snapshot), string = String(decoding: bytes, as: UTF8.self)
        XCTAssertFalse(string.contains("NO PUBLICAR")); XCTAssertFalse(string.contains("window-node"))
        XCTAssertEqual(snapshot["expires_in_seconds"], .number(120))
        let protected = snapshot["elements"].array![2]
        XCTAssertEqual(protected["value"], .null); XCTAssertEqual(protected["actions"], .array([]))
        let denied = try await executor.execute("desktop.action", arguments: action(snapshot, driver, node: "secret", extra: ["value": .string("x")]))
        XCTAssertEqual(denied["error_code"], .string("secure_element")); XCTAssertFalse(driver.calls.contains("effect:set_value"))
    }

    @MainActor func testNativeEffectUsesFreshSnapshotAndCannotReplayOperationOrReferences() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let snapshot = try await observe(executor, driver), request = action(snapshot, driver, extra: ["value": .string("PRUEBA")])
        let result = try await executor.execute("desktop.action", arguments: request)
        XCTAssertEqual(result["verified"], .bool(true)); XCTAssertEqual(driver.value, "PRUEBA")
        let duplicate = try await executor.execute("desktop.action", arguments: request)
        XCTAssertEqual(duplicate["error_code"], .string("operation_already_used"))
        var repeated = request; repeated["operation_id"] = op()
        let oldRef = try await executor.execute("desktop.action", arguments: repeated)
        XCTAssertEqual(oldRef["error_code"], .string("stale_snapshot"))
        XCTAssertEqual(driver.calls.filter { $0.hasPrefix("effect:") }.count, 1)
    }

    @MainActor func testChangedValueCursorGeometryOrDestinationBlocksTheEffect() async throws {
        for change in 0...3 {
            let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
            let snapshot = try await observe(executor, driver)
            if change == 0 { driver.value = "CAMBIÓ" }
            if change == 1 { driver.range = [0, 0] }
            if change == 2 { driver.geometry[0] += 1 }
            if change == 3 { driver.changedIdentity = true }
            let result = try await executor.execute("desktop.action", arguments: action(snapshot, driver, extra: ["value": .string("x")]))
            XCTAssertEqual(result["succeeded"], .bool(false)); XCTAssertFalse(driver.calls.contains("effect:set_value"))
        }
    }

    @MainActor func testReviewCanTake119SecondsButExpiredSnapshotDoesNotRenewItself() async throws {
        var time: TimeInterval = 100
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver, now: { time })
        let first = try await observe(executor, driver); time = 219
        let admitted = try await executor.execute("desktop.action", arguments: action(first, driver, extra: ["value": .string("OK")]))
        XCTAssertEqual(admitted["succeeded"], .bool(true))
        let second = try await observe(executor, driver); time = 340
        let expired = try await executor.execute("desktop.action", arguments: action(second, driver, extra: ["value": .string("NO")]))
        XCTAssertEqual(expired["error_code"], .string("stale_snapshot")); XCTAssertEqual(driver.value, "OK")
    }

    @MainActor func testCancelledCommandAndResetCannotDeliverAnOldFrameOrReleaseAnActiveLane() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let request = arguments(driver, ["include_frame": .bool(true)])
        await executor.cancel(operationID: request["operation_id"]!.string!)
        let cancelled = try await executor.execute("desktop.observe", arguments: request)
        XCTAssertEqual(cancelled["error_code"], .string("cancelled")); XCTAssertTrue(driver.calls.isEmpty)
        driver.holdFrame = true
        let pending = Task { try await self.observe(executor, driver, frame: true) }
        for _ in 0..<100 where driver.frameContinuation == nil { await Task.yield() }
        XCTAssertNotNil(driver.frameContinuation)
        await executor.reset()
        let busy = try await observe(executor, driver)
        XCTAssertEqual(busy["error_code"], .string("busy"))
        driver.frameContinuation?.resume(returning: .object(["storage": .string("memory_only")]))
        let result = try await pending.value
        XCTAssertEqual(result["error_code"], .string("cancelled")); XCTAssertNil(result["elements"].array)
    }

    @MainActor func testPostDispatchReadFailureRemainsUnknownAndDoesNotReportSuccess() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let snapshot = try await observe(executor, driver); driver.failAfterEffect = true
        let result = try await executor.execute("desktop.action", arguments: action(snapshot, driver, extra: ["value": .string("ESCRITO")]))
        XCTAssertEqual(driver.value, "ESCRITO"); XCTAssertEqual(result["succeeded"], .bool(false))
        XCTAssertEqual(result["effect_status"], .string("unknown")); XCTAssertEqual(result["permits_replay"], .bool(false))
    }

    @MainActor func testCoordinatesAndKeysAreClosedAndInputDispatchRemainsUnverified() async throws {
        let invalid: [[String: RPCValue]] = [["point": .array([.number(-0.1), .number(0.5)])],
            ["point": .array([.number(0), .number(0)]), "end_point": .array([.number(1), .number(1)])],
            ["point": .array([.number(0), .number(0)]), "value": .string("unexpected")]]
        for extra in invalid {
            let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
            let snapshot = try await observe(executor, driver)
            let result = try await executor.execute("desktop.action", arguments: action(snapshot, driver, node: "window-node", kind: "click_at", extra: extra))
            XCTAssertEqual(result["succeeded"], .bool(false)); XCTAssertFalse(driver.calls.contains("effect:click_at"))
        }
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let snapshot = try await observe(executor, driver)
        let clipboard = try await executor.execute("desktop.action", arguments: action(snapshot, driver, node: "window-node", kind: "key", extra: ["key": .string("cmd+v")]))
        XCTAssertEqual(clipboard["succeeded"], .bool(false))
        let next = try await observe(executor, driver)
        driver.effect = MacAutomationEffect(verified: false, verification: "no_ACK", effectStatus: "unknown")
        let sent = try await executor.execute("desktop.action", arguments: action(next, driver, node: "window-node", kind: "key", extra: ["key": .string("return")]))
        XCTAssertEqual(sent["succeeded"], .bool(false)); XCTAssertEqual(sent["verified"], .bool(false))
        XCTAssertEqual(sent["effect_status"], .string("unknown"))
    }

    @MainActor func testOwnAppKitTextViewPreservesUnicodeAndSelectionInTheExecutionContract() async throws {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 10, y: 20, width: 600, height: 400),
                              styleMask: [.titled], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        defer { window.close() }
        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 300, height: 200))
        text.string = "BASE 🧪 FINAL"; text.setSelectedRange(NSRange(location: 8, length: 0))
        window.contentView = text
        let driver = MacFixtureDriver(); driver.textView = text; driver.range = [8, 0]
        let executor = MacAutomationExecutor(driver: driver), snapshot = try await observe(executor, driver)
        let result = try await executor.execute("desktop.action", arguments: action(snapshot, driver, kind: "type_text", extra: ["value": .string("\nPRUEBA\n")]))
        XCTAssertEqual(result["verified"], .bool(true)); XCTAssertEqual(text.string, "BASE 🧪 \nPRUEBA\nFINAL")
        XCTAssertTrue(result["elements"].array!.contains { $0["value"].string == text.string })
    }

    @MainActor func testWindowFrameEncodingIsJPEGInMemoryAndBounded() throws {
        let context = try XCTUnwrap(CGContext(data: nil, width: 1600, height: 1200, bitsPerComponent: 8,
            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
        context.setFillColor(CGColor(gray: 0.4, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: 1600, height: 1200))
        let image = try XCTUnwrap(context.makeImage())
        let bytes = try MacAutomationAXDriver.encodeFrame(image)
        XCTAssertLessThanOrEqual(bytes.count, 48 * 1024)
        XCTAssertEqual(Array(bytes.prefix(2)), [0xff, 0xd8])
    }

    @MainActor func testNewObservationRetiresOldRefsOnlyForItsOwnWindow() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let first = try await observe(executor, driver), latest = try await observe(executor, driver)
        let stale = try await executor.execute("desktop.action", arguments: action(first, driver, extra: ["value": .string("NO")]))
        XCTAssertEqual(stale["error_code"], .string("stale_snapshot"))
        let accepted = try await executor.execute("desktop.action", arguments: action(latest, driver, extra: ["value": .string("SÍ")]))
        XCTAssertEqual(accepted["verified"], .bool(true)); XCTAssertEqual(driver.value, "SÍ")
    }

    @MainActor func testSerializedObservationBoundsUnicodeAndLargeLabelsTogetherWithAFrame() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        driver.customTree = MacAutomationTree(nodes: (0..<160).map { n in
            MacAutomationNode(id: "node\(n)", role: "AXTextField", label: String(repeating: "L", count: 512),
                value: String(repeating: "🧪", count: 3000), actions: ["set_value"])
        })
        driver.customFrame = .object(["data_url": .string("data:image/jpeg;base64," + String(repeating: "a", count: 65_536)),
            "storage": .string("memory_only")])
        let result = try await observe(executor, driver, frame: true)
        XCTAssertEqual(result["succeeded"], .bool(true)); XCTAssertEqual(result["truncated"], .bool(true))
        XCTAssertLessThan(try JSONEncoder().encode(result).count, 128 * 1024)
        let total = result["elements"].array!.reduce(0) { $0 + ($1["value"].string?.utf8.count ?? 0) }
        XCTAssertLessThanOrEqual(total, 12_000)
    }

    @MainActor func testInputPostconditionPollsOnlyReadbackAndDoesNotRepeatTheInput() async throws {
        var reads = 0, pauses = 0
        let match = try await MacAutomationAXDriver.confirmPostcondition(expected: "EXACTO", read: {
            reads += 1; return reads == 3 ? "EXACTO" : "anterior"
        }, beforeRead: {}, pause: { pauses += 1 })
        XCTAssertTrue(match); XCTAssertEqual(reads, 3); XCTAssertEqual(pauses, 2)
        reads = 0
        let unknown = try await MacAutomationAXDriver.confirmPostcondition(expected: "EXACTO", read: {
            reads += 1; return "otro"
        }, beforeRead: {}, pause: {})
        XCTAssertFalse(unknown); XCTAssertEqual(reads, 8)
    }

    @MainActor func testInputPostconditionCannotPromoteAResultAfterCancellationOrForAProtectedRef() async throws {
        var reads = 0
        do {
            _ = try await MacAutomationAXDriver.confirmPostcondition(expected: "EXACTO", read: {
                reads += 1; return "EXACTO"
            }, beforeRead: { throw MacAutomationFailure("cancelled") }, pause: {})
            XCTFail("La cancelación debe impedir la lectura y la verificación.")
        } catch {}
        XCTAssertEqual(reads, 0)
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let snapshot = try await observe(executor, driver)
        let result = try await executor.execute("desktop.action", arguments: action(snapshot, driver, node: "window-node", kind: "key",
            extra: ["key": .string("return"), "verify_ref": snapshot["elements"].array![2]["ref"], "expected_value": .string("secreto")]))
        XCTAssertEqual(result["error_code"], .string("invalid_verification")); XCTAssertFalse(driver.calls.contains("effect:key"))
    }

    @MainActor func testCentralLeaseIsCheckedAgainAfterNativeWaitBeforeAnyEffect() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        var admitted = true, checked: [String] = []
        executor.beforeEffect = { operation in
            checked.append(operation)
            if !admitted { throw RPCFailure.remote(-32003, "Revocado") }
        }
        let snapshot = try await observe(executor, driver)
        let request = action(snapshot, driver, extra: ["value": .string("NO ESCRIBIR")])
        driver.holdBeforeEffect = true
        let pending = Task { try await executor.execute("desktop.action", arguments: request) }
        for _ in 0..<100 where driver.effectContinuation == nil { await Task.yield() }
        XCTAssertNotNil(driver.effectContinuation); admitted = false
        driver.effectContinuation?.resume()
        let result = try await pending.value
        XCTAssertEqual(result["error_code"], .string("authorization_unconfirmed"))
        XCTAssertEqual(result["effect_status"], .string("known")); XCTAssertEqual(driver.value, "BASE")
        XCTAssertEqual(checked.filter { $0 == request["operation_id"]?.string }.count, 2)
        XCTAssertFalse(driver.calls.contains("effect:set_value"))
    }

    @MainActor func testCancellationDuringCentralLeaseCheckDoesNotReadNativeDataOrFreeTheLaneEarly() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        var check: CheckedContinuation<Void, Never>?
        executor.beforeEffect = { _ in await withCheckedContinuation { check = $0 } }
        let request = arguments(driver)
        let pending = Task { try await executor.execute("desktop.observe", arguments: request) }
        for _ in 0..<100 where check == nil { await Task.yield() }
        XCTAssertNotNil(check)
        await executor.cancel(operationID: request["operation_id"]!.string!)
        let busy = try await observe(executor, driver)
        XCTAssertEqual(busy["error_code"], .string("busy")); XCTAssertTrue(driver.calls.isEmpty)
        check?.resume()
        let result = try await pending.value
        XCTAssertEqual(result["error_code"], .string("cancelled")); XCTAssertTrue(driver.calls.isEmpty)
    }

    @MainActor func testEmptyReplacementAndEmptyPostconditionAreValidExplicitValues() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        let snapshot = try await observe(executor, driver)
        let cleared = try await executor.execute("desktop.action", arguments: action(snapshot, driver,
            extra: ["value": .string(""), "verify_ref": snapshot["elements"].array![1]["ref"], "expected_value": .string("")]))
        XCTAssertEqual(cleared["verified"], .bool(true)); XCTAssertEqual(driver.value, "")
    }

    @MainActor func testPixelsAreWithheldWhenTheAccessibilityTreeIsIncomplete() async throws {
        let driver = MacFixtureDriver(), executor = MacAutomationExecutor(driver: driver)
        driver.customTree = MacAutomationTree(nodes: [MacAutomationNode(id: "root", role: "AXWindow")], truncated: true)
        let result = try await observe(executor, driver, frame: true)
        XCTAssertEqual(result["error_code"], .string("frame_requires_complete_accessibility"))
        XCTAssertFalse(driver.calls.contains("frame"))
    }

    @MainActor func testProtectedRectanglesArePaintedOverInMemoryBeforeEncoding() throws {
        let context = try XCTUnwrap(CGContext(data: nil, width: 100, height: 100, bitsPerComponent: 8,
            bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue))
        context.setFillColor(CGColor(gray: 1, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: 100, height: 100))
        let image = try XCTUnwrap(context.makeImage())
        let safe = try MacAutomationAXDriver.redactFrame(image, window: CGRect(x: 20, y: 30, width: 100, height: 100),
            protectedRects: [CGRect(x: 30, y: 50, width: 30, height: 10)])
        let bytes = try XCTUnwrap(safe.dataProvider?.data) as Data
        let stride = safe.bitsPerPixel / 8
        let black = (0..<safe.height).reduce(0) { count, y in count + (0..<safe.width).filter { x in
            let offset = y * safe.bytesPerRow + x * stride
            return bytes[offset] < 10 && bytes[offset + 1] < 10 && bytes[offset + 2] < 10
        }.count }
        XCTAssertEqual(black, 300)
        let protectedCrop = try XCTUnwrap(safe.cropping(to: CGRect(x: 10, y: 20, width: 30, height: 10)))
        let cropBytes = try XCTUnwrap(protectedCrop.dataProvider?.data) as Data
        let cropStride = protectedCrop.bitsPerPixel / 8
        for y in 0..<protectedCrop.height {
            for x in 0..<protectedCrop.width {
                let index = y * protectedCrop.bytesPerRow + x * cropStride
                XCTAssertLessThan(cropBytes[index], 10)
                XCTAssertLessThan(cropBytes[index + 1], 10)
                XCTAssertLessThan(cropBytes[index + 2], 10)
            }
        }
        XCTAssertLessThanOrEqual(try MacAutomationAXDriver.encodeFrame(safe).count, 48 * 1024)
    }

    @MainActor func testWindowReferenceIsStableAcrossBackendInstancesButChangesWithTheRealWindowID() throws {
        let target = MacFixtureDriver().target
        let first = MacAutomationAXDriver(), reopened = MacAutomationAXDriver()
        let original = first.stableWindowReference(target: target, number: 7319)
        XCTAssertEqual(original.count, 64)
        XCTAssertEqual(original, reopened.stableWindowReference(target: target, number: 7319))
        XCTAssertNotEqual(original, reopened.stableWindowReference(target: target, number: 7320))
        let relaunched = MacAutomationTarget(bundleID: target.bundleID, pid: target.pid, launchID: String(repeating: "b", count: 64))
        XCTAssertNotEqual(original, reopened.stableWindowReference(target: relaunched, number: 7319))
    }

    @MainActor func testPublicWindowMappingRejectsAmbiguityAndDoesNotUseTheTitleAsIdentity() throws {
        let target = MacFixtureDriver().target, rect = CGRect(x: 10, y: 20, width: 600, height: 400)
        let first = MacAutomationAXDriver.WindowRecord(number: 7319, ownerPID: target.pid, bounds: rect, title: "Documento")
        let replacement = MacAutomationAXDriver.WindowRecord(number: 7320, ownerPID: target.pid, bounds: rect, title: "Documento")
        XCTAssertEqual(try MacAutomationAXDriver.resolveWindowNumber(target: target, bounds: rect, title: "Documento", records: [first]), 7319)
        XCTAssertEqual(try MacAutomationAXDriver.resolveWindowNumber(target: target, bounds: rect, title: "Documento", records: [replacement]), 7320)
        XCTAssertThrowsError(try MacAutomationAXDriver.resolveWindowNumber(target: target, bounds: rect, title: "Documento", records: [first, replacement]))
        let other = MacAutomationAXDriver.WindowRecord(number: 8888, ownerPID: target.pid + 1, bounds: rect, title: "Documento")
        XCTAssertThrowsError(try MacAutomationAXDriver.resolveWindowNumber(target: target, bounds: rect, title: "Documento", records: [other]))
        let titleUnavailable = MacAutomationAXDriver.WindowRecord(number: 7319, ownerPID: target.pid, bounds: rect, title: nil)
        XCTAssertEqual(try MacAutomationAXDriver.resolveWindowNumber(target: target, bounds: rect, title: "Documento", records: [titleUnavailable]), 7319)
    }
}
