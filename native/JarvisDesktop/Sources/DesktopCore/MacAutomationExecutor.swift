import Foundation

/// Executes only an already-authorized native request. The host broker owns the
/// permission decision; this object never issues grants or prompts for TCC.
@MainActor public final class MacAutomationExecutor {
    private struct Snapshot {
        let id: String
        let target: MacAutomationTarget
        let windowID: String
        let tree: MacAutomationTree
        let references: [String: String]
        let created: TimeInterval
        let epoch: Int
    }
    private let driver: any MacAutomationDriver
    private let now: () -> TimeInterval
    private var epoch = 0
    private var snapshots: [String: Snapshot] = [:]
    private var consumed: Set<String> = []
    private var cancelled: Set<String> = []
    private var busyOperation: String?
    public static let snapshotLifetime: TimeInterval = 120
    /// Set by the trusted host worker to recheck its exact central dispatch lease.
    /// Component tests may omit it; this executor is not an approval authority.
    public var beforeEffect: (@MainActor (String) async throws -> Void)?

    public convenience init(allowOwnTestWindow: Bool = false) {
        self.init(driver: MacAutomationAXDriver(allowOwnTestWindow: allowOwnTestWindow))
    }
    public init(driver: any MacAutomationDriver,
                now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.driver = driver; self.now = now
    }

    public func cancel(operationID: String) async {
        if cancelled.count < 8192 { cancelled.insert(operationID) }
        snapshots.removeAll()
    }

    public func reset() async {
        epoch += 1; snapshots.removeAll(); driver.reset()
        // Keep IDs of a pending command until its awaited native callback returns.
        if busyOperation == nil { consumed.removeAll(); cancelled.removeAll() }
    }

    public func execute(_ command: String, arguments: [String: RPCValue]) async throws -> RPCValue {
        if command == "desktop.status" {
            guard arguments.isEmpty else { throw RPCFailure.configuration }
            return driver.status()
        }
        guard let operationID = arguments["operation_id"]?.string,
              operationID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil
        else { throw RPCFailure.configuration }
        var dispatched = false
        do {
            guard busyOperation == nil else { throw MacAutomationFailure("busy") }
            guard consumed.count < 8192 else { throw MacAutomationFailure("operation_capacity") }
            guard consumed.insert(operationID).inserted else { throw MacAutomationFailure("operation_already_used") }
            let admissionEpoch = epoch
            try current(operationID, epoch: admissionEpoch)
            busyOperation = operationID
            defer { busyOperation = nil }
            try await authorize(operationID, epoch: admissionEpoch)
            switch command {
            case "desktop.resolve":
                try keys(arguments, allowed: ["operation_id", "bundle_id"], required: ["bundle_id"])
                guard let bundle = arguments["bundle_id"]?.string, !bundle.isEmpty,
                      bundle.utf8.count <= 256 else { throw MacAutomationFailure("invalid_target") }
                let targets = try driver.resolve(bundleID: bundle)
                guard targets.count <= 16 else { throw MacAutomationFailure("too_many_targets") }
                try current(operationID, epoch: admissionEpoch)
                return success(["targets": .array(targets.map(\.wire))])
            case "desktop.windows":
                try keys(arguments, allowed: ["operation_id", "target"], required: ["target"])
                let target = try MacAutomationTarget(arguments["target"]!)
                try driver.validate(target)
                let windows = try driver.windows(target)
                guard windows.count <= 32 else { throw MacAutomationFailure("too_many_windows") }
                try current(operationID, epoch: admissionEpoch)
                return success(["target": target.wire, "windows": .array(windows.map(\.wire))])
            case "desktop.observe":
                try keys(arguments, allowed: ["operation_id", "target", "window_ref", "include_frame"],
                         required: ["target", "window_ref"])
                let target = try MacAutomationTarget(arguments["target"]!)
                let window = try text(arguments, "window_ref", maximum: 64)
                guard arguments["include_frame"] == nil || arguments["include_frame"]?.bool != nil
                else { throw MacAutomationFailure("invalid_arguments") }
                try driver.validate(target)
                var tree = try checkedTree(driver.read(target, windowID: window))
                var frame: RPCValue?
                if arguments["include_frame"]?.bool == true {
                    guard !tree.truncated else { throw MacAutomationFailure("frame_requires_complete_accessibility") }
                    frame = try await driver.frame(target, windowID: window)
                    guard try JSONEncoder().encode(frame!).count <= 70_000 else {
                        throw MacAutomationFailure("frame_oversized")
                    }
                    try driver.validate(target)
                    tree = try checkedTree(driver.read(target, windowID: window))
                }
                try current(operationID, epoch: admissionEpoch)
                return observation(target, window: window, tree: tree, frame: frame)
            case "desktop.action":
                try keys(arguments, allowed: ["operation_id", "target", "window_ref", "snapshot_id", "ref", "action", "value", "point", "end_point", "key", "verify_ref", "expected_value"],
                         required: ["target", "window_ref", "snapshot_id", "ref", "action"])
                let target = try MacAutomationTarget(arguments["target"]!)
                let window = try text(arguments, "window_ref", maximum: 64)
                let snapshotID = try text(arguments, "snapshot_id", maximum: 64)
                let reference = try text(arguments, "ref", maximum: 64)
                let action = try text(arguments, "action", maximum: 32)
                let value = arguments["value"]?.string
                let point = try normalizedPoint(arguments["point"])
                let endPoint = try normalizedPoint(arguments["end_point"])
                let key = arguments["key"]?.string
                guard arguments["value"] == nil || value != nil,
                      (value?.utf8.count ?? 0) <= 8000 else { throw MacAutomationFailure("invalid_arguments") }
                guard let snapshot = snapshots.removeValue(forKey: snapshotID),
                      snapshot.target == target, snapshot.windowID == window,
                      snapshot.epoch == epoch, now() >= snapshot.created,
                      now() - snapshot.created <= Self.snapshotLifetime,
                      let nodeID = snapshot.references[reference],
                      let node = snapshot.tree.nodes.first(where: { $0.id == nodeID })
                else { throw MacAutomationFailure("stale_snapshot") }
                guard !node.secure else { throw MacAutomationFailure("secure_element") }
                guard node.enabled, node.actions.contains(action),
                      ["press", "focus", "set_value", "type_text", "scroll_up", "scroll_down", "scroll_left", "scroll_right", "click_at", "drag", "key"].contains(action)
                else { throw MacAutomationFailure("unsupported_action") }
                guard ["set_value", "type_text"].contains(action) ? value != nil : value == nil,
                      !(["set_value", "type_text"].contains(action) && node.valueTruncated)
                else { throw MacAutomationFailure("invalid_arguments") }
                guard (action == "click_at" || action == "drag") == (point != nil),
                      (action == "drag") == (endPoint != nil),
                      (action == "key") == (key != nil),
                      key == nil || MacAutomationAction.keys.contains(key!)
                else { throw MacAutomationFailure("invalid_arguments") }
                var verificationNode: String?
                let expectedResult = arguments["expected_value"]?.string
                if let verification = arguments["verify_ref"]?.string {
                    guard let expectedResult, expectedResult.utf8.count <= 8000,
                          let found = snapshot.references[verification],
                          let node = snapshot.tree.nodes.first(where: { $0.id == found }),
                          !node.secure, !node.valueTruncated, node.value != nil
                    else { throw MacAutomationFailure("invalid_verification") }
                    verificationNode = found
                } else if arguments["verify_ref"] != nil || arguments["expected_value"] != nil {
                    throw MacAutomationFailure("invalid_verification")
                }
                try driver.validate(target)
                let before = try checkedTree(driver.read(target, windowID: window))
                guard before.digest == snapshot.tree.digest else { throw MacAutomationFailure("stale_snapshot") }
                try current(operationID, epoch: admissionEpoch)
                // No await between the final cancellation/target guard and the effect.
                dispatched = true
                let effect = try await driver.perform(target, windowID: window, nodeID: nodeID,
                    action: MacAutomationAction(name: action, value: value, point: point,
                        endPoint: endPoint, key: key, expectedValue: node.value,
                        expectedSelection: node.selectedRange, verificationNodeID: verificationNode,
                        expectedResult: expectedResult, expectedBounds: snapshot.tree.bounds), beforeEffect: { [self] in
                            try await authorize(operationID, epoch: admissionEpoch); try driver.validate(target)
                        })
                do {
                    try current(operationID, epoch: admissionEpoch)
                    let after = try checkedTree(driver.read(target, windowID: window))
                    var result = observation(target, window: window, tree: after, frame: nil).object!
                    result["succeeded"] = .bool(effect.effectStatus == "known")
                    result["verified"] = .bool(effect.effectStatus == "known" && effect.verified)
                    result["verification"] = .string(effect.verification)
                    if let acknowledged = effect.nativeAcknowledged {
                        result["native_acknowledged"] = .bool(acknowledged)
                    }
                    result["effect_status"] = .string(effect.effectStatus)
                    result["dispatched"] = .bool(true)
                    return .object(result)
                } catch {
                    throw MacAutomationFailure("result_unconfirmed", dispatched: true)
                }
            case "desktop.forget":
                try keys(arguments, allowed: ["operation_id"], required: [])
                snapshots.removeAll(); driver.reset()
                return success(["forgotten": .bool(true)])
            default: throw MacAutomationFailure("unsupported_command")
            }
        } catch let error as MacAutomationFailure {
            return .object(["schema": .string("jarvis.desktop.v1"), "succeeded": .bool(false),
                "verified": .bool(false), "effect_status": .string(error.dispatched ? "unknown" : "known"),
                "dispatched": .bool(error.dispatched), "error_code": .string(error.code),
                "error": .string(error.localizedDescription), "permits_replay": .bool(false)])
        } catch {
            return .object(["schema": .string("jarvis.desktop.v1"), "succeeded": .bool(false),
                "verified": .bool(false),
                "dispatched": .bool(dispatched),
                "error_code": .string(dispatched ? "native_result_unconfirmed" : "native_observation_failed"),
                "effect_status": .string(dispatched ? "unknown" : "known"), "permits_replay": .bool(false)])
        }
    }

    private func current(_ operation: String, epoch expected: Int) throws {
        guard epoch == expected, !cancelled.contains(operation), !Task.isCancelled
        else { throw MacAutomationFailure("cancelled") }
    }
    private func authorize(_ operation: String, epoch expected: Int) async throws {
        try current(operation, epoch: expected)
        do { try await beforeEffect?(operation) }
        catch { throw MacAutomationFailure("authorization_unconfirmed") }
        try current(operation, epoch: expected)
    }
    private func checkedTree(_ tree: MacAutomationTree) throws -> MacAutomationTree {
        guard tree.nodes.count <= 160, Set(tree.nodes.map(\.id)).count == tree.nodes.count,
              tree.bounds.count == 4, tree.bounds.allSatisfy({ $0.isFinite && abs($0) <= 65_536 }),
              tree.bounds[2] > 0, tree.bounds[3] > 0,
              tree.nodes.allSatisfy({ !$0.id.isEmpty && $0.id.utf8.count <= 64 && $0.role.utf8.count <= 128
                  && $0.label.utf8.count <= 512 && ($0.value?.utf8.count ?? 0) <= 12_000
                  && (!$0.secure || ($0.value == nil && $0.actions.isEmpty)) })
        else { throw MacAutomationFailure("native_observation_oversized") }
        return tree
    }
    private func observation(_ target: MacAutomationTarget, window: String,
                             tree: MacAutomationTree, frame: RPCValue?) -> RPCValue {
        snapshots = snapshots.filter { now() - $0.value.created <= Self.snapshotLifetime && $0.value.epoch == epoch
            && !($0.value.target == target && $0.value.windowID == window) }
        if snapshots.count >= 16 { snapshots.removeAll() }
        let identity = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let refs = Dictionary(uniqueKeysWithValues: tree.nodes.map {
            (UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased(), $0.id)
        })
        let inverse = Dictionary(uniqueKeysWithValues: refs.map { ($0.value, $0.key) })
        var remaining = 12_000
        var nodes: [RPCValue] = [], visibleIDs: Set<String> = []
        var elementBudget = frame == nil ? 100_000 : 42_000
        for node in tree.nodes {
            var row = node.identity.object!
            row.removeValue(forKey: "id")
            row["ref"] = .string(inverse[node.id]!)
            row["parent"] = node.parentID.flatMap { inverse[$0] }.map(RPCValue.string) ?? .null
            var visible = String(decoding: (node.value ?? "").utf8.prefix(min(2048, max(0, remaining))), as: UTF8.self)
            while visible.utf8.count > min(2048, max(0, remaining)) { visible.removeLast() }
            remaining = max(0, remaining - visible.utf8.count)
            row["value"] = node.secure || node.value == nil ? .null : .string(visible)
            row["value_truncated"] = .bool(node.valueTruncated || visible != (node.value ?? ""))
            let value = RPCValue.object(row), size = ((try? JSONEncoder().encode(value).count) ?? 100_001) + 1
            guard size <= elementBudget else { break }
            elementBudget -= size; nodes.append(value); visibleIDs.insert(node.id)
        }
        snapshots[identity] = Snapshot(id: identity, target: target, windowID: window,
            tree: tree, references: refs.filter { visibleIDs.contains($0.value) }, created: now(), epoch: epoch)
        var payload: [String: RPCValue] = ["target": target.wire, "window_ref": .string(window),
            "snapshot_id": .string(identity), "expires_in_seconds": .number(Self.snapshotLifetime),
            "elements": .array(nodes), "truncated": .bool(tree.truncated || nodes.count < tree.nodes.count),
            "bounds": .array(tree.bounds.map(RPCValue.number)),
            "source": .string("native_accessibility_observation"), "permits_replay": .bool(false)]
        if let frame { payload["frame"] = frame }
        return success(payload)
    }
    private func success(_ payload: [String: RPCValue]) -> RPCValue {
        .object(payload.merging(["schema": .string("jarvis.desktop.v1"),
            "succeeded": .bool(true), "verified": .bool(true), "effect_status": .string("known"),
            "permits_replay": .bool(false), "dispatched": .bool(false)]) { _, rhs in rhs })
    }
    private func keys(_ arguments: [String: RPCValue], allowed: Set<String>, required: Set<String>) throws {
        guard Set(arguments.keys).isSubset(of: allowed), required.isSubset(of: arguments.keys)
        else { throw MacAutomationFailure("invalid_arguments") }
    }
    private func text(_ arguments: [String: RPCValue], _ key: String, maximum: Int) throws -> String {
        guard let value = arguments[key]?.string, !value.isEmpty, value.utf8.count <= maximum
        else { throw MacAutomationFailure("invalid_arguments") }; return value
    }
    private func normalizedPoint(_ value: RPCValue?) throws -> [Double]? {
        guard let value else { return nil }
        guard let values = value.array, values.count == 2 else { throw MacAutomationFailure("invalid_arguments") }
        let numbers: [Double] = try values.map {
            guard case .number(let number) = $0, number.isFinite, (0...1).contains(number)
            else { throw MacAutomationFailure("invalid_arguments") }; return number
        }
        return numbers
    }
}
