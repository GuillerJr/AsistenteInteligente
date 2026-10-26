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
    var openingChallengeID: String?
    private(set) var secretResetID = 0
    @ObservationIgnored private var client: TelegramClient?
    @ObservationIgnored private var pairingClient: TelegramPairingClient?
    @ObservationIgnored private let opener = TelegramNativeOpener()
    @ObservationIgnored private var runtimeIsActive: @Sendable () -> Bool = { false }
    @ObservationIgnored private var generation = 0

    func configure(client: LocalRPCClient) {
        runtimeIsActive = { client.isActive }
        self.client = TelegramClient(transport: client)
        pairingClient = TelegramPairingClient(transport: client)
    }
    func clearSecretInput() { secretResetID += 1 }

    func close() async {
        generation += 1; secretResetID += 1
        snapshot = nil; events = []; pairing = nil; errorMessage = nil
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
            guard version == generation else { return }
            snapshot = status; events = history.events; self.pairing = pairing; errorMessage = nil
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
        guard let client, let snapshot, !snapshot.running, pairing?.active != true else { return false }
        let detail = "Se creará una conversación persistente nueva para este chat privado, con el agente elegido. No se reutiliza otra conversación. Al activar el canal, el texto se enviará a Codex con ChatGPT; las herramientas seguirán esperando la aprobación del dueño en Jarvis.\n\n" +
            ManagementValidation.json(.object(["chat_id": .number(Double(chatID)), "agent_id": .string(agentID),
                                               "workspace_id": .string(snapshot.workspaceID)]))
        return await change(title: "Añadir una ruta privada de Telegram", action: "Crear esta ruta", detail: detail) {
            try await client.createRoute(chatID: chatID, agentID: agentID)
        }
    }

    func removeRoute(_ route: TelegramRouteSnapshot) async {
        guard let client, let snapshot, !snapshot.running, pairing?.active != true else { return }
        await change(title: "Quitar ruta de Telegram", action: "Quitar esta ruta",
                     detail: "Se dejarán de aceptar mensajes de este chat cuando se active el canal. La conversación de Jarvis se conserva.\n\n" + ManagementValidation.json(route.reviewValue)) {
            try await client.removeRoute(route)
        }
    }

    func start(workspacePath: String?) async {
        guard let client, let snapshot, snapshot.connected, !snapshot.running, pairing?.active != true else { return }
        await change(title: "Activar el canal de Telegram", action: "Activar estas rutas",
                     detail: "Jarvis consultará Telegram y responderá sólo a los chats privados de esta lista. El texto se enviará a Codex con ChatGPT. No se aceptan grupos ni adjuntos. Ningún mensaje de Telegram aprueba herramientas: las decisiones reales siguen en las tarjetas nativas de Jarvis. Revisa bot, agentes, conversaciones y carpeta exactos.\n\nCarpeta actual: " + (workspacePath ?? "Espacio privado de Jarvis") + "\n\n" + ManagementValidation.json(snapshot.reviewValue)) {
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
        guard runtimeIsActive(), !isChanging, let pairingClient else { return nil }
        do {
            let result = try await pairingClient.review(agentID: agentID)
            errorMessage = nil; return result
        } catch { errorMessage = TelegramPresentation.errorMessage(error); return nil }
    }

    func startPairing(_ review: TelegramPairingReview, updateConsumption: Bool, noOtherConsumer: Bool) async -> Bool {
        guard runtimeIsActive(), !isChanging, let pairingClient, updateConsumption, noOtherConsumer else { return false }
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
        guard OwnerManagementReview.confirm(title: title, action: action, detail: detail, isCurrent: runtimeIsActive) else { return false }
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
            guard version == generation else { return false }
            pairing = state; snapshot = status
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
        guard OwnerManagementReview.confirm(title: title, action: action, detail: detail, isCurrent: runtimeIsActive) else { return false }
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
            guard version == generation else { return false }
            snapshot = status; events = history.events; self.pairing = pairing
        } catch {
            if version == generation, errorMessage == nil { errorMessage = TelegramPresentation.errorMessage(error) }
        }
        return success
    }
}
