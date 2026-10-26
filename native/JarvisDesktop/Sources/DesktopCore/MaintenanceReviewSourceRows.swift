import SwiftUI

/// Actual source rows and recovery policy notices in the native review.
/// Both collections must have distinct identities when they share a role.
public struct MaintenanceReviewSourceRows: View {
    public let review: MaintenanceReview
    public init(review: MaintenanceReview) { self.review = review }
    private func recoveryNotice(_ recovery: MaintenanceSqliteRecovery) -> String {
        let role = MaintenancePresentation.role(recovery.role)
        if recovery.mode == "cold_wal" { return "\(role): recuperación WAL en una copia privada, sólo de transacciones confirmadas. El original se conserva." }
        if recovery.mode.hasPrefix("cold_journal") { return "\(role): recuperación del journal SQLite en una copia privada. El original se conserva." }
        return "\(role): se recupera el estado durable presente. No se puede acreditar si faltan transacciones de un WAL ausente o vacío. El original se conserva."
    }
    public var body: some View {
        ForEach(review.sources) { source in
            HStack {
                Text(MaintenancePresentation.role(source.role))
                Spacer()
                Text(source.disposition == "absent" ? "Ausente" : source.disposition == "unavailable" ? "Pendiente de cargar" : source.disposition == "generated_builtin" ? "Base integrada · \(source.bytes) bytes" : "\(source.bytes) bytes")
                    .foregroundStyle(.secondary)
            }
            .accessibilityIdentifier("maintenance-source-\(source.role)")
        }
        ForEach(review.sqliteRecovery) { recovery in
            Text(recoveryNotice(recovery))
                .font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("maintenance-wal-\(recovery.role)")
        }
    }
}
