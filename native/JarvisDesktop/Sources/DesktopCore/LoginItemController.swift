import Foundation
import Observation

/// Registration is owned by macOS. A remembered preference is never evidence
/// that the app is allowed to start at login, including after user revocation.
public enum LoginItemRegistrationStatus: Sendable, Equatable {
    case notRegistered, enabled, requiresApproval, notFound, unknown

    public var title: String {
        switch self {
        case .notRegistered: "Desactivado"
        case .enabled: "Activado en macOS"
        case .requiresApproval: "Requiere tu aprobación en macOS"
        case .notFound: "macOS no encuentra esta app"
        case .unknown: "Estado no reconocido; no se modificó el arranque"
        }
    }
}

@MainActor public protocol LoginItemRegistrationService: AnyObject {
    var registrationStatus: LoginItemRegistrationStatus { get }
    func register() throws
    func unregister() async throws
    func openSettings()
}

/// This controls only the main app's login item. It neither launches a second
/// service nor changes the running profile, tool permissions, or account state.
@MainActor @Observable
public final class LoginItemController {
    public private(set) var status: LoginItemRegistrationStatus = .unknown
    public private(set) var isChanging = false
    public private(set) var notice: String?
    public private(set) var eligible = false
    @ObservationIgnored private let service: any LoginItemRegistrationService
    @ObservationIgnored private let eligibility: @MainActor () -> Bool
    @ObservationIgnored private let validateBundle: @MainActor () async -> Bool

    public init(service: any LoginItemRegistrationService, eligibility: @escaping @MainActor () -> Bool = { true },
                validateBundle: @escaping @MainActor () async -> Bool) {
        self.service = service; self.eligibility = eligibility; self.validateBundle = validateBundle
    }

    /// Read-only. In particular, app activation and Settings refresh never
    /// restore a registration revoked in System Settings.
    public func refresh() {
        eligible = eligibility()
        status = service.registrationStatus
    }

    public var canEnable: Bool { eligible && !isChanging && status == .notRegistered }
    public var canDisable: Bool { eligible && !isChanging && (status == .enabled || status == .requiresApproval) }

    public func change(enabled: Bool, confirm: @MainActor () -> Bool) async {
        guard !isChanging else { return }
        refresh()
        guard enabled ? canEnable : canDisable else { return }
        let reviewedStatus = status
        isChanging = true
        defer { isChanging = false; refresh() }
        guard confirm() else { return }
        // The owner review can run a nested AppKit event loop. Revalidate both
        // the bundle and OS state at the actual effect boundary.
        guard await validateBundle(), !Task.isCancelled, eligibility(), service.registrationStatus == reviewedStatus else {
            notice = "La app o su autorización cambió durante la revisión. No se modificó el arranque."
            return
        }
        do {
            if enabled { try service.register() }
            else { try await service.unregister() }
            let observed = service.registrationStatus
            if enabled {
                notice = switch observed {
                case .enabled: "Jarvis se abrirá al iniciar tu sesión. No se activaron cuentas ni herramientas."
                case .requiresApproval: "macOS requiere tu aprobación. Abre «Ítems de inicio» para revisar Jarvis."
                default: "La solicitud terminó, pero macOS no confirma el arranque al iniciar sesión. No se repetirá automáticamente."
                }
            } else {
                notice = observed == .notRegistered
                    ? "Arranque al iniciar sesión desactivado. Jarvis y su servicio actual siguen abiertos hasta que elijas Salir."
                    : "macOS aún no confirma la retirada del arranque. Revisa «Ítems de inicio»; no se repetirá automáticamente."
            }
        } catch {
            notice = "macOS no confirmó el cambio de arranque. Revisa el estado y «Ítems de inicio» antes de volver a elegirlo."
        }
    }

    public func openSettings() { service.openSettings() }
}
