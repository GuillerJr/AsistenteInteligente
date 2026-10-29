import AppKit
import DesktopCore
import Foundation
import Observation

@MainActor @Observable
final class TelegramStore {
    var snapshot: TelegramSnapshot?
    var events: [TelegramEventSnapshot] = []
    var errorMessage: String?
    var isChanging = false
    var isLoading = false
    var pairing: TelegramPairingStatus?
    var groupPairing: TelegramGroupPairingStatus?
    var copiedGroupChallengeID: String?
    var pairingActive: Bool { pairing?.active == true || groupPairing?.active == true }
    var mayReduceAuthority: Bool { runtimeIsActive() && !isChanging }
    var openingChallengeID: String?
    private(set) var secretResetID = 0
    @ObservationIgnored private var client: TelegramClient?
    @ObservationIgnored private var pairingClient: TelegramPairingClient?
    @ObservationIgnored private var groupPairingClient: TelegramGroupPairingClient?
    @ObservationIgnored private let commandCopier = TelegramCommandCopier()
    @ObservationIgnored private let opener = TelegramNativeOpener()
    @ObservationIgnored private var runtimeIsActive: @Sendable () -> Bool = { false }
    @ObservationIgnored private var generation = 0

    func configure(client: LocalRPCClient) {
        runtimeIsActive = { client.isActive }
        self.client = TelegramClient(transport: client)
        pairingClient = TelegramPairingClient(transport: client)
        groupPairingClient = TelegramGroupPairingClient(transport: client)
    }
    func clearSecretInput() { secretResetID += 1 }

    func close() async {
        generation += 1; secretResetID += 1
        snapshot = nil; events = []; pairing = nil; groupPairing = nil; copiedGroupChallengeID = nil; errorMessage = nil
        commandCopier.clearOwnedCommand()
        await client?.close()
    }

    func refreshFromUI() async {
        guard runtimeIsActive(), !isChanging, !isLoading, let client else { return }
        isLoading = true
        defer { isLoading = false }
        let version = generation
        do {
            let status = try await client.status()
            let history = try await client.history()
            let pairing = try await pairingClient?.status()
            let group = try await groupPairingClient?.status()
            guard version == generation else { return }
            snapshot = status; events = history.events; self.pairing = pairing; groupPairing = group; errorMessage = nil
            if group?.state != "listening" || !status.connected { commandCopier.clearOwnedCommand() }
        } catch {
            if version == generation { errorMessage = TelegramPresentation.errorMessage(error) }
        }
    }

    /// The token is an ephemeral argument, never an observable property,
    /// generic review payload, persisted preference or model message.
    func connect(token: String) async {
        guard let client else { return }
        guard TelegramValidation.token(token) else { errorMessage = "El token del bot no tiene un formato válido."; return }
        await change(title: "Conectar un bot de Telegram", action: "Conectar este bot",
                     detail: "Jarvis enviará el token que acabas de introducir únicamente a Telegram para comprobar la identidad del bot. El token permanecerá en memoria hasta desconectar o cerrar el servicio. Tendrás que introducirlo otra vez tras reiniciar. Esto no activa la recepción de mensajes ni concede permisos de herramientas.") {
            try await client.connect(token: token)
        }
    }

    func disconnect() async {
        guard let client, let snapshot else { return }
        await change(title: "Desconectar Telegram", action: "Desconectar y borrar token de memoria",
                     detail: "Se detendrá el canal y se cancelarán sus ejecuciones activas. Un efecto ya despachado puede quedar sin confirmar. Las rutas y los registros se conservan.\n\n" + ManagementValidation.json(snapshot.reviewValue)) {
            try await client.disconnect()
        }
    }

    func createRoute(chatID: Int, agentID: String) async -> Bool {
        guard let client, let snapshot, !snapshot.running, !pairingActive else { return false }
        let detail = "Se creará una conversación persistente nueva para este chat privado, con el agente elegido. No se reutiliza otra conversación. Al activar el canal, el texto se enviará a Codex con ChatGPT; las herramientas seguirán esperando la aprobación del dueño en Jarvis.\n\n" +
            ManagementValidation.json(.object(["chat_id": .number(Double(chatID)), "agent_id": .string(agentID),
                                               "workspace_id": .string(snapshot.workspaceID)]))
        return await change(title: "Añadir una ruta privada de Telegram", action: "Crear esta ruta", detail: detail) {
            try await client.createRoute(chatID: chatID, agentID: agentID)
        }
    }

    func removeRoute(_ route: TelegramRouteSnapshot) async {
        guard let client, let snapshot, !snapshot.running, !pairingActive else { return }
        await change(title: "Quitar ruta de Telegram", action: "Quitar esta ruta",
                     detail: "Se dejarán de aceptar mensajes de este chat cuando se active el canal. La conversación de Jarvis se conserva.\n\n" + ManagementValidation.json(route.reviewValue)) {
            try await client.removeRoute(route)
        }
    }

    func start(workspacePath: String?) async {
        guard let client, let snapshot, snapshot.connected, !snapshot.running, !pairingActive else { return }
        await change(title: "Activar el canal de Telegram", action: "Activar estas rutas",
                     detail: "Jarvis consultará Telegram y responderá sólo a las rutas de esta lista. En grupos se aceptarán únicamente los remitentes y el tema revisados. El texto se enviará a Codex con ChatGPT. Los adjuntos sólo se podrán inspeccionar tras tu aprobación nativa y cuando la ruta los admita. Ningún mensaje de Telegram aprueba herramientas: las decisiones reales siguen en Jarvis. Revisa bot, destinos, remitentes, agentes, conversaciones y carpeta exactos.\n\nCarpeta actual: " + (workspacePath ?? "Espacio privado de Jarvis") + "\n\n" + ManagementValidation.json(snapshot.reviewValue)) {
            try await client.start(configurationHash: snapshot.configurationHash)
        }
    }

    func stop() async {
        guard let client, let snapshot else { return }
        await change(title: "Detener el canal de Telegram", action: "Detener el canal",
                     detail: "Se detendrá la recepción y se cancelarán las ejecuciones activas del canal. Los envíos o acciones ya despachados pueden quedar sin confirmar y no se repiten automáticamente.\n\n" + ManagementValidation.json(snapshot.reviewValue)) {
            try await client.stop()
        }
    }

    func pairingReview(agentID: String) async -> TelegramPairingReview? {
        guard runtimeIsActive(), !isChanging, groupPairing?.active != true, let pairingClient else { return nil }
        do {
            let result = try await pairingClient.review(agentID: agentID)
            errorMessage = nil; return result
        } catch { errorMessage = TelegramPresentation.errorMessage(error); return nil }
    }

    func startPairing(_ review: TelegramPairingReview, updateConsumption: Bool, noOtherConsumer: Bool) async -> Bool {
        guard runtimeIsActive(), !isChanging, groupPairing?.active != true, let pairingClient, updateConsumption, noOtherConsumer else { return false }
        return await pairingChange(title: "Iniciar enlace privado con Telegram", action: "Iniciar este enlace",
            detail: "Durante un máximo de 180 segundos se leerán y avanzarán las actualizaciones del bot, incluso las ignoradas. Confirmas que este bot no tiene otro consumidor: el bloqueo de Jarvis sólo excluye lectores locales. Telegram conservará el mensaje de inicio con el código privado; Jarvis no lo mostrará ni enviará al modelo. Si no se puede abrir Telegram, el intento se cancelará. Todavía no se crea una ruta ni se activa el canal.\n\n" + ManagementValidation.json(review.reviewValue)) {
                .state(try await pairingClient.start(review, acknowledgeUpdateConsumption: updateConsumption,
                                                     confirmNoOtherConsumer: noOtherConsumer))
            }
    }

    func openPairing() async {
        guard runtimeIsActive(), !isChanging, let pairingClient, let pairing, pairing.state == "listening", let challenge = pairing.challengeID,
              openingChallengeID != challenge else { return }
        isChanging = true; generation += 1
        let version = generation
        openingChallengeID = challenge
        defer { isChanging = false }
        do {
            try await pairingClient.open(pairing, using: opener) { [weak self] in self?.generation == version && self?.runtimeIsActive() == true }
            guard version == generation else { return }
            errorMessage = nil
        }
        catch {
            if version == generation {
                errorMessage = (error as? TelegramPairingOpenFailure)?.localizedDescription ?? TelegramPresentation.errorMessage(error)
            }
        }
        guard version == generation else { return }
        do {
            let state = try await pairingClient.status()
            guard version == generation else { return }
            self.pairing = state
        }
        catch { if errorMessage == nil { errorMessage = TelegramPresentation.errorMessage(error) } }
    }

    func cancelPairing() async {
        guard let pairingClient else { return }
        _ = await pairingChange(title: "Cancelar enlace de Telegram", action: "Cancelar este enlace",
            detail: "Se detendrá el lector y se descartará la propuesta de chat. No se crea ni activa ninguna ruta.") {
                .state(try await pairingClient.cancel())
            }
    }

    func confirmPairing(_ claim: TelegramPairingStatus) async {
        guard let pairingClient else { return }
        _ = await pairingChange(title: "Confirmar chat privado de Telegram", action: "Crear esta ruta",
            detail: "Revisa el chat propuesto, el bot y la versión exacta del agente. Esta decisión crea una conversación persistente nueva y su ruta privada. El canal seguirá detenido hasta que lo actives por separado.\n\n" + ManagementValidation.json(claim.claimReviewValue)) {
                .snapshot(try await pairingClient.confirm(claim))
            }
    }

    func confirmRestoredPairing(_ claim: TelegramPairingStatus, route: TelegramRouteSnapshot) async {
        guard let pairingClient, let snapshot else { return }
        _ = await pairingChange(title: "Verificar una ruta conservada de Telegram", action: "Verificar esta ruta",
            detail: "Revisa el chat privado que acaba de responder al enlace nuevo. Se verificará únicamente esta ruta, con el mismo bot, agente, conversación y carpeta. La comprobación se conserva en memoria y desaparece al desconectar o cambiar su configuración. No crea otra conversación ni inicia el canal.\n\n" + ManagementValidation.json(claim.claimReviewValue) + "\n\n" + ManagementValidation.json(route.reviewValue)) {
                .snapshot(try await pairingClient.confirmRestored(claim, route: route, snapshot: snapshot))
            }
    }

    private enum PairingChangeResult { case state(TelegramPairingStatus), snapshot(TelegramSnapshot) }

    private func pairingChange(title: String, action: String, detail: String, operation: () async throws -> PairingChangeResult) async -> Bool {
        guard runtimeIsActive(), !isChanging, let pairingClient, let client else { return false }
        isChanging = true; generation += 1
        let version = generation
        defer { isChanging = false }
        guard OwnerManagementReview.confirm(title: title, action: action, detail: detail, isCurrent: { self.generation == version && self.runtimeIsActive() }) else { return false }
        var success = false
        do {
            let result = try await operation()
            guard version == generation else { return false }
            switch result {
            case .state(let state): pairing = state
            case .snapshot(let status): snapshot = status
            }
            errorMessage = nil; success = true
        } catch { if version == generation { errorMessage = TelegramPresentation.errorMessage(error) } }
        guard version == generation else { return false }
        do {
            let state = try await pairingClient.status()
            let status = try await client.status()
            let group = try await groupPairingClient?.status()
            guard version == generation else { return false }
            pairing = state; snapshot = status; groupPairing = group
            if group?.state != "listening" || !status.connected { commandCopier.clearOwnedCommand() }
        } catch { if version == generation, errorMessage == nil { errorMessage = TelegramPresentation.errorMessage(error) } }
        return success
    }

    @discardableResult
    private func change(title: String, action: String, detail: String,
                        operation: () async throws -> TelegramSnapshot) async -> Bool {
        guard runtimeIsActive(), !isChanging, let client else { return false }
        isChanging = true; generation += 1
        let version = generation
        defer { isChanging = false }
        guard OwnerManagementReview.confirm(title: title, action: action, detail: detail, isCurrent: { self.generation == version && self.runtimeIsActive() }) else { return false }
        var success = false
        do {
            let result = try await operation()
            guard version == generation else { return false }
            snapshot = result; errorMessage = nil; success = true
        } catch {
            if version == generation { errorMessage = TelegramPresentation.errorMessage(error) }
        }
        // Reconcile only reads after uncertainty. Never repeat a connection,
        // route mutation or start/stop because its acknowledgment was lost.
        do {
            let status = try await client.status()
            let history = try await client.history()
            let pairing = try await pairingClient?.status()
            let group = try await groupPairingClient?.status()
            guard version == generation else { return false }
            snapshot = status; events = history.events; self.pairing = pairing; groupPairing = group
            if group?.state != "listening" || !status.connected { commandCopier.clearOwnedCommand() }
        } catch {
            if version == generation, errorMessage == nil { errorMessage = TelegramPresentation.errorMessage(error) }
        }
        return success
    }
}

@MainActor private final class TelegramCommandCopier: TelegramNativeCommandCopier {
    private var ownedChangeCount: Int?
    private var expiry: Task<Void, Never>?
    func copyTelegramCommand(_ command: String) -> Bool {
        clearOwnedCommand()
        NSPasteboard.general.prepareForNewContents(with: [.currentHostOnly])
        guard NSPasteboard.general.setString(command, forType: .string) else { return false }
        ownedChangeCount = NSPasteboard.general.changeCount
        expiry = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(180)) } catch { return }
            self?.clearOwnedCommand()
        }
        return true
    }
    func clearOwnedCommand() {
        expiry?.cancel(); expiry = nil
        if let ownedChangeCount, NSPasteboard.general.changeCount == ownedChangeCount { NSPasteboard.general.clearContents() }
        ownedChangeCount = nil
    }
    deinit { expiry?.cancel() }
}

extension TelegramStore {
    func createRoute(chatID: Int, agentID: String, policy: TelegramRoutePolicy) async -> Bool {
        guard let client, let snapshot, !snapshot.running, !pairingActive else { return false }
        var review = policy.params; review["chat_id"] = .number(Double(chatID)); review["agent_id"] = .string(agentID)
        review["workspace_id"] = .string(snapshot.workspaceID)
        return await change(title: "Crear ruta de Telegram", action: "Crear esta ruta", detail:
            "Se creará una conversación nueva. El grupo requiere además una prueba fresca desde un remitente autorizado antes de activarse. Los adjuntos requieren una aprobación separada para leerlos; esta opción sólo admite su recepción.\n\n" + ManagementValidation.json(.object(review))) {
                try await client.createRoute(chatID: chatID, agentID: agentID, policy: policy)
            }
    }
    func updateRoute(_ route: TelegramRouteSnapshot, policy: TelegramRoutePolicy, enabled: Bool) async -> Bool {
        guard let client, let snapshot, !snapshot.running, !pairingActive, snapshot.routes.contains(route) else { return false }
        var review = policy.params; review["enabled"] = .bool(enabled)
        return await change(title: "Cambiar ruta de Telegram", action: "Guardar esta política", detail:
            "Conserva agente y conversación. Cambia sólo esta revisión y revoca su prueba anterior; los grupos deberán verificarse otra vez. Los archivos requieren su propia aprobación de lectura.\n\nActual:\n" + ManagementValidation.json(route.reviewValue) + "\n\nNueva:\n" + ManagementValidation.json(.object(review))) {
                try await client.updateRoute(route, policy: policy, enabled: enabled)
            }
    }
    func cancelEvent(_ event: TelegramEventSnapshot) async {
        guard runtimeIsActive(), !isChanging, let client, event.canCancel, events.contains(event) else { return }
        isChanging = true; generation += 1; let version = generation
        defer { isChanging = false }
        let detail: RPCValue = .object(["epoch": .number(Double(event.epoch)), "update_id": .number(Double(event.updateID)),
            "run_id": event.runID.map(RPCValue.string) ?? .null, "chat_id": event.chatID.map { .number(Double($0)) } ?? .null,
            "sender_id": event.senderID.map { .number(Double($0)) } ?? .null,
            "message_thread_id": event.messageThreadID.map { .number(Double($0)) } ?? .null,
            "route_revision": event.routeRevision.map { .number(Double($0)) } ?? .null])
        guard OwnerManagementReview.confirm(title: "Cancelar una ejecución de Telegram", action: "Cancelar esta ejecución", detail:
            "Sólo cancela este evento y su ejecución exacta. Los efectos despachados pueden quedar sin confirmar; no se reenvían automáticamente.\n\n" + ManagementValidation.json(detail), isCurrent: { self.generation == version && self.runtimeIsActive() }) else { return }
        do { try await client.cancelEvent(event); if version == generation { errorMessage = nil } }
        catch { if version == generation { errorMessage = TelegramPresentation.errorMessage(error) } }
        do {
            let history = try await client.history(); let status = try await client.status()
            if version == generation { events = history.events; snapshot = status }
        } catch { if version == generation, errorMessage == nil { errorMessage = TelegramPresentation.errorMessage(error) } }
    }
    func groupPairingReview(route: TelegramRouteSnapshot) async -> TelegramGroupPairingReview? {
        guard runtimeIsActive(), !isChanging, !pairingActive, let groupPairingClient else { return nil }
        do { return try await groupPairingClient.review(route: route) }
        catch { errorMessage = TelegramPresentation.errorMessage(error); return nil }
    }
    func startGroupPairing(_ review: TelegramGroupPairingReview, updateConsumption: Bool, noOtherConsumer: Bool) async -> Bool {
        guard !pairingActive, let groupPairingClient else { return false }
        return await groupChange(title: "Verificar grupo de Telegram", action: "Iniciar esta prueba", detail:
            "Durante 180 segundos Jarvis leerá y avanzará las actualizaciones del bot, incluidas las ignoradas. Confirma que no hay otro consumidor. Copiarás un código de un solo uso y lo publicarás tú en el grupo y tema exactos desde un remitente autorizado. Telegram conservará ese mensaje. No se activa la ruta ni se envía el código al modelo.\n\n" + ManagementValidation.json(review.reviewValue)) {
                _ = try await groupPairingClient.start(review, updateConsumption: updateConsumption, noOtherConsumer: noOtherConsumer)
            }
    }
    func copyGroupPairingCommand() async {
        guard let groupPairingClient, let groupPairing, groupPairing.state == "listening", let challenge = groupPairing.base.challengeID,
              copiedGroupChallengeID != challenge else { return }
        _ = await groupChange(title: "Copiar código privado del grupo", action: "Copiar este código", detail:
            "El portapapeles de este Mac se sustituirá por un código privado de un solo uso, sin compartirlo con otros dispositivos. Pégalo tú en el grupo y tema revisados antes de que caduque. Jarvis borrará su copia al terminar el enlace o tras tres minutos si el portapapeles no cambió. No lo envíes a otros chats ni a Codex. Jarvis no publicará ningún mensaje por ti.\n\n" + ManagementValidation.json(groupPairing.claimReviewValue)) {
                self.copiedGroupChallengeID = challenge
                let version = self.generation
                try await groupPairingClient.copy(groupPairing, using: self.commandCopier) { self.generation == version && self.runtimeIsActive() }
            }
    }
    func cancelGroupPairing() async {
        guard let groupPairingClient else { return }
        _ = await groupChange(title: "Cancelar prueba del grupo", action: "Cancelar esta prueba", detail:
            "Se detendrá el lector de esta prueba. La ruta se conserva y seguirá necesitando una prueba fresca.") { _ = try await groupPairingClient.cancel() }
    }
    func confirmGroupPairing(_ claim: TelegramGroupPairingStatus) async {
        guard let groupPairingClient, let snapshot, let route = snapshot.routes.first(where: { $0.chatID == claim.chatID }) else { return }
        _ = await groupChange(title: "Confirmar grupo y remitente", action: "Verificar esta ruta", detail:
            "Revisa el destino, tema y remitente que publicaron el código exacto. Esta prueba se conserva sólo en memoria y se pierde al desconectar o cambiar la configuración. No activa el canal.\n\n" + ManagementValidation.json(claim.claimReviewValue) + "\n\n" + ManagementValidation.json(route.reviewValue)) {
                let version = self.generation
                let result = try await groupPairingClient.confirm(claim, route: route, snapshot: snapshot)
                if self.generation == version, self.runtimeIsActive() { self.snapshot = result }
            }
    }
    private func groupChange(title: String, action: String, detail: String, operation: () async throws -> Void) async -> Bool {
        guard runtimeIsActive(), !isChanging, let groupPairingClient, let client else { return false }
        isChanging = true; generation += 1; let version = generation
        defer { isChanging = false }
        guard OwnerManagementReview.confirm(title: title, action: action, detail: detail, isCurrent: { self.generation == version && self.runtimeIsActive() }) else { return false }
        var success = false
        do { try await operation(); if version == generation { success = true; errorMessage = nil } }
        catch { if version == generation { errorMessage = (error as? TelegramGroupCommandFailure)?.localizedDescription ?? TelegramPresentation.errorMessage(error) } }
        do {
            let state = try await groupPairingClient.status(); let status = try await client.status()
            if version == generation {
                groupPairing = state; snapshot = status
                if state.state != "listening" || !status.connected { commandCopier.clearOwnedCommand() }
            }
        } catch { if version == generation, errorMessage == nil { errorMessage = TelegramPresentation.errorMessage(error) } }
        return success
    }
}
