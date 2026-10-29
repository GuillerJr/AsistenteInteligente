import DesktopCore
import SwiftUI

struct TelegramRoutesView: View {
    @Bindable var store: ChatStore
    let snapshot: TelegramSnapshot
    @State private var editing: TelegramRouteSnapshot?
    @State private var adding = false
    private var editable: Bool { store.canChangeSession && !store.telegram.isChanging && !snapshot.running && !store.telegram.pairingActive }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Rutas (\(snapshot.routes.count)/32)").font(.headline)
            ForEach(snapshot.routes) { route in
                VStack(alignment: .leading, spacing: 7) {
                    Text("\(route.chatType.label) · \(String(route.chatID))").font(.headline)
                    Text("Agente \(route.agentID) · versión \(route.revision) · \(route.enabled ? "habilitada" : "deshabilitada")").font(.caption)
                    if route.chatType != .privateChat { Text("Remitentes: " + route.policy.allowedSenderIDs.map(String.init).joined(separator: ", ")).font(.caption).textSelection(.enabled) }
                    if let topic = route.policy.messageThreadID { Text("Sólo tema \(topic)").font(.caption) }
                    Text(route.policy.allowAttachments ? "Admite adjuntos; inspección con aprobación separada" : "Sólo mensajes de texto").font(.caption).foregroundStyle(.secondary)
                    if snapshot.restoredPairingRequiredChatIDs.contains(route.chatID) { Text("Prueba fresca requerida").font(.caption).foregroundStyle(.orange) }
                    HStack {
                        Button("Abrir conversación") { Task { await store.openScheduledSession(route.sessionID) } }.disabled(!store.canChangeSession)
                        Button("Editar política…") { editing = route }.disabled(!editable)
                        Button("Quitar…", role: .destructive) { Task { await store.telegram.removeRoute(route) } }.disabled(!editable)
                    }
                }.padding(12).background(.quaternary, in: .rect(cornerRadius: 8))
            }
            if snapshot.routes.isEmpty { Text("Sin rutas autorizadas.").foregroundStyle(.secondary) }
            Button("Añadir ruta…") { adding = true }.disabled(!editable || snapshot.routes.count >= 32)
            if snapshot.running { Text("Detén el canal antes de cambiar rutas.").font(.caption).foregroundStyle(.secondary) }
        }
        .sheet(isPresented: $adding) { TelegramRouteEditor(store: store, route: nil) }
        .sheet(item: $editing) { TelegramRouteEditor(store: store, route: $0) }
    }
}

private struct TelegramRouteEditor: View {
    @Bindable var store: ChatStore
    let route: TelegramRouteSnapshot?
    @Environment(\.dismiss) private var dismiss
    @State private var chatID = ""
    @State private var agentID = "jarvis"
    @State private var kind = TelegramChatType.privateChat
    @State private var senders = ""
    @State private var topic = ""
    @State private var attachments = false
    @State private var enabled = true
    private var identity: Int? { kind == .privateChat ? TelegramValidation.chatID(chatID) : TelegramValidation.groupChatID(chatID) }
    private var policy: TelegramRoutePolicy? {
        let parts = senders.split(separator: ",", omittingEmptySubsequences: false)
        let senderIDs = kind == .privateChat ? [] : parts.compactMap { TelegramValidation.chatID(String($0)) }
        guard kind == .privateChat || senderIDs.count == parts.count else { return nil }
        let trimmed = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        let thread = trimmed.isEmpty ? nil : TelegramValidation.chatID(trimmed)
        guard trimmed.isEmpty || thread != nil else { return nil }
        return try? TelegramRoutePolicy(chatType: kind, allowedSenderIDs: senderIDs, messageThreadID: thread, allowAttachments: attachments)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            Text(route == nil ? "Añadir ruta" : "Editar política de ruta").font(.title2.weight(.semibold))
            Picker("Tipo de chat", selection: $kind) { ForEach(TelegramChatType.allCases, id: \.rawValue) { Text($0.label).tag($0) } }
                .disabled(route != nil)
            TextField(kind == .privateChat ? "ID positivo del chat privado" : "ID negativo del grupo", text: $chatID).textFieldStyle(.roundedBorder).disabled(route != nil)
            Picker("Agente", selection: $agentID) { ForEach(store.management.agents) { Text($0.name).tag($0.id) } }.disabled(route != nil)
            if kind != .privateChat {
                TextField("IDs positivos de remitentes, separados por comas (máximo 32)", text: $senders).textFieldStyle(.roundedBorder)
                Text("Sólo se aceptan estos remitentes humanos en el grupo exacto; se excluyen bots y publicaciones anónimas. Debes publicar una prueba de enlace antes de activarlo.").font(.caption).foregroundStyle(.secondary)
            }
            if kind == .supergroup { TextField("ID positivo del tema (vacío: sólo mensajes sin tema)", text: $topic).textFieldStyle(.roundedBorder) }
            Toggle("Admitir adjuntos con lectura sujeta a aprobación nativa", isOn: $attachments)
            if route != nil { Toggle("Ruta habilitada", isOn: $enabled) }
            Text("El texto aceptado se enviará a Codex con ChatGPT. Los permisos de herramientas se revisan en Jarvis; un mensaje remoto nunca los autoriza.").font(.caption).foregroundStyle(.secondary)
            if let error = store.telegram.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer(); Button("Volver") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(route == nil ? "Revisar y crear…" : "Revisar y guardar…") {
                    guard let identity, let policy else { return }
                    Task {
                        let saved: Bool
                        if let route { saved = await store.telegram.updateRoute(route, policy: policy, enabled: enabled) }
                        else { saved = await store.telegram.createRoute(chatID: identity, agentID: agentID, policy: policy) }
                        if saved { dismiss() }
                    }
                }.disabled(policy == nil || identity == nil || !store.canChangeSession || store.telegram.isChanging || store.telegram.pairingActive || store.telegram.snapshot?.running != false || !store.management.agents.contains(where: { $0.id == agentID }))
            }
        }.padding(24).frame(width: 650)
        .onAppear {
            guard let route else { return }
            chatID = String(route.chatID); agentID = route.agentID; kind = route.chatType
            senders = route.policy.allowedSenderIDs.map(String.init).joined(separator: ", ")
            topic = route.policy.messageThreadID.map(String.init) ?? ""; attachments = route.policy.allowAttachments; enabled = route.enabled
        }
        .onChange(of: kind) { _, new in if new != .supergroup { topic = "" }; if new == .privateChat { senders = "" } }
    }
}
