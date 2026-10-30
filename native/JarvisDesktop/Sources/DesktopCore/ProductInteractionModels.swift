import Foundation

public enum ProductInteractionValidation {
    public static func identifier(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]{1,128}\\z", options: .regularExpression) != nil
    }
    static func text(_ value: RPCValue, limit: Int, empty: Bool = false) throws -> String {
        guard let text = value.string, ManagementValidation.text(text, limit: limit, empty: empty) else {
            throw RPCFailure.invalidResponse
        }
        return text
    }
    static func identity(_ value: RPCValue) throws -> String {
        let value = try text(value, limit: 128)
        guard identifier(value) else { throw RPCFailure.invalidResponse }; return value
    }
    static func revision(_ value: RPCValue) throws -> Int {
        guard let value = value.integer, value >= 0, value < 4_503_599_627_370_496 else {
            throw RPCFailure.invalidResponse
        }
        return value
    }
    static func bounded(_ value: RPCValue) throws {
        var nodes = 0, bytes = 0
        func walk(_ value: RPCValue, depth: Int) throws {
            nodes += 1
            guard depth <= 16, nodes <= 30_000 else { throw RPCFailure.oversized }
            switch value {
            case .string(let text): bytes += text.utf8.count
            case .array(let rows): for row in rows { try walk(row, depth: depth + 1) }
            case .object(let rows):
                for (key, child) in rows { bytes += key.utf8.count; try walk(child, depth: depth + 1) }
            case .number(let number): guard number.isFinite else { throw RPCFailure.invalidResponse }
            case .bool, .null: break
            }
            guard bytes <= 1_048_576 else { throw RPCFailure.oversized }
        }
        try walk(value, depth: 0)
    }
}

public struct ProductInteractionBinding: Equatable, Sendable {
    public let scope: SessionScope
    public let runID: String
    public let agentID: String
    public let workspaceID: String
    public let generation: Int
    public init(_ value: RPCValue, expectedScope: SessionScope, expectedRunID: String? = nil) throws {
        guard value["session_id"].string == expectedScope.sessionID,
              value["temporary"].bool == expectedScope.temporary else { throw RPCFailure.invalidResponse }
        scope = expectedScope
        runID = try ProductInteractionValidation.identity(value["run_id"])
        if let expectedRunID, runID != expectedRunID { throw RPCFailure.invalidResponse }
        agentID = try ProductInteractionValidation.identity(value["agent_id"])
        workspaceID = try ProductInteractionValidation.identity(value["workspace_id"])
        generation = try ProductInteractionValidation.revision(value["generation"])
    }
}

public struct ProductQuestionOption: Identifiable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let description: String
    init(_ value: RPCValue) throws {
        id = try ProductInteractionValidation.identity(value["id"])
        guard id != "freeform" else { throw RPCFailure.invalidResponse }
        label = try ProductInteractionValidation.text(value["label"], limit: 160)
        description = try ProductInteractionValidation.text(value["description"], limit: 512)
    }
}

public struct ProductQuestionPrompt: Identifiable, Equatable, Sendable {
    public let id: String
    public let header: String
    public let prompt: String
    public let options: [ProductQuestionOption]
    init(_ value: RPCValue) throws {
        id = try ProductInteractionValidation.identity(value["id"])
        header = try ProductInteractionValidation.text(value["header"], limit: 48)
        prompt = try ProductInteractionValidation.text(value["prompt"], limit: 2048)
        options = try ManagementValidation.rows(value["options"], limit: 3, parse: ProductQuestionOption.init)
        guard options.count >= 2, Set(options.map(\.id)).count == options.count,
              Set(options.map { $0.label.lowercased().trimmingCharacters(in: .whitespacesAndNewlines) }).count == options.count,
              value["allow_freeform"].bool == true,
              value["sensitive"] == .null || value["sensitive"].bool == false else {
            throw RPCFailure.invalidResponse
        }
    }
}

public enum ProductQuestionAnswer: Equatable, Sendable {
    case option(String), text(String)
    public var value: RPCValue {
        switch self {
        case .option(let id): return .object(["option_id": .string(id)])
        case .text(let text): return .object(["text": .string(text)])
        }
    }
    public func valid(for prompt: ProductQuestionPrompt) -> Bool {
        switch self {
        case .option(let id): return prompt.options.contains(where: { $0.id == id })
        case .text(let text): return ManagementValidation.text(text, limit: 4096)
        }
    }
}

public struct ProductSuggestion: Identifiable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let text: String
    init(_ value: RPCValue) throws {
        id = try ProductInteractionValidation.identity(value["id"])
        label = try ProductInteractionValidation.text(value["label"], limit: 160)
        text = try ProductInteractionValidation.text(value["text"], limit: 2048)
    }
}

public struct ProductWidgetItem: Identifiable, Equatable, Sendable {
    public let id: String
    public let index: Int
    public var text: String
    public var checked: Bool
    public init(id: String, index: Int, text: String, checked: Bool = false) {
        self.id = id; self.index = index; self.text = text; self.checked = checked
    }
}

public struct ProductWidgetSource: Equatable, Sendable {
    public let id: String
    public let type: String
    public let label: String
    public let text: String
    init(_ value: RPCValue) throws {
        id = try ProductInteractionValidation.identity(value["id"])
        type = try ProductInteractionValidation.text(value["type"], limit: 16)
        guard ["sessions", "runs", "goals", "usage", "inbox", "projects"].contains(type) else {
            throw RPCFailure.invalidResponse
        }
        label = try ProductInteractionValidation.text(value["label"], limit: 256)
        text = try ProductInteractionValidation.text(value["text"], limit: 4096, empty: true)
    }
}

public struct ProductCanvasWidget: Identifiable, Equatable, Sendable {
    public static let types = ["text", "stat", "list", "checklist", "progress", "notice"]
    public static let symbols = ["text.alignleft", "list.bullet", "checkmark.circle", "chart.bar",
        "clock", "info.circle", "exclamationmark.triangle", "sparkles", "square.grid.2x2",
        "doc.text", "questionmark.circle", "bell", "circle"]
    public static let colors = ["primary", "secondary", "accent", "success", "warning", "error"]
    public let id: String
    public var type: String
    public var title: String
    public var symbol: String
    public var color: String
    public var text: String?
    public var value: String?
    public var progress: Double?
    public var items: [ProductWidgetItem]
    public var sourceID: String?
    public var source: ProductWidgetSource?
    public init(id: String, type: String, title: String, text: String? = nil,
                value: String? = nil, progress: Double? = nil, items: [ProductWidgetItem] = []) {
        self.id = id; self.type = type; self.title = title; self.text = text; self.value = value
        self.progress = progress; self.items = items; symbol = "circle"; color = "primary"
        sourceID = nil; source = nil
    }
    init(_ row: RPCValue) throws {
        guard let object = row.object, Set(object.keys).isSubset(of: ["id", "type", "title", "symbol",
            "color", "text", "value", "items", "source_id", "source"]) else { throw RPCFailure.invalidResponse }
        id = try ProductInteractionValidation.identity(row["id"])
        type = try ProductInteractionValidation.text(row["type"], limit: 16)
        title = try ProductInteractionValidation.text(row["title"], limit: 256)
        symbol = row["symbol"].string ?? "circle"; color = row["color"].string ?? "primary"
        guard Self.types.contains(type), Self.symbols.contains(symbol), Self.colors.contains(color) else {
            throw RPCFailure.invalidResponse
        }
        text = row["text"] == .null ? nil : try ProductInteractionValidation.text(row["text"], limit: 4096, empty: true)
        value = nil; progress = nil; items = []; sourceID = nil; source = nil
        if row["value"] != .null {
            if type == "progress", case .number(let number) = row["value"], number.isFinite, (0...100).contains(number) {
                progress = number
            } else if type != "progress" {
                value = try ProductInteractionValidation.text(row["value"], limit: 256, empty: true)
            } else { throw RPCFailure.invalidResponse }
        }
        if row["items"] != .null {
            guard ["list", "checklist"].contains(type), let rows = row["items"].array, rows.count <= 50 else {
                throw RPCFailure.invalidResponse
            }
            items = try rows.enumerated().map { index, item in
                let text: String, checked: Bool
                if type == "list" { text = try ProductInteractionValidation.text(item, limit: 512); checked = false }
                else {
                    text = try ProductInteractionValidation.text(item["text"], limit: 512)
                    guard let flag = item["checked"].bool else { throw RPCFailure.invalidResponse }; checked = flag
                }
                return ProductWidgetItem(id: "\(id)-\(index)", index: index, text: text, checked: checked)
            }
        }
        if row["source_id"] != .null { sourceID = try ProductInteractionValidation.identity(row["source_id"]) }
        if row["source"] != .null {
            source = try ProductWidgetSource(row["source"])
            guard source?.id == sourceID else { throw RPCFailure.invalidResponse }
        }
    }
    public var submission: RPCValue {
        var row: [String: RPCValue] = ["id": .string(id), "type": .string(type), "title": .string(title),
                                      "symbol": .string(symbol), "color": .string(color)]
        if let text { row["text"] = .string(text) }
        if let value { row["value"] = .string(value) }
        if let progress { row["value"] = .number(progress) }
        if ["list", "checklist"].contains(type) {
            row["items"] = .array(items.map { item in type == "list" ? .string(item.text)
                : .object(["text": .string(item.text), "checked": .bool(item.checked)]) })
        }
        if let sourceID { row["source_id"] = .string(sourceID) }
        return .object(row)
    }
}

public struct ProductCanvasDefinition: Equatable, Sendable {
    public var title: String
    public var layout: String
    public var appearance: String
    public var accent: String
    public var widgets: [ProductCanvasWidget]
    public init(title: String = "Mi panel", layout: String = "stack", appearance: String = "system",
                accent: String = "accent", widgets: [ProductCanvasWidget] = []) {
        self.title = title; self.layout = layout; self.appearance = appearance
        self.accent = accent; self.widgets = widgets
    }
    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys).isSubset(of: ["title", "layout", "theme", "widgets", "rendering"]),
              value["rendering"].string == nil || value["rendering"].string == "native_declarative" else {
            throw RPCFailure.invalidResponse
        }
        title = try ProductInteractionValidation.text(value["title"], limit: 256)
        layout = try ProductInteractionValidation.text(value["layout"], limit: 16)
        appearance = try ProductInteractionValidation.text(value["theme"]["appearance"], limit: 16)
        accent = try ProductInteractionValidation.text(value["theme"]["accent"], limit: 16)
        guard ["stack", "grid", "columns"].contains(layout),
              ["system", "light", "dark", "high_contrast"].contains(appearance),
              ProductCanvasWidget.colors.contains(accent),
              value["theme"].object?.count == 2 else { throw RPCFailure.invalidResponse }
        widgets = try ManagementValidation.rows(value["widgets"], limit: 24, parse: ProductCanvasWidget.init)
        guard Set(widgets.map(\.id)).count == widgets.count else { throw RPCFailure.invalidResponse }
    }
    public var submission: RPCValue {
        .object(["title": .string(title), "layout": .string(layout),
                 "theme": .object(["appearance": .string(appearance), "accent": .string(accent)]),
                 "widgets": .array(widgets.map(\.submission))])
    }
}

public struct ProductInteractionRecord: Identifiable, Equatable, Sendable {
    public let binding: ProductInteractionBinding
    public let id: String
    public let kind: String
    public let revision: Int
    public let status: String
    public let scopeRunID: String
    public let expiresAt: Date?
    public let questions: [ProductQuestionPrompt]
    public let suggestions: [ProductSuggestion]
    public let panel: ProductCanvasDefinition?
    public let label: String?
    public let progress: Double?
    public let presence: String?
    public let attentionKind: String?
    public let correlationID: String?
    public var isCurrent: Bool { expiresAt.map { $0 > Date() } ?? true }
    public var mayAnswer: Bool { kind == "question" && status == "pending" && isCurrent }

    public init(_ row: RPCValue, binding: ProductInteractionBinding) throws {
        self.binding = binding
        id = try ProductInteractionValidation.identity(row["id"])
        kind = try ProductInteractionValidation.text(row["kind"], limit: 32)
        status = try ProductInteractionValidation.text(row["status"], limit: 32)
        guard ["question", "progress", "presence", "suggestions", "panel", "notification", "scope_revocation"].contains(kind),
              ["pending", "answered", "consumed", "active", "running", "waiting", "completed", "failed",
               "cancelled", "interrupted", "unknown", "queued", "dispatching", "delivered", "resumed", "revoked", "expired"].contains(status),
              case .number(let expires) = row["expires_ns"], expires.isFinite, expires >= 0, expires < 9_223_372_036_854_775_808 else {
            throw RPCFailure.invalidResponse
        }
        revision = try ProductInteractionValidation.revision(row["revision"])
        guard revision > 0 else { throw RPCFailure.invalidResponse }
        scopeRunID = row["scope_run_id"] == .null ? binding.runID : try ProductInteractionValidation.identity(row["scope_run_id"])
        expiresAt = expires == 0 ? nil : Date(timeIntervalSince1970: expires / 1_000_000_000)
        let payload = row["payload"]
        guard payload.object != nil else { throw RPCFailure.invalidResponse }
        questions = kind == "question" ? try ManagementValidation.rows(payload["questions"], limit: 3, parse: ProductQuestionPrompt.init) : []
        if kind == "question", questions.isEmpty || Set(questions.map(\.id)).count != questions.count
            || payload["grants_tool_approval"].bool != false { throw RPCFailure.invalidResponse }
        suggestions = kind == "suggestions" ? try ManagementValidation.rows(payload["suggestions"], limit: 3, parse: ProductSuggestion.init) : []
        if kind == "suggestions", payload["execution"].string != "draft_only"
            || Set(suggestions.map(\.id)).count != suggestions.count { throw RPCFailure.invalidResponse }
        panel = kind == "panel" ? try ProductCanvasDefinition(payload) : nil
        label = kind == "progress" ? try ProductInteractionValidation.text(payload["label"], limit: 512) : nil
        var observedProgress: Double?
        if kind == "progress", payload["value"] != .null {
            guard case .number(let value) = payload["value"], value.isFinite, (0...100).contains(value) else { throw RPCFailure.invalidResponse }
            observedProgress = value
        }
        progress = observedProgress
        presence = kind == "presence" ? try ProductInteractionValidation.text(payload["state"], limit: 16) : nil
        if let presence, !["working", "waiting_user", "idle"].contains(presence) { throw RPCFailure.invalidResponse }
        attentionKind = kind == "notification" ? try ProductInteractionValidation.text(payload["attention_kind"], limit: 32) : nil
        correlationID = kind == "notification" ? try ProductInteractionValidation.identity(payload["correlation_id"]) : nil
        if let attentionKind, !["question", "run_completed", "run_failed", "resume_needed"].contains(attentionKind) { throw RPCFailure.invalidResponse }
    }
}

public struct ProductNotificationPreferences: Equatable, Sendable {
    public let revision: Int
    public let enabled: Bool
    public let silenced: Bool
    init(_ value: RPCValue) throws {
        revision = try ProductInteractionValidation.revision(value["revision"])
        guard let enabled = value["enabled"].bool, let silenced = value["silenced"].bool else { throw RPCFailure.invalidResponse }
        self.enabled = enabled; self.silenced = silenced
    }
}

public struct ProductInteractionSnapshot: Equatable, Sendable {
    public let binding: ProductInteractionBinding
    public let interactions: [ProductInteractionRecord]
    public let notificationPreferences: ProductNotificationPreferences
    public init(_ value: RPCValue, expectedScope: SessionScope, expectedRunID: String? = nil) throws {
        try ProductInteractionValidation.bounded(value)
        binding = try ProductInteractionBinding(value["scope"], expectedScope: expectedScope, expectedRunID: expectedRunID)
        let binding = self.binding
        interactions = try ManagementValidation.rows(value["interactions"], limit: 512) { try ProductInteractionRecord($0, binding: binding) }
        guard Set(interactions.map(\.id)).count == interactions.count else { throw RPCFailure.invalidResponse }
        notificationPreferences = try ProductNotificationPreferences(value["notification_preferences"])
    }
}

public struct ProductNotificationClaim: Equatable, Sendable {
    public let scope: SessionScope
    public let runID: String
    public let id: String
    public let revision: Int
    public let claim: String
    public let title: String
    public let body: String
    init(_ value: RPCValue, scope: SessionScope, runID: String, id: String) throws {
        guard value["session_id"].string == scope.sessionID, value["run_id"].string == runID,
              value["id"].string == id else { throw RPCFailure.invalidResponse }
        self.scope = scope; self.runID = runID; self.id = id
        revision = try ProductInteractionValidation.revision(value["revision"])
        claim = try ProductInteractionValidation.identity(value["claim"])
        title = try ProductInteractionValidation.text(value["title"], limit: 256)
        body = try ProductInteractionValidation.text(value["body"], limit: 512)
    }
}

public struct ProductNotificationNavigation: Equatable, Sendable {
    public let scope: SessionScope
    public let runID: String
    public let questionID: String?
    init(_ value: RPCValue, scope: SessionScope, runID: String) throws {
        guard value["session_id"].string == scope.sessionID, value["run_id"].string == runID,
              value["navigation_only"].bool == true, value["grants_tool_approval"].bool == false else {
            throw RPCFailure.invalidResponse
        }
        self.scope = scope; self.runID = runID
        questionID = value["question_id"] == .null ? nil : try ProductInteractionValidation.identity(value["question_id"])
    }
}
