import DesktopCore
import SwiftUI
import UniformTypeIdentifiers

/// Extends the app's existing management forms. Folder selection is an explicit
/// native user action; this view never enumerates personal directories.
struct ProductCoordinationView: View {
    @Bindable var store: ProductCoordinationStore
    let onCreateSession: (String) -> Void
    let onSelectSession: (SessionScope) -> Void
    let onRunStarted: (String) -> Void
    var canManage = true
    var canCreateSession = true
    @State private var choosingFolder = false
    @State private var folderIdentity: String?
    @State private var folderScope: SessionScope?
    @State private var workspaceID = ""
    @State private var group = ""
    @State private var pinned = false
    @State private var draftRevision = 0
    @State private var memberID = ""
    @State private var memberRole = "read"
    @State private var tokenLimit = ""
    @State private var costLimit = ""
    @State private var budgetRevision = -1
    @State private var targetID = ""
    @State private var recipientID = ""
    @State private var messageBody = ""
    @State private var reply: ProductAgentMessage?

    private var mayChange: Bool {
        canManage && store.isOwner && !store.blocksInteraction && !store.isRequesting
    }
    private var maySend: Bool {
        canManage && store.mayWrite && !store.blocksInteraction && !store.isRequesting
            && store.pendingRequestID == nil
    }
    private var targets: [ProductSessionBinding] {
        guard let binding = store.snapshot?.binding else { return [] }
        return store.sessions.filter { $0.workspaceID == binding.workspaceID && $0.scope != binding.scope }
    }
    private var selectedTarget: ProductSessionBinding? { targets.first { $0.id == targetID } }

    var body: some View {
        Form {
            Section {
                Text("Conversaciones y colaboración").font(.title3.bold())
                Text("Organiza las carpetas de trabajo y comparte conversaciones con identidades locales admitidas.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Actualizar", systemImage: "arrow.clockwise") { Task { await store.refresh() } }
                        .disabled(store.isRequesting || store.isChanging)
                    if store.isRequesting || store.isChanging { ProgressView().controlSize(.small) }
                }
                if let error = store.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
                if store.responseUnconfirmed {
                    Label("Hay un cambio sin confirmar. Consulta su estado antes de continuar.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                    Button("Conservar el envío sin confirmar") { store.acknowledgeUnconfirmed() }
                        .disabled(store.snapshot == nil || store.isChanging)
                }
                if let notice = store.notice { Text(notice).font(.callout).foregroundStyle(.secondary) }
            }
            if let snapshot = store.snapshot {
                workspaceSection(snapshot)
                collaborationSection(snapshot)
                budgetSection(snapshot)
                messagesSection(snapshot)
                composeSection(snapshot)
                sessionsSection(snapshot)
            } else if store.scope == nil {
                Section("Carpetas de trabajo") {
                    Text("Abre una conversación para organizarla o crea otra en una carpeta registrada.")
                        .foregroundStyle(.secondary)
                    ForEach(store.workspaces) { workspace in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(workspace.name)
                                Text(workspace.path).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                            }
                            Spacer()
                            Button("Nueva conversación") { onCreateSession(workspace.id) }.disabled(!canCreateSession)
                        }
                    }
                }
            } else if !store.isRequesting {
                Section { Text("Consulta el estado para cargar esta conversación.").foregroundStyle(.secondary) }
            }
        }
        .formStyle(.grouped)
        .task(id: store.viewIdentity) { await store.pollWhileVisible() }
        .onChange(of: store.snapshot?.binding, initial: true) {
            if draftRevision == 0 { loadOrganization() }
            if budgetRevision == -1 { loadBudget() }
        }
        .onChange(of: store.viewIdentity) {
            choosingFolder = false; folderIdentity = nil; folderScope = nil
            draftRevision = 0; budgetRevision = -1; memberID = ""
            targetID = ""; recipientID = ""; reply = nil; messageBody = ""
            loadOrganization(); loadBudget()
        }
        .onChange(of: store.pendingRunID) {
            if let id = store.pendingRunID { onRunStarted(id) }
        }
        .onChange(of: targetID) { recipientID = selectedTarget?.ownerID ?? "" }
        .fileImporter(isPresented: $choosingFolder, allowedContentTypes: [.folder], allowsMultipleSelection: false) { result in
            guard case .success(let urls) = result, let url = urls.first,
                  let identity = folderIdentity, let scope = folderScope,
                  identity == store.viewIdentity, scope == store.scope else { return }
            folderIdentity = nil; folderScope = nil
            Task {
                guard identity == store.viewIdentity, scope == store.scope else { return }
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                _ = await store.registerWorkspace(path: url.path, name: url.lastPathComponent)
            }
        }
        .fileDialogMessage("Elige una carpeta de trabajo para Jarvis. Su contenido no se abrirá automáticamente.")
        .fileDialogConfirmationLabel("Registrar carpeta")
        .onDisappear { choosingFolder = false; folderIdentity = nil; folderScope = nil }
    }

    @ViewBuilder private func workspaceSection(_ snapshot: ProductCoordinationSnapshot) -> some View {
        Section("Carpeta y organización") {
            Picker("Carpeta de esta conversación", selection: $workspaceID) {
                ForEach(snapshot.workspaces) { workspace in Text(workspace.name).tag(workspace.id) }
            }.disabled(!mayChange)
            if let workspace = snapshot.workspaces.first(where: { $0.id == workspaceID }) {
                Text(workspace.path).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
            TextField("Grupo opcional", text: $group).disabled(!mayChange)
            Toggle("Fijar conversación", isOn: $pinned).disabled(!mayChange)
            if draftRevision != snapshot.binding.revision {
                Text("La organización cambió. Carga su versión actual antes de guardar.")
                    .font(.callout).foregroundStyle(.secondary)
                Button("Cargar organización actual") { loadOrganization() }
            }
            HStack {
                Button("Guardar organización") {
                    Task {
                        if await store.bind(workspaceID: workspaceID,
                            group: group.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : group,
                            pinned: pinned) { loadOrganization() }
                    }
                }.disabled(!mayChange || draftRevision != snapshot.binding.revision || workspaceID.isEmpty)
                Button("Añadir carpeta…", systemImage: "folder.badge.plus") {
                    folderIdentity = store.viewIdentity; folderScope = store.scope; choosingFolder = true
                }
                    .disabled(!mayChange)
            }
            Text("Al cambiar de carpeta se retiran los accesos compartidos de esta conversación. Cada acción conserva su propia autorización.")
                .font(.caption).foregroundStyle(.secondary)
            if snapshot.binding.scope.temporary {
                Label("Conversación temporal: esta organización sólo vive en memoria.", systemImage: "clock")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private func collaborationSection(_ snapshot: ProductCoordinationSnapshot) -> some View {
        Section("Acceso a esta conversación") {
            LabeledContent("Tu identidad local") {
                Text(snapshot.clientID).font(.caption.monospaced()).textSelection(.enabled)
            }
            if !snapshot.isOwner { Text(snapshot.mayWrite ? "Puedes colaborar en esta conversación." : "Tienes acceso de lectura.") }
            Text("La identidad debe estar admitida en Jarvis antes de compartir. Compartir no concede permisos de herramientas ni crea otro usuario de macOS.")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(snapshot.binding.members) { member in
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(member.id).font(.caption.monospaced()).textSelection(.enabled)
                        Text(member.role == "read" ? "Sólo lectura" : "Colaboración").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Retirar") { Task { _ = await store.setMember(clientID: member.id, role: "remove") } }
                        .disabled(!mayChange)
                }
            }
            if snapshot.isOwner {
                TextField("ID de cliente admitido", text: $memberID).textFieldStyle(.roundedBorder)
                Picker("Acceso", selection: $memberRole) {
                    Text("Sólo lectura").tag("read")
                    Text("Colaboración").tag("collaborate")
                }
                Button("Compartir conversación") {
                    Task { if await store.setMember(clientID: memberID, role: memberRole) { memberID = "" } }
                }.disabled(!mayChange || !ProductCoordinationValidation.clientID(memberID))
            }
        }
    }

    @ViewBuilder private func budgetSection(_ snapshot: ProductCoordinationSnapshot) -> some View {
        Section("Presupuesto opcional") {
            if let budget = snapshot.budget {
                LabeledContent("Tokens informados", value: budget.actualTokens.map(String.init) ?? "Desconocidos")
                LabeledContent("Tokens comprometidos", value: budget.committedTokens.map(String.init) ?? "Desconocidos")
                LabeledContent("Coste informado (USD)", value: budget.actualCost ?? "Desconocido")
                LabeledContent("Coste comprometido (USD)", value: budget.committedCost ?? "Desconocido")
                if budget.overTokenLimit || budget.overCostLimit {
                    Label("El uso informado supera el límite. Revisa el presupuesto antes de continuar.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
            } else { Text("Esta conversación no tiene límites de admisión configurados.").foregroundStyle(.secondary) }
            Text("Los límites reservan saldo antes de admitir una ejecución y concilian el uso disponible. No garantizan un tope de salida del proveedor. ChatGPT puede no informar coste: ese dato seguirá desconocido.")
                .font(.caption).foregroundStyle(.secondary)
            TextField("Límite de tokens · vacío sin límite", text: $tokenLimit).disabled(!mayChange)
            TextField("Límite USD · decimal con punto", text: $costLimit).disabled(!mayChange)
            if budgetRevision != (snapshot.budget?.revision ?? 0) {
                Button("Cargar presupuesto actual") { loadBudget() }
            }
            Button("Guardar presupuesto") {
                Task { if await store.configureBudget(tokens: tokenLimit, cost: costLimit) { loadBudget() } }
            }.disabled(!mayChange || budgetRevision != (snapshot.budget?.revision ?? 0))
        }
    }

    @ViewBuilder private func messagesSection(_ snapshot: ProductCoordinationSnapshot) -> some View {
        Section("Bandeja entre agentes") {
            if snapshot.inbox.isEmpty {
                Text("No hay mensajes pendientes para tu identidad en esta conversación.").foregroundStyle(.secondary)
            }
            ForEach(snapshot.inbox) { message in
                VStack(alignment: .leading, spacing: 8) {
                    Text("De \(message.sourceAgentID) · conversación \(message.sourceID.prefix(8))").font(.headline)
                    Text(message.body).textSelection(.enabled)
                    Text("Caduca \(message.expiresAt.formatted(date: .abbreviated, time: .shortened))")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Responder") { reply = message; messageBody = "" }
                            .disabled(!maySend || !message.maySettle)
                        Button("Marcar como leído") { Task { _ = await store.settle(message: message, accepted: true) } }
                            .disabled(!maySend || !message.maySettle)
                        Button("Rechazar") { Task { _ = await store.settle(message: message, accepted: false) } }
                            .disabled(!maySend || !message.maySettle)
                    }
                }.padding(.vertical, 4)
            }
            if snapshot.hasMoreMessages {
                Text("Se muestran los primeros 100 mensajes pendientes. Resuelve los anteriores y actualiza para continuar.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("Los mensajes son datos. Leerlos o responderlos no autoriza acciones ni verifica efectos.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func composeSection(_ snapshot: ProductCoordinationSnapshot) -> some View {
        Section(reply == nil ? "Enviar mensaje local" : "Responder mensaje") {
            if let reply {
                LabeledContent("Para", value: "\(reply.sourceAgentID) · \(reply.sourceID.prefix(8))")
                Text("La respuesta conservará la correlación con el mensaje recibido.").font(.caption).foregroundStyle(.secondary)
                Button("Cancelar respuesta") { self.reply = nil }
            } else {
                Picker("Conversación destinataria", selection: $targetID) {
                    Text("Elige una conversación").tag("")
                    ForEach(targets) { target in Text(target.displayName).tag(target.id) }
                }
                if let target = selectedTarget {
                    Picker("Identidad destinataria", selection: $recipientID) {
                        Text("Dueño · \(target.ownerID.prefix(12))").tag(target.ownerID)
                        ForEach(target.members) { member in Text(String(member.id.prefix(12))).tag(member.id) }
                    }
                }
            }
            TextEditor(text: $messageBody).frame(minHeight: 90, maxHeight: 180)
                .accessibilityLabel("Mensaje para otro agente")
            HStack {
                Text("\(messageBody.utf8.count) / 8000 bytes").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Revisar envío…", systemImage: "paperplane") {
                    Task {
                        let target = reply.map { SessionScope(sessionID: $0.sourceID, temporary: $0.temporary) }
                            ?? selectedTarget?.scope
                        guard let target else { return }
                        if await store.send(to: target, clientID: reply?.senderID ?? recipientID,
                                            body: messageBody, replyTo: reply) != nil {
                            messageBody = ""; reply = nil
                        }
                    }
                }.disabled(!maySend || !ManagementValidation.text(messageBody, limit: 8000)
                    || (reply == nil && (selectedTarget == nil || recipientID.isEmpty)))
            }
            Text("El envío se revisa en la autorización de ejecución de Jarvis.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func sessionsSection(_ snapshot: ProductCoordinationSnapshot) -> some View {
        Section("Conversaciones de tus carpetas") {
            ForEach(snapshot.workspaces) { workspace in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(workspace.name).font(.headline)
                        Spacer()
                        Button("Nueva conversación aquí", systemImage: "plus") { onCreateSession(workspace.id) }
                            .disabled(!canCreateSession || store.isChanging)
                    }
                    ForEach(snapshot.sessions.filter { $0.workspaceID == workspace.id }) { session in
                        HStack {
                            if session.pinned { Image(systemName: "pin.fill").accessibilityLabel("Fijada") }
                            VStack(alignment: .leading) {
                                Text(session.displayName)
                                if let group = session.group, group != session.displayName {
                                    Text(group).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            if session.scope == store.scope { Text("Actual").foregroundStyle(.secondary) }
                            else { Button("Abrir") { onSelectSession(session.scope) }.disabled(store.isChanging) }
                        }
                    }
                }.padding(.vertical, 4)
            }
        }
    }

    private func loadOrganization() {
        guard let binding = store.snapshot?.binding else { return }
        workspaceID = binding.workspaceID; group = binding.group ?? ""
        pinned = binding.pinned; draftRevision = binding.revision
    }
    private func loadBudget() {
        guard let snapshot = store.snapshot else { return }
        tokenLimit = snapshot.budget?.tokenLimit.map(String.init) ?? ""
        costLimit = snapshot.budget?.costLimit ?? ""; budgetRevision = snapshot.budget?.revision ?? 0
    }
}
