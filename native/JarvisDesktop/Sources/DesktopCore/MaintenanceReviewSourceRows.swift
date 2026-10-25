import SwiftUI

/// Actual source rows and recovery policy notices in the native review.
/// Both collections must have distinct identities when they share a role.
public struct MaintenanceReviewSourceRows: View {
    public let review: MaintenanceReview
    public init(review: MaintenanceReview) { self.review = review }
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
            Text("\(MaintenancePresentation.role(recovery.role)): recuperación WAL en una copia privada, sólo de transacciones confirmadas. El original se conserva.")
                .font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("maintenance-wal-\(recovery.role)")
        }
    }
}
