import Foundation

/// Closing a panel does not release a live maintenance worker or turn a known
/// gate-busy response into provider disconnection. No process is relaunched here.
public enum MaintenanceMonitorPolicy {
    public static func pauses(visible: Bool, operation: MaintenanceOperation?, requestInFlight: Bool, unresolvedResponse: Bool) -> Bool {
        visible || requestInFlight || unresolvedResponse || (operation.map { !$0.settled } ?? false)
    }
    public static func isTemporaryBusy(_ error: Error) -> Bool {
        guard let failure = error as? RPCFailure,
              case .remote(-32037, "profile_maintenance_busy") = failure else { return false }
        return true
    }
}

public enum MaintenanceResponsePolicy {
    /// A local preflight rejection or explicit RPC rejection did not return a
    /// job. Lost/invalid acknowledgements may have accepted it: never resend.
    public static func submissionMayBeUnconfirmed(_ error: Error) -> Bool {
        guard let failure = error as? RPCFailure else { return true }
        switch failure { case .configuration, .remote: return false; default: return true }
    }
}
