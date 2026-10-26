import Foundation
import Observation

/// Presentation metadata only. No grant, deadline or operation is created here.
public struct OwnerDecisionNotice: Identifiable, Sendable, Equatable {
    public let id: String
    public let tab: String
    public let phase: String
    public let expiresAt: Date?
    public let errorCode: String?
    public let resourcesPending: Bool
    public let unconfirmed: Bool
    public var tabTitle: String {
        ["maintenance":"Mantenimiento", "recovery":"Recuperación", "memory-restored":"Memoria conservada", "restored":"Perfil restaurado", "channels-restored":"Canales conservados"][tab] ?? "Ajustes"
    }
    public init?(tab: String, operation: MaintenanceOperation?, reviewID: String?, expiresAt: Date?, unconfirmed: Bool = false) {
        guard ["maintenance", "recovery", "memory-restored", "restored", "channels-restored"].contains(tab), let reviewID else { return nil }
        if let operation, operation.reviewID == reviewID {
            id = operation.id; phase = operation.phase; errorCode = operation.errorCode; resourcesPending = operation.resourcesPending
        } else {
            guard unconfirmed else { return nil }; id = reviewID; phase = "response_unconfirmed"; errorCode = nil; resourcesPending = true
        }
        self.tab = tab; self.expiresAt = expiresAt; self.unconfirmed = unconfirmed
    }
    public var requiresAttention: Bool {
        unconfirmed || ["awaiting_owner", "failed", "denied", "cancelled", "publication_unknown", "cleanup_pending"].contains(phase)
    }
    public var awaitingDecision: Bool { phase == "awaiting_owner" && !unconfirmed }
    public func expired(at now: Date) -> Bool {
        errorCode?.hasSuffix("review_expired") == true || (awaitingDecision && expiresAt.map { $0 <= now } == true)
    }
    public func title(at now: Date) -> String {
        if expired(at: now) { return "La decisión de \(tabTitle) caducó" }
        if unconfirmed || phase == "publication_unknown" { return "Resultado de \(tabTitle) sin confirmar" }
        switch phase {
        case "awaiting_owner": return "Hay una decisión pendiente en \(tabTitle)"
        case "denied": return "La operación de \(tabTitle) no fue autorizada"
        case "cancelled": return "La operación de \(tabTitle) se canceló"
        case "failed": return "La operación de \(tabTitle) no se completó"
        case "cleanup_pending": return "\(tabTitle) conserva recursos pendientes"
        default: return "Estado de la decisión de \(tabTitle)"
        }
    }
    public func message(at now: Date) -> String {
        if expired(at: now) { return "Esta revisión ya no permite autorizar. Consulta el estado y cancela la espera si continúa activa; después prepara una revisión nueva. No se repetirá la solicitud anterior." }
        if unconfirmed || phase == "publication_unknown" { return "No se confirmó el resultado. La solicitud anterior no se reenviará y cerrar este aviso no acredita que se deshiciera." }
        switch phase {
        case "awaiting_owner": return "La tarjeta permanece en Ajustes. Pulsa «Ver decisión» para volver a su pestaña; cambiar de pestaña o cerrar la ventana no autoriza ni cancela nada."
        case "denied": return "No se concedió permiso y no se volverá a solicitar automáticamente."
        case "cancelled": return "La solicitud se canceló. Comprueba el recibo y completa cualquier limpieza pendiente antes de continuar. No se repetirá automáticamente."
        case "failed": return "No se confirmó una operación completada. Revisa el diagnóstico y el recibo; no se reenviará automáticamente."
        case "cleanup_pending": return "Completa sólo la limpieza propia. Eso no repite el efecto ni cambia un recibo incierto en éxito."
        default: return "Consulta el recibo de esta operación para conocer su resultado."
        }
    }
}

@MainActor @Observable public final class SettingsOwnerNavigation {
    public private(set) var selectedTab = "general"
    public private(set) var focusID: String?
    public private(set) var revealSequence = 0
    private var runtimeID: UUID?
    private var dismissed = Set<String>()
    private var seen = Set<String>()
    private var firstObserved: [String: Int] = [:]
    private var observationSequence = 0
    public init() {}
    public func configure(runtimeID: UUID) {
        guard self.runtimeID != runtimeID else { return }
        self.runtimeID = runtimeID; selectedTab = "general"; focusID = nil; dismissed = []; seen = []; firstObserved = [:]; observationSequence = 0; revealSequence += 1
    }
    public func select(_ tab: String) {
        guard ["general", "maintenance", "recovery", "restored", "memory-restored", "channels-restored"].contains(tab) else { return }; selectedTab = tab
    }
    public func attention(in notices: [OwnerDecisionNotice]) -> OwnerDecisionNotice? {
        let rows = notices.filter { $0.requiresAttention && !dismissed.contains($0.id) }
        func live(_ notice: OwnerDecisionNotice) -> Bool {
            notice.awaitingDecision || notice.unconfirmed || notice.resourcesPending
        }
        // Array order reflects panel layout, not when the owner initiated an
        // operation. Keep observed operation order through its terminal result.
        // Live custody always takes precedence over a newer terminal notice.
        return rows.reduce(nil as OwnerDecisionNotice?) { selected, candidate in
            guard let selected else { return candidate }
            if live(candidate) != live(selected) { return live(candidate) ? candidate : selected }
            return (firstObserved[candidate.id] ?? 0) > (firstObserved[selected.id] ?? 0) ? candidate : selected
        }
    }
    public func reveal(_ notice: OwnerDecisionNotice) {
        selectedTab = notice.tab; focusID = notice.id; revealSequence += 1
    }
    public func detect(_ notices: [OwnerDecisionNotice], visible: Bool) {
        for notice in notices where notice.requiresAttention && firstObserved[notice.id] == nil {
            observationSequence += 1; firstObserved[notice.id] = observationSequence
        }
        for notice in notices where notice.awaitingDecision && !seen.contains(notice.id) {
            seen.insert(notice.id)
            // Scroll the owning pane once. Never pull a user away from a
            // different tab merely because another identical poll arrived.
            if visible && selectedTab == notice.tab { reveal(notice) }
        }
        if seen.count > 128 { seen = Set(notices.map(\.id)) }
        if firstObserved.count > 128 {
            let retained = Set(notices.map(\.id))
            firstObserved = firstObserved.filter { retained.contains($0.key) }
        }
    }
    public func dismiss(_ notice: OwnerDecisionNotice) {
        guard !notice.resourcesPending, !notice.awaitingDecision, !notice.unconfirmed else { return }
        dismissed.insert(notice.id); if dismissed.count > 128 { dismissed = [notice.id] }
    }
}

public enum OwnerDecisionTicketPolicy {
    public static func same(_ lhs: MaintenanceApproval?, _ rhs: MaintenanceApproval?) -> Bool {
        if lhs == nil && rhs == nil { return true }
        guard let lhs, let rhs else { return false }
        return lhs.id == rhs.id && lhs.digest == rhs.digest && lhs.operationID == rhs.operationID && lhs.purpose == rhs.purpose && lhs.scope == rhs.scope && lhs.reviewID == rhs.reviewID && lhs.reviewDigest == rhs.reviewDigest && lhs.reviewMetadata == rhs.reviewMetadata && lhs.expiresAt == rhs.expiresAt
    }
}
