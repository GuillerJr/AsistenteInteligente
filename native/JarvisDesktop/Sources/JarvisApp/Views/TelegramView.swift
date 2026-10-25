import DesktopCore
import SwiftUI

struct TelegramView: View {
    @Bindable var store: ChatStore
    @State private var token = ""
    @State private var chatID = ""
    @State private var agentID = "jarvis"

    private var editable: Bool { store.canChangeSession && !store.telegram.isChanging }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Telegram").font(.title2.weight(.semibold))
                Text("Un bot propio para chats privados que elijas. Cada ruta usa una conversación nueva de Jarvis y conserva las aprobaciones nativas.")
                    .foregroundStyle(.secondary)
                if let snapshot = store.telegram.snapshot {
                    status(snapshot)
                    if !snapshot.connected { connection }
                    TelegramPairingView(store: store)
                    routes(snapshot)
                    controls(snapshot)
                } else {
                    Text("Actualiza el estado para revisar la configuración del canal.").foregroundStyle(.secondary)
                }
                HStack {
                    Button("Actualizar") { Task { await store.telegram.refreshFromUI() } }
                        .disabled(store.telegram.isChanging || store.telegram.isLoading)
                    if store.telegram.isLoading { ProgressView().controlSize(.small) }
                }
                history
                if let error = store.telegram.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            }.padding(20)
        }
        .task {
            await store.telegram.refreshFromUI()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
                await store.telegram.refreshFromUI()
            }
        }
        .onDisappear { token = "" }
        .onChange(of: store.telegram.secretResetID) { _, _ in token = "" }
    }

    @ViewBuilder private func status(_ snapshot: TelegramSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(snapshot.running ? "Canal activo" : "Canal detenido",
                  systemImage: snapshot.running ? "antenna.radiowaves.left.and.right" : "pause.circle")
                .font(.headline)
            if let bot = snapshot.botID {
                Text("Bot: \(snapshot.username.map { "@" + $0 } ?? "sin nombre público") · ID \(bot)")
            } else { Text("Bot desconectado").foregroundStyle(.secondary) }
            if let error = snapshot.errorCode {
                Text(TelegramPresentation.channelError(error))
                    .foregroundStyle(.red)
            }
            DisclosureGroup("Carpeta e identidad de configuración") {
                Text("Carpeta: \(snapshot.workspaceID)").textSelection(.enabled)
                Text("Configuración: \(snapshot.configurationHash)").textSelection(.enabled)
                if let pinned = snapshot.pinnedWorkspaceID { Text("Carpeta fijada al activar: \(pinned)").textSelection(.enabled) }
            }.font(.system(.caption2, design: .monospaced))
        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
    }

    private var connection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SecureField("Token del bot", text: $token).privacySensitive()
                .textFieldStyle(.roundedBorder).disabled(!editable)
                .accessibilityHint("Se conserva sólo en memoria y se borra del campo al conectar o cerrar el panel.")
            Text("Introduce el token de tu bot. No se guarda en disco: debes volver a introducirlo después de reiniciar el servicio.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Conectar bot…") {
                // Clear the visible state immediately. The only remaining copy
                // belongs to this call and goes out of scope after its RPC.
                let submitted = token; token = ""
                Task { await store.telegram.connect(token: submitted) }
            }.disabled(!editable || !TelegramValidation.token(token))
        }
    }

    private func routes(_ snapshot: TelegramSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Rutas privadas (\(snapshot.routes.count)/32)").font(.headline)
            ForEach(snapshot.routes) { route in
                VStack(alignment: .leading, spacing: 7) {
                    Text("Chat \(route.chatID)").font(.headline)
                    Text("Agente: \(store.management.agents.first(where: { $0.id == route.agentID })?.name ?? route.agentID)")
                    Text("Versión \(route.revision) · \(route.enabled ? "habilitada" : "deshabilitada")")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Abrir conversación") { Task { await store.openScheduledSession(route.sessionID) } }
                            .disabled(!store.canChangeSession)
                        Button("Quitar…", role: .destructive) { Task { await store.telegram.removeRoute(route) } }
                            .disabled(!editable || snapshot.running || store.telegram.pairing?.active == true)
                    }
                }.padding(12).background(.quaternary, in: .rect(cornerRadius: 8))
            }
            if snapshot.routes.isEmpty { Text("No hay chats autorizados para este bot.").foregroundStyle(.secondary) }
            TextField("ID numérico positivo de chat privado", text: $chatID).textFieldStyle(.roundedBorder)
                .disabled(!editable || snapshot.running || store.telegram.pairing?.active == true)
            Picker("Agente de la nueva ruta", selection: $agentID) {
                ForEach(store.management.agents) { Text($0.name).tag($0.id) }
            }.disabled(!editable || snapshot.running || store.telegram.pairing?.active == true)
            Button("Añadir ruta…") {
                guard let identity = TelegramValidation.chatID(chatID) else { return }
                Task { if await store.telegram.createRoute(chatID: identity, agentID: agentID) { chatID = "" } }
            }.disabled(!editable || snapshot.running || store.telegram.pairing?.active == true || snapshot.routes.count >= 32 || TelegramValidation.chatID(chatID) == nil ||
                       !store.management.agents.contains(where: { $0.id == agentID }) ||
                       snapshot.routes.contains(where: { $0.chatID == TelegramValidation.chatID(chatID) }))
            if snapshot.running { Text("Detén el canal antes de modificar sus rutas.").font(.caption).foregroundStyle(.secondary) }
        }
    }

    private func controls(_ snapshot: TelegramSnapshot) -> some View {
        HStack {
            if snapshot.running {
                Button("Detener canal…") { Task { await store.telegram.stop() } }.disabled(!editable)
            } else {
                Button("Activar canal…") { Task { await store.telegram.start(workspacePath: store.access.workspacePath) } }
                    .disabled(!editable || !snapshot.connected || store.telegram.pairing?.active == true || !snapshot.routes.contains(where: \.enabled))
            }
            if snapshot.connected {
                Button("Desconectar…", role: .destructive) { Task { await store.telegram.disconnect() } }.disabled(!editable)
            }
        }
    }

    private var history: some View {
        DisclosureGroup("Últimos registros del canal") {
            VStack(alignment: .leading, spacing: 12) {
                Text("Sólo metadatos: no muestra mensajes ni credenciales. Un resultado sin confirmar se conserva y no se reenvía automáticamente.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(store.telegram.events) { event in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Evento \(event.epoch):\(event.updateID) · \(TelegramPresentation.state(event.status))")
                        Text(event.outcome == "unknown" ? "Resultado sin confirmar" : event.outcome == "pending" ? "Resultado pendiente" : "Estado registrado")
                            .font(.caption).foregroundStyle(.secondary)
                        if let chat = event.chatID { Text("Chat \(chat) · \(event.agentID ?? "sin agente")").font(.caption) }
                        if let message = event.outgoingMessageID { Text("Telegram confirmó el mensaje saliente \(message).").font(.caption) }
                        if event.replyTruncated { Text("La respuesta se recortó por el límite del canal.").font(.caption) }
                        if let session = event.sessionID {
                            Button("Abrir conversación") { Task { await store.openScheduledSession(session) } }
                                .disabled(!store.canChangeSession)
                        }
                    }
                }
                if store.telegram.events.isEmpty { Text("Sin registros del canal.").foregroundStyle(.secondary) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
