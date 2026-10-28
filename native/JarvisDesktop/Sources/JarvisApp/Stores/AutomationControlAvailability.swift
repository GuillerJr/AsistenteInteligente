import Foundation

/// Admission follows the chat's lifecycle constraints. Reducing authority must
/// stay available while that chat waits for a tool decision or another run.
struct AutomationControlAvailability: Equatable {
    let canAdmit: Bool
    let canRevoke: Bool
    func canSetEnabled(currentlyEnabled: Bool) -> Bool { currentlyEnabled ? canRevoke : canAdmit }
    func canSetSuspended(currentlySuspended: Bool) -> Bool { currentlySuspended ? canAdmit : canRevoke }
}

extension ManagementStore {
    func automationControls(mayChangeSession: Bool) -> AutomationControlAvailability {
        let available = runtimeIsActive() && !isChanging
        return AutomationControlAvailability(canAdmit: available && mayChangeSession, canRevoke: available)
    }
}
