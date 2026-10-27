import Foundation
import CryptoKit

public struct MacAutomationTarget: Equatable, Sendable {
    public let bundleID: String
    public let pid: Int32
    public let launchID: String

    public init(bundleID: String, pid: Int32, launchID: String) {
        self.bundleID = bundleID; self.pid = pid; self.launchID = launchID
    }

    public init(_ value: RPCValue) throws {
        guard let row = value.object, Set(row.keys) == ["bundle_id", "pid", "launch_id"],
              let bundle = row["bundle_id"]?.string, !bundle.isEmpty, bundle.utf8.count <= 256,
              let process = row["pid"]?.integer, process > 0, process <= Int(Int32.max),
              let launch = row["launch_id"]?.string, Self.isDigest(launch)
        else { throw MacAutomationFailure("invalid_target") }
        self.init(bundleID: bundle, pid: Int32(process), launchID: launch)
    }

    public var wire: RPCValue { .object(["bundle_id": .string(bundleID),
        "pid": .number(Double(pid)), "launch_id": .string(launchID)]) }

    public static func isDigest(_ text: String) -> Bool {
        text.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
    }
}

public struct MacAutomationWindow: Equatable, Sendable {
    public let id: String
    public let title: String
    public init(id: String, title: String) { self.id = id; self.title = title }
    public var wire: RPCValue { .object(["window_ref": .string(id), "title": .string(title)]) }
}

public struct MacAutomationNode: Equatable, Sendable {
    public let id: String
    public let parentID: String?
    public let role: String
    public let label: String
    public let value: String?
    public let secure: Bool
    public let enabled: Bool
    public let valueTruncated: Bool
    public let actions: [String]
    public let selectedRange: [Int]?
    public init(id: String, parentID: String? = nil, role: String, label: String = "",
                value: String? = nil, secure: Bool = false, enabled: Bool = true,
                valueTruncated: Bool = false, actions: [String] = [], selectedRange: [Int]? = nil) {
        self.id = id; self.parentID = parentID; self.role = role; self.label = secure ? "Campo protegido" : label
        self.value = secure ? nil : value; self.secure = secure; self.enabled = enabled
        self.valueTruncated = valueTruncated; self.actions = secure ? [] : actions
        self.selectedRange = secure ? nil : selectedRange
    }
    var identity: RPCValue {
        .object(["id": .string(id), "parent": parentID.map(RPCValue.string) ?? .null,
            "role": .string(role), "label": .string(label),
            "value": value.map(RPCValue.string) ?? .null,
            "secure": .bool(secure), "enabled": .bool(enabled),
            "value_truncated": .bool(valueTruncated), "actions": .array(actions.map(RPCValue.string)),
            "selected_range": selectedRange.map { .array($0.map { .number(Double($0)) }) } ?? .null])
    }
}

public struct MacAutomationTree: Equatable, Sendable {
    public let nodes: [MacAutomationNode]
    public let truncated: Bool
    public let bounds: [Double]
    public init(nodes: [MacAutomationNode], truncated: Bool = false,
                bounds: [Double] = [0, 0, 640, 480]) {
        self.nodes = nodes; self.truncated = truncated; self.bounds = bounds
    }
    var digest: String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let bytes = (try? encoder.encode(RPCValue.object([
            "nodes": .array(semanticCASNodes.map(\.identity)), "bounds": .array(bounds.map(RPCValue.number))]))) ?? Data()
        return SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
    }

    /// AppKit may grow a chain of empty chrome groups between AX reads. Those
    /// groups carry no observable meaning or possible action. Keep them in the
    /// observation/ref inventory, but omit only their wholly empty subtrees
    /// from semantic CAS. Every retained control still has its exact AX ID.
    private var semanticCASNodes: [MacAutomationNode] {
        guard nodes.count <= 160, Set(nodes.map(\.id)).count == nodes.count else { return nodes }
        let ids = Set(nodes.map(\.id))
        let parents = Dictionary(uniqueKeysWithValues: nodes.compactMap { node in
            node.parentID.map { (node.id, $0) }
        })
        for node in nodes {
            var seen: Set<String> = [node.id]
            var parent = node.parentID
            while let current = parent {
                guard ids.contains(current), seen.insert(current).inserted else { return nodes }
                parent = parents[current]
            }
        }
        let children = Dictionary(grouping: nodes.filter { $0.parentID != nil }, by: { $0.parentID! })
        var removed: Set<String> = []
        for _ in 0..<nodes.count {
            var changed = false
            for node in nodes where !removed.contains(node.id) {
                guard node.role == "AXGroup", node.label.isEmpty, node.value == nil,
                      node.actions.isEmpty, !node.secure, node.enabled, node.selectedRange == nil,
                      !node.valueTruncated,
                      (children[node.id] ?? []).allSatisfy({ removed.contains($0.id) }) else { continue }
                removed.insert(node.id); changed = true
            }
            if !changed { break }
        }
        return nodes.filter { !removed.contains($0.id) }
    }
}

public struct MacAutomationAction: Equatable, Sendable {
    public let name: String
    public let value: String?
    public let point: [Double]?
    public let endPoint: [Double]?
    public let key: String?
    public let expectedValue: String?
    public let expectedSelection: [Int]?
    public let verificationNodeID: String?
    public let expectedResult: String?
    public let expectedBounds: [Double]?
    public init(name: String, value: String? = nil, point: [Double]? = nil,
                endPoint: [Double]? = nil, key: String? = nil,
                expectedValue: String? = nil, expectedSelection: [Int]? = nil,
                verificationNodeID: String? = nil, expectedResult: String? = nil,
                expectedBounds: [Double]? = nil) {
        self.name = name; self.value = value; self.point = point; self.endPoint = endPoint; self.key = key
        self.expectedValue = expectedValue; self.expectedSelection = expectedSelection
        self.verificationNodeID = verificationNodeID; self.expectedResult = expectedResult
        self.expectedBounds = expectedBounds
    }
    public static let keys: Set<String> = ["return", "tab", "escape", "space", "backspace",
        "delete", "left", "right", "up", "down", "home", "end", "page_up", "page_down",
        "cmd+a", "cmd+z", "cmd+shift+z", "cmd+s", "cmd+f"]
}

public struct MacAutomationEffect: Equatable, Sendable {
    public let verified: Bool
    public let verification: String
    public let effectStatus: String
    public let nativeAcknowledged: Bool?
    public init(verified: Bool, verification: String, effectStatus: String = "known",
                nativeAcknowledged: Bool? = nil) {
        self.verified = verified; self.verification = verification; self.effectStatus = effectStatus
        self.nativeAcknowledged = nativeAcknowledged
    }
}

public struct MacAutomationFailure: Error, LocalizedError, Equatable, Sendable {
    public let code: String
    public let dispatched: Bool
    public init(_ code: String, dispatched: Bool = false) {
        self.code = code; self.dispatched = dispatched
    }
    public var errorDescription: String? {
        switch code {
        case "accessibility_permission_required": return "Concede tú el permiso de Accesibilidad a esta app y vuelve a observar la ventana."
        case "screen_permission_required": return "Concede tú el permiso de grabación de pantalla para observar los píxeles de esta ventana."
        case "stale_snapshot": return "La ventana cambió o la observación caducó. Observa de nuevo antes de actuar."
        case "target_changed": return "La identidad de la aplicación o de la ventana cambió. La acción no se trasladó a otro destino."
        case "cancelled": return "La operación fue cancelada; no se repetirá automáticamente."
        case "operation_already_used": return "La operación ya fue recibida; no se ejecutará de nuevo."
        case "secure_element": return "Los campos protegidos no se leen ni se modifican mediante esta herramienta."
        case "owner_only_target": return "Los permisos del sistema, contraseñas y llaveros debe gestionarlos directamente el dueño."
        case "unsupported_action": return "El control no admite esa acción de accesibilidad."
        case "frame_ambiguous": return "No se identificó una única ventana para observar sus píxeles."
        case "frame_oversized": return "La imagen de observación supera el límite admitido."
        case "busy": return "Espera a que termine la operación anterior del escritorio."
        default: return "No se pudo completar la operación del escritorio (\(code))."
        }
    }
}

@MainActor public protocol MacAutomationDriver: AnyObject {
    func status() -> RPCValue
    func resolve(bundleID: String) throws -> [MacAutomationTarget]
    func validate(_ target: MacAutomationTarget) throws
    func windows(_ target: MacAutomationTarget) throws -> [MacAutomationWindow]
    func read(_ target: MacAutomationTarget, windowID: String) throws -> MacAutomationTree
    func perform(_ target: MacAutomationTarget, windowID: String, nodeID: String,
                 action: MacAutomationAction, beforeEffect: @MainActor () async throws -> Void) async throws -> MacAutomationEffect
    func frame(_ target: MacAutomationTarget, windowID: String) async throws -> RPCValue
    func reset()
}
