import XCTest
@testable import DesktopCore

final class MacAutomationSnapshotTests: XCTestCase {
    private static let root = MacAutomationNode(id: "window", role: "AXWindow", label: "Synthetic window", actions: ["click_at"])
    private static let editor = MacAutomationNode(id: "editor", parentID: "window", role: "AXTextArea", label: "Synthetic editor", value: "BASE", actions: ["set_value"])
    private static let zoom = MacAutomationNode(id: "zoom", parentID: "window", role: "AXButton", actions: ["press"])
    private func tree(_ extra: [MacAutomationNode]) -> MacAutomationTree {
        MacAutomationTree(nodes: [Self.root, Self.editor, Self.zoom] + extra)
    }

    func testWhollyEmptyChromeChainsDoNotChangeSemanticCASOrVisibleNodes() {
        let leaf = tree([MacAutomationNode(id: "empty-old", parentID: "zoom", role: "AXGroup")])
        let chain = tree([MacAutomationNode(id: "empty-new", parentID: "zoom", role: "AXGroup"),
            MacAutomationNode(id: "empty-new-child", parentID: "empty-new", role: "AXGroup")])
        XCTAssertEqual(leaf.digest, chain.digest)
        XCTAssertEqual(leaf.nodes.count, 4)
        XCTAssertEqual(chain.nodes.count, 5, "Pruning affects CAS only; observations retain their physical refs")
        var nodes: [MacAutomationNode] = []
        for index in 0..<157 {
            nodes.append(MacAutomationNode(id: "group\(index)",
                parentID: index == 0 ? "zoom" : "group\(index - 1)", role: "AXGroup"))
        }
        XCTAssertEqual(tree(nodes).digest, leaf.digest, "The fixed point remains bounded by the 160-node contract")
    }

    func testAnySemanticPayloadInAGroupSubtreeStillInvalidatesCAS() {
        let baseline = tree([MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup")]).digest
        let variants = [
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", label: "New instruction"),
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", value: ""),
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", secure: true),
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", enabled: false),
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", valueTruncated: true),
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", actions: ["press"]),
            MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup", selectedRange: [0, 0])]
        for variant in variants { XCTAssertNotEqual(tree([variant]).digest, baseline) }
        let emptyParent = MacAutomationNode(id: "group", parentID: "zoom", role: "AXGroup")
        for child in [MacAutomationNode(id: "child", parentID: "group", role: "AXStaticText", value: "New context"),
                      MacAutomationNode(id: "child", parentID: "group", role: "AXGroup", actions: ["press"])] {
            XCTAssertNotEqual(tree([emptyParent, child]).digest, baseline)
        }
    }

    func testMissingParentsCyclesAndDuplicateIDsAreNeverPruned() {
        let baseline = tree([]).digest
        XCTAssertNotEqual(tree([MacAutomationNode(id: "g", parentID: "missing", role: "AXGroup")]).digest, baseline)
        XCTAssertNotEqual(tree([MacAutomationNode(id: "g1", parentID: "g2", role: "AXGroup"),
            MacAutomationNode(id: "g2", parentID: "g1", role: "AXGroup")]).digest, baseline)
        XCTAssertNotEqual(tree([MacAutomationNode(id: "g", parentID: "zoom", role: "AXGroup"),
            MacAutomationNode(id: "g", parentID: "zoom", role: "AXGroup")]).digest, baseline)
    }

    @MainActor func testEmptyChromeGrowthAllowsTheSamePhysicalEditorOnly() async throws {
        let driver = ChromeSnapshotDriver(), executor = MacAutomationExecutor(driver: driver)
        let observed = try await executor.execute("desktop.observe", arguments: driver.observation())
        driver.grown = true
        let result = try await executor.execute("desktop.action", arguments: driver.action(observed))
        XCTAssertEqual(result["succeeded"].bool, true)
        XCTAssertEqual(result["verified"].bool, true)
        XCTAssertEqual(driver.dispatched, 1)
        XCTAssertEqual(driver.value, "CHANGED")
    }

    @MainActor func testReplacedEditorStillFailsEvenWhenItsTextAndChromeMatch() async throws {
        let driver = ChromeSnapshotDriver(), executor = MacAutomationExecutor(driver: driver)
        let observed = try await executor.execute("desktop.observe", arguments: driver.observation())
        driver.grown = true; driver.replaced = true
        let result = try await executor.execute("desktop.action", arguments: driver.action(observed))
        XCTAssertEqual(result["error_code"].string, "stale_snapshot")
        XCTAssertEqual(result["dispatched"].bool, false)
        XCTAssertEqual(driver.dispatched, 0)
        XCTAssertEqual(driver.value, "BASE")
    }
}

@MainActor private final class ChromeSnapshotDriver: MacAutomationDriver {
    let target = MacAutomationTarget(bundleID: "com.jarvis.synthetic", pid: 42,
        launchID: String(repeating: "a", count: 64))
    var grown = false, replaced = false
    var value = "BASE", dispatched = 0
    func operation() -> RPCValue { .string(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()) }
    func observation() -> [String: RPCValue] {
        ["operation_id": operation(), "target": target.wire, "window_ref": .string("window")]
    }
    func action(_ observed: RPCValue) -> [String: RPCValue] {
        ["operation_id": operation(), "target": target.wire, "window_ref": .string("window"),
         "snapshot_id": observed["snapshot_id"], "ref": observed["elements"].array![1]["ref"],
         "action": .string("set_value"), "value": .string("CHANGED")]
    }
    func status() -> RPCValue { .object([:]) }
    func resolve(bundleID: String) throws -> [MacAutomationTarget] { [target] }
    func validate(_ target: MacAutomationTarget) throws { if target != self.target { throw MacAutomationFailure("target_changed") } }
    func windows(_ target: MacAutomationTarget) throws -> [MacAutomationWindow] { [MacAutomationWindow(id: "window", title: "Synthetic")] }
    func read(_ target: MacAutomationTarget, windowID: String) throws -> MacAutomationTree {
        var nodes = [MacAutomationNode(id: "window", role: "AXWindow"),
            MacAutomationNode(id: replaced ? "other-editor" : "editor", parentID: "window", role: "AXTextArea",
                value: value, actions: ["set_value"]),
            MacAutomationNode(id: "zoom", parentID: "window", role: "AXButton", actions: ["press"]),
            MacAutomationNode(id: "empty", parentID: "zoom", role: "AXGroup")]
        if grown { nodes.append(MacAutomationNode(id: "empty-child", parentID: "empty", role: "AXGroup")) }
        return MacAutomationTree(nodes: nodes)
    }
    func perform(_ target: MacAutomationTarget, windowID: String, nodeID: String,
                 action: MacAutomationAction, beforeEffect: @MainActor () async throws -> Void) async throws -> MacAutomationEffect {
        guard nodeID == "editor", !replaced else { throw MacAutomationFailure("stale_snapshot") }
        try await beforeEffect()
        dispatched += 1; value = action.value!
        return MacAutomationEffect(verified: true, verification: "synthetic exact value readback")
    }
    func frame(_ target: MacAutomationTarget, windowID: String) async throws -> RPCValue { .null }
    func reset() {}
}
