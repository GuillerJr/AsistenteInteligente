import AegisAudioCore
import CoreFoundation
import Foundation
import Observation

struct JarvisActionEntry: Identifiable, Equatable {
    let id: UUID
    let sequence: Int
    let timestamp: Date
    let toolName: String
    let state: String
    let conversationID: UUID?
    var executionCount: Int = 0
    var failureCount: Int = 0

    var toolTitle: String? {
        switch toolName {
        case "computer_use": String(localized: "Control del Mac", bundle: .module)
        case "web_research": String(localized: "Búsqueda en Internet", bundle: .module)
        case "web_fetch": String(localized: "Consulta de página web", bundle: .module)
        case "filesystem_read_text": String(localized: "Lectura de archivo", bundle: .module)
        case "system_describe_runtime", "system_power_status", "system_storage_status", "system_observe_status":
            String(localized: "Consulta del estado del Mac", bundle: .module)
        case "spotlight_search": String(localized: "Búsqueda con Spotlight", bundle: .module)
        case "mail_list_recent": String(localized: "Consulta de correo", bundle: .module)
        case "calendar_list_events": String(localized: "Consulta del calendario", bundle: .module)
        case "reminders_list": String(localized: "Consulta de recordatorios", bundle: .module)
        case "contacts_search": String(localized: "Búsqueda de contactos", bundle: .module)
        case "terminal_run_template", "network_discover_hosts", "mail_send_message", "calendar_create_event",
             "reminder_create", "reminder_complete", "contact_create", "system_audio_set", "media_control",
             "spotlight_open", "browser_open_url", "browser_search", "browser_play_media", "application_open", "shortcut_run":
            ActionReviewPresentation(toolName: toolName).title
        default:
            toolName.hasPrefix("browser_task_")
                ? String(localized: "Acción en el navegador", bundle: .module) : nil
        }
    }

    var stateLabel: String {
        switch state {
        case "authorized": String(localized: "Autorizada; esperando resultado", bundle: .module)
        case "awaiting_approval": String(localized: "Pendiente de tu aprobación", bundle: .module)
        case "denied": String(localized: "No autorizada", bundle: .module)
        case "reported_success": String(localized: "La herramienta informó éxito", bundle: .module)
        case "failed": String(localized: "La herramienta informó un fallo", bundle: .module)
        default: String(localized: "Sin resultado confirmado", bundle: .module)
        }
    }
}

struct JarvisActionHistoryPage {
    let revision: UUID
    let entries: [JarvisActionEntry]
    let nextBeforeSequence: Int?
    let retentionLimit: Int
    let recordingIncomplete: Bool

    init(_ data: Data) throws {
        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rawRevision = payload["revision"] as? String,
              let revision = UUID(uuidString: rawRevision),
              let rows = payload["entries"] as? [[String: Any]], rows.count <= 50,
              let retention = Self.integer(payload["retention_limit"]), (1...1_000).contains(retention),
              let incomplete = payload["recording_incomplete"] as? NSNumber,
              CFGetTypeID(incomplete) == CFBooleanGetTypeID(),
              payload.keys.contains("next_before_sequence") else { throw LocalIPCError.malformedResponse }
        self.revision = revision
        retentionLimit = retention
        recordingIncomplete = incomplete.boolValue
        entries = try rows.map { row in
            guard let rawID = row["id"] as? String, let id = UUID(uuidString: rawID),
                  let sequence = Self.integer(row["sequence"]), sequence > 0,
                  let stamp = row["timestamp"] as? String, let date = Self.date(stamp),
                  let tool = row["tool_name"] as? String,
                  tool.range(of: #"^[a-z][a-z0-9_-]{2,63}$"#, options: .regularExpression) != nil,
                  let state = row["state"] as? String,
                  ["authorized", "awaiting_approval", "denied", "reported_success", "failed", "unconfirmed"].contains(state),
                  let executions = Self.integer(row["execution_count"]), (0...1_000_000).contains(executions),
                  let failures = Self.integer(row["failure_count"]), (0...executions).contains(failures),
                  row.keys.contains("conversation_id") else { throw LocalIPCError.malformedResponse }
            let conversationID: UUID?
            if row["conversation_id"] is NSNull { conversationID = nil }
            else {
                guard let raw = row["conversation_id"] as? String,
                      let parsed = UUID(uuidString: raw) else { throw LocalIPCError.malformedResponse }
                conversationID = parsed
            }
            return JarvisActionEntry(id: id, sequence: sequence, timestamp: date,
                                     toolName: tool, state: state, conversationID: conversationID,
                                     executionCount: executions, failureCount: failures)
        }
        guard Set(entries.map(\.id)).count == entries.count,
              Set(entries.map(\.sequence)).count == entries.count,
              entries.map(\.sequence) == entries.map(\.sequence).sorted(by: >)
        else { throw LocalIPCError.malformedResponse }
        if payload["next_before_sequence"] is NSNull { nextBeforeSequence = nil }
        else {
            guard let next = Self.integer(payload["next_before_sequence"]), next > 0,
                  next == entries.last?.sequence else { throw LocalIPCError.malformedResponse }
            nextBeforeSequence = next
        }
    }

    static func integer(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue.rounded() == number.doubleValue,
              abs(number.doubleValue) <= 9_007_199_254_740_991 else { return nil }
        return number.intValue
    }

    private static func date(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }
}

enum JarvisActionHistoryError: Error {
    case changed, unavailable, invalid, deletionUnconfirmed

    var message: String {
        switch self {
        case .changed: String(localized: "El historial cambió. Actualiza antes de continuar.", bundle: .module)
        case .unavailable: String(localized: "No pude consultar el historial de acciones. Inténtalo de nuevo.", bundle: .module)
        case .invalid: String(localized: "No pude validar el historial de acciones. No se ha sustituido la lista anterior.", bundle: .module)
        case .deletionUnconfirmed: String(localized: "No pude confirmar el borrado. Actualiza el historial antes de volver a intentarlo.", bundle: .module)
        }
    }
}

@MainActor @Observable
final class JarvisActionHistoryStore {
    private(set) var entries: [JarvisActionEntry] = []
    private(set) var revision: UUID?
    private(set) var nextBeforeSequence: Int?
    private(set) var conversationID: UUID?
    private(set) var retentionLimit = 1_000
    private(set) var recordingIncomplete = false
    private(set) var loading = false
    private(set) var deleting = false
    private(set) var loaded = false
    private(set) var error: String?
    private var generation = UUID()
    private(set) var requiresRefresh = false

    init(previewEntries: [JarvisActionEntry]? = nil) {
        if let previewEntries {
            entries = previewEntries
            revision = UUID()
            loaded = true
        }
    }

    func invalidate() {
        generation = UUID()
        loading = false
    }

    func load(conversationID: UUID? = nil, append: Bool = false,
              fetch: @MainActor ([String: Any]) async throws -> Data) async {
        guard !deleting else { return }
        let cursor: Int?
        if append {
            guard !loading, !requiresRefresh, conversationID == self.conversationID,
                  let nextBeforeSequence, revision != nil else { return }
            cursor = nextBeforeSequence
        } else {
            generation = UUID()
            cursor = nil
            if conversationID != self.conversationID {
                entries = []; revision = nil; nextBeforeSequence = nil; loaded = false
            }
            self.conversationID = conversationID
        }
        let token = generation
        loading = true; error = nil
        defer { if token == generation { loading = false } }
        var payload: [String: Any] = ["limit": 30]
        if let conversationID { payload["conversation_id"] = conversationID.uuidString.lowercased() }
        if let cursor, let revision {
            payload["before_sequence"] = cursor
            payload["revision"] = revision.uuidString.lowercased()
        }
        do {
            let data = try await fetch(payload)
            guard token == generation, !Task.isCancelled else { return }
            let page = try JarvisActionHistoryPage(data)
            guard page.entries.allSatisfy({ conversationID == nil || $0.conversationID == conversationID }),
                  cursor == nil || (page.revision == revision && page.entries.allSatisfy { $0.sequence < cursor! })
            else { throw JarvisActionHistoryError.invalid }
            if append {
                let ids = Set(entries.map(\.id))
                guard page.entries.allSatisfy({ !ids.contains($0.id) }) else { throw JarvisActionHistoryError.invalid }
                entries += page.entries
            } else { entries = page.entries }
            revision = page.revision; nextBeforeSequence = page.nextBeforeSequence
            retentionLimit = page.retentionLimit; recordingIncomplete = page.recordingIncomplete
            loaded = true; requiresRefresh = false
        } catch {
            guard token == generation, !Task.isCancelled else { return }
            self.error = (error as? JarvisActionHistoryError ?? .invalid).message
            requiresRefresh = true
        }
    }

    /// Invoked only by the explicit confirmation button. No optimistic removal.
    func delete(entryID: UUID?, expectedRevision: UUID,
                execute: @MainActor (UUID, UUID?) async throws -> Data) async {
        guard !deleting, !requiresRefresh, let revision,
              entryID == nil || entries.contains(where: { $0.id == entryID }) else { return }
        guard revision == expectedRevision else {
            error = JarvisActionHistoryError.changed.message
            requiresRefresh = true
            return
        }
        invalidate()
        let token = generation
        deleting = true; error = nil
        defer { deleting = false }
        do {
            let data = try await execute(revision, entryID)
            guard token == generation, !Task.isCancelled,
                  let receipt = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let rawRevision = receipt["revision"] as? String,
                  let nextRevision = UUID(uuidString: rawRevision), nextRevision != revision,
                  let count = JarvisActionHistoryPage.integer(receipt["deleted"]), count >= 0,
                  receipt.keys.contains("entry_id") else { throw JarvisActionHistoryError.deletionUnconfirmed }
            if let entryID {
                guard count == 1, let rawID = receipt["entry_id"] as? String,
                      UUID(uuidString: rawID) == entryID else { throw JarvisActionHistoryError.deletionUnconfirmed }
                entries.removeAll { $0.id == entryID }
            } else {
                guard receipt["entry_id"] is NSNull else { throw JarvisActionHistoryError.deletionUnconfirmed }
                entries = []
            }
            self.revision = nextRevision; nextBeforeSequence = nil
            // A fresh page is required to continue paginating from a new snapshot.
            requiresRefresh = true
        } catch {
            guard token == generation else { return }
            self.error = (error as? JarvisActionHistoryError ?? .deletionUnconfirmed).message
            requiresRefresh = true
        }
    }
}
