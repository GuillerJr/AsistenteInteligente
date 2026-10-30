import DesktopCore
import ServiceManagement

/// Merely constructing this adapter never registers, starts, or unregisters an
/// item. The controller calls mutations only after an explicit owner review.
@MainActor final class MacLoginItemService: LoginItemRegistrationService {
    private let service = SMAppService.mainApp

    var registrationStatus: LoginItemRegistrationStatus {
        switch service.status {
        case .notRegistered: .notRegistered
        case .enabled: .enabled
        case .requiresApproval: .requiresApproval
        case .notFound: .notFound
        @unknown default: .unknown
        }
    }
    func register() throws { try service.register() }
    func unregister() async throws { try await service.unregister() }
    func openSettings() { SMAppService.openSystemSettingsLoginItems() }
}
