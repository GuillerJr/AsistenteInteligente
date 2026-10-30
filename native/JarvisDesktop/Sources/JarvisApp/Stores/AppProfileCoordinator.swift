import DesktopCore
import Foundation
import Observation

@MainActor
final class ProfileSession {
    let store: ChatStore
    var runtimeID: UUID { store.runtime.id }
    init(options: LaunchOptions? = nil, target: ProfileOpenTarget? = nil) {
        store = ChatStore(options: options, target: target)
    }
}

/// App-wide owner of the one active profile, including retained Settings scenes.
@MainActor @Observable
final class AppProfileCoordinator {
    private(set) var active = ProfileSession()
    private(set) var isOpening = false
    var notice: String?
    @ObservationIgnored private var terminating = false
    @ObservationIgnored private let gate = ProfileHandoffGate()
    @ObservationIgnored private let drafts = ProfileDraftArchive()
    func start() async { guard !terminating else { return }; await active.store.start() }
    func open(_ target: ProfileOpenTarget, from runtimeID: UUID) async {
        guard (try? active.store.runtime.check(expectedID: runtimeID)) != nil else { return }
        guard !terminating, !isOpening, active.store.mayHandoff else { notice = "Termina el trabajo o la decisión pendiente antes de cambiar de perfil."; return }
        let old = active
        isOpening = true; old.store.handoffPreparing = true
        defer { isOpening = false; old.store.handoffPreparing = false }
        do {
            let source = try await old.store.readLocalStatus()
            guard let options = old.store.launchOptions, let predecessor = old.store.serviceOwner,
                  active === old, target.identity != source.identity else { throw RPCFailure.configuration }
            try drafts.archive(old.store.durableDraftSnapshot, origin: source.identity)
            notice = "Cerrando el perfil actual antes de abrir el restaurado…"
            try await gate.open(target: target, source: options, origin: source.identity, predecessor: predecessor,
                retireAndStop: { await old.store.stopForHandoff() }, start: { [self] options in
                    guard !terminating else { throw CancellationError() }
                    let candidate = ProfileSession(options: options, target: target)
                    // Retain the new owner even if bootstrap fails or its wait is
                    // cancelled. Termination and supervision now target it.
                    active = candidate
                    notice = "Iniciando el perfil restaurado con conexiones nuevas…"
                    await candidate.store.start()
                    if let local = candidate.store.localProfileStatus, local.scope.profileID == source.scope.profileID {
                        _ = await candidate.store.shutdown()
                        notice = "Se rechazó el nuevo perfil porque su identidad de autoridad no era nueva."
                        return
                    }
                    guard let local = candidate.store.localProfileStatus, local.matches(target),
                          local.scope.profileID != source.scope.profileID, candidate.store.ready else {
                        notice = "El arranque del nuevo perfil no está confirmado. Jarvis conserva su supervisión; no se iniciará otro servicio."
                        return
                    }
                    notice = "Perfil restaurado abierto. La carpeta de trabajo es nueva y vacía; conecta tu cuenta sólo cuando lo elijas. Los borradores duraderos anteriores quedan aislados sólo en memoria durante esta sesión; aún no hay un selector para recuperarlos. Los chats y borradores temporales no se trasladan."
                })
        } catch {
            if active === old, error as? RPCFailure == .oversized {
                notice = "No se cambió de perfil: el archivo de borradores de esta sesión llegó a su límite de memoria. Conserva tus textos antes de cerrar la app."
                return
            }
            if active === old {
                notice = "No se abrió el perfil restaurado: el cierre anterior o la selección exacta no pudieron confirmarse. No se inició otro servicio."
            } else {
                notice = "No se confirmó la apertura. El nuevo servicio conserva su propietario y no se repetirá el inicio automáticamente."
            }
        }
    }
    func shutdown() async -> Bool { terminating = true; return await active.store.shutdown() }
    var shutdownWarning: String? { active.store.shutdownWarning }
}
