import Foundation

/// Pausing profile readers must never hide the exit of the owned service.
/// Recovery still needs the separate kernel-exit, lease and identity gate.
public enum ServiceSupervisionPolicy {
    public enum Decision: Equatable, Sendable { case read, recover, wait, attention }
    public static func decision(childRunning: Bool, lifecycleChanging: Bool,
                                recoveryBlocked: Bool, readersPaused: Bool) -> Decision {
        if lifecycleChanging { return .wait }
        if !childRunning { return recoveryBlocked ? .attention : .recover }
        return readersPaused ? .wait : .read
    }
}
