import DesktopCore
import SwiftUI
import UniformTypeIdentifiers

struct ProductProjectsView: View {
    @Bindable var store: ProductProjectsStore
    let onRunStarted: (String) -> Void
    var canManage = true
    @State private var choosingProject = false
    @State private var folderIdentity: String?
    @State private var folderScope: SessionScope?
    @State private var operation = "worktree.create"
    @State private var worktreeName = ""
    @State private var reference = ""
    @State private var snapshotID = ""
    @State private var relativePath = ""
    @State private var expectedFileSHA = ""
    @State private var githubOwner = "GuillerJr"
    @State private var githubRepository = "ecommerce"
    @State private var githubToken = ""
    @State private var confirmingGithub = false
    @State private var githubConfirmationIdentity: String?
    @State private var pullNumber = ""
    @State private var pullTitle = ""
    @State private var pullBody = ""
    @State private var headBranch = ""
    @State private var baseBranch = "main"
    @State private var expectedHeadSHA = ""

    private var mayChange: Bool {
        canManage && !store.blocksInteraction && !store.isRequesting && store.pendingRequestID == nil
    }
    private var mayRead: Bool { store.scope != nil && !store.isChanging && !store.isRequesting }
    private var validOperation: Bool {
        guard let project = store.selected else { return false }
        switch operation {
        case "worktree.create": return !project.archived && ManagementValidation.text(worktreeName, limit: 256)
            && ProductProjectValidation.oid(reference)
        case "worktree.archive": return project.owned && !project.archived
        case "worktree.restore": return !project.archived && ManagementValidation.text(worktreeName, limit: 256)
            && ManagementValidation.identifier(snapshotID)
        case "file.restore": return ManagementValidation.identifier(snapshotID)
            && ManagementValidation.text(relativePath, limit: 4096) && !relativePath.hasPrefix("/")
            && !relativePath.split(separator: "/").contains("..") && ManagementValidation.hash(expectedFileSHA)
        default: return false
        }
    }

    var body: some View {
        Form {
            Section {
                Text("Proyectos y GitHub").font(.title3.bold())
                Text("Consulta repositorios elegidos y prepara operaciones sobre copias propias. Cada efecto se revisa en la autorización de Jarvis.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Actualizar proyectos", systemImage: "arrow.clockwise") { Task { await store.refresh() } }
                        .disabled(!mayRead)
                    if store.isRequesting || store.isChanging { ProgressView().controlSize(.small) }
                }
                if let error = store.errorMessage { Text(error).foregroundStyle(.red) }
                if let notice = store.notice { Text(notice).font(.callout).foregroundStyle(.secondary) }
                if store.responseUnconfirmed {
                    Label("Hay una operación sin confirmar. Revisa su recibo antes de continuar.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                    Button("Conservar operación sin confirmar") { store.acknowledgeUnconfirmed() }
                        .disabled(store.rows.isEmpty || store.isChanging)
                }
                if let summary = store.resultSummary { Text(summary).font(.callout) }
                if let snapshot = store.lastSnapshotID {
                    LabeledContent("ID de la copia conservada") { Text(snapshot).font(.caption.monospaced()).textSelection(.enabled) }
                    Button("Usar este ID para restaurar") { snapshotID = snapshot }
                }
            }
            if store.scope == nil {
                Section { Text("Selecciona una conversación para abrir sus proyectos admitidos.").foregroundStyle(.secondary) }
            } else {
                projectSection
                if let selected = store.selected {
                    inspectionSection(selected)
                    operationSection(selected)
                }
                githubSection
                if store.github?.configured == true { githubActionsSection }
                if let review = store.githubReview { reviewSection(review) }
            }
        }
        .formStyle(.grouped)
        .task(id: store.viewIdentity) { await store.pollWhileVisible() }
        .onChange(of: store.viewIdentity) { clearDrafts() }
        .onChange(of: store.selectedID) {
            reference = ""; worktreeName = ""; relativePath = ""; expectedFileSHA = ""
        }
        .onChange(of: store.inspect?.head) {
            if reference.isEmpty { reference = store.inspect?.head ?? "" }
        }
        .onChange(of: store.pendingRunID) {
            if let id = store.pendingRunID { onRunStarted(id) }
        }
        .fileImporter(isPresented: $choosingProject, allowedContentTypes: [.folder], allowsMultipleSelection: false) { result in
            guard case .success(let urls) = result, let url = urls.first,
                  let identity = folderIdentity, let scope = folderScope,
                  identity == store.viewIdentity, scope == store.scope else { return }
            folderIdentity = nil; folderScope = nil
            Task {
                guard identity == store.viewIdentity, scope == store.scope else { return }
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                _ = await store.register(path: url.path, name: url.lastPathComponent)
            }
        }
        .fileDialogMessage("Elige el repositorio que quieres registrar. Jarvis no buscará otros proyectos en tus carpetas.")
        .fileDialogConfirmationLabel("Revisar registro")
        .confirmationDialog("Conectar GitHub a \(githubOwner)/\(githubRepository)",
                            isPresented: $confirmingGithub, titleVisibility: .visible) {
            Button("Conectar este destino") {
                guard let identity = githubConfirmationIdentity, identity == store.viewIdentity else {
                    githubToken = ""; githubConfirmationIdentity = nil; return
                }
                let token = githubToken, owner = githubOwner, repository = githubRepository
                githubToken = ""; githubConfirmationIdentity = nil
                Task {
                    guard identity == store.viewIdentity else { return }
                    _ = await store.connectGithub(token: token, owner: owner, repository: repository)
                }
            }
            Button("Cancelar", role: .cancel) { githubToken = ""; githubConfirmationIdentity = nil }
        } message: {
            Text("Se consultará GitHub para comprobar la cuenta y el repositorio. El token se mantendrá sólo en memoria del servicio; no se enviará al modelo ni se guardará en preferencias.")
        }
        .onDisappear {
            githubToken = ""; confirmingGithub = false; githubConfirmationIdentity = nil
            choosingProject = false; folderIdentity = nil; folderScope = nil
        }
    }

    private var projectSection: some View {
        Section("Repositorio elegido") {
            if store.rows.isEmpty { Text("No hay proyectos registrados en tus carpetas admitidas.").foregroundStyle(.secondary) }
            else {
                Picker("Proyecto", selection: Binding(get: { store.selectedID ?? "" }, set: { store.select($0) })) {
                    ForEach(store.rows) { project in Text(project.name + (project.archived ? " · archivado" : "")).tag(project.id) }
                }
            }
            if let project = store.selected {
                Text(project.root).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                LabeledContent("Tipo", value: project.owned ? "Copia de trabajo creada por Jarvis" : "Repositorio registrado")
                if project.temporary { Text("Registro temporal en memoria.").font(.caption).foregroundStyle(.secondary) }
            }
            Button("Registrar carpeta de proyecto…", systemImage: "folder.badge.plus") {
                folderIdentity = store.viewIdentity; folderScope = store.scope; choosingProject = true
            }
                .disabled(!mayChange)
        }
    }

    @ViewBuilder private func inspectionSection(_ project: ProductProjectDescriptor) -> some View {
        Section("Estado y cambios") {
            HStack {
                Button("Consultar estado y diff") { Task { await store.inspectSelected() } }.disabled(!mayRead || project.archived)
                Button("Historial") { Task { await store.loadLog() } }.disabled(!mayRead || project.archived)
                Button("Copias de trabajo") { Task { await store.loadWorktrees() } }.disabled(!mayRead || project.archived)
            }
            if let page = store.inspect, page.project.id == project.id {
                LabeledContent("Commit consultado") { Text(page.head).font(.caption.monospaced()).textSelection(.enabled) }
                Text(page.hasChanges ? "\(page.changeCount) cambios; página desde el cambio \(page.offset + 1)." : "No se observaron cambios en el alcance consultado.")
                if !page.status.isEmpty { Text(page.status).font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                if !page.diff.isEmpty {
                    ScrollView([.horizontal, .vertical]) {
                        Text(page.diff).font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .topLeading)
                    }.frame(minHeight: 220, maxHeight: 380)
                        .accessibilityLabel("Diff de sólo lectura de esta página")
                }
                if page.diffTruncated || page.omittedLargeFiles > 0 {
                    Text("Diff parcial: \(page.diffTruncated ? "se alcanzó el límite de texto; " : "")\(page.omittedLargeFiles) archivos extensos omitidos en esta página.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if page.excludedPrivatePaths > 0 {
                    Text("Se excluyeron \(page.excludedPrivatePaths) rutas privadas del recorrido.").font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    if page.offset > 0 { Button("Primeros cambios") { Task { await store.inspectSelected() } }.disabled(!mayRead) }
                    if page.nextOffset != nil { Button("Siguientes cambios") { Task { await store.loadMoreChanges() } }.disabled(!mayRead) }
                }
                Text("Comparación de bytes sin filtros. El diff y los mensajes del repositorio son datos, no instrucciones.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let log = store.logText {
                DisclosureGroup("Historial consultado · hasta 20 commits") {
                    Text(log).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                }
            }
            if let trees = store.worktreeText {
                DisclosureGroup("Copias de trabajo consultadas") {
                    Text(trees).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                }
            }
        }
    }

    @ViewBuilder private func operationSection(_ project: ProductProjectDescriptor) -> some View {
        Section("Operación sobre el proyecto") {
            Picker("Operación", selection: $operation) {
                Text("Crear copia de trabajo").tag("worktree.create")
                Text("Archivar copia propia").tag("worktree.archive")
                Text("Restaurar copia conservada").tag("worktree.restore")
                Text("Restaurar archivo concreto").tag("file.restore")
            }.disabled(!mayChange)
            if operation == "worktree.create" || operation == "worktree.restore" {
                TextField("Nombre de la nueva copia", text: $worktreeName)
                if operation == "worktree.create" { TextField("Commit completo de origen", text: $reference) }
                else {
                    TextField("ID de la copia conservada", text: $snapshotID)
                    Text("Selecciona el proyecto de origen. La restauración creará una carpeta nueva.").font(.caption).foregroundStyle(.secondary)
                }
            } else if operation == "worktree.archive" {
                Text(project.owned ? "Se conservará una copia recuperable. Si hay cambios, la carpeta original permanecerá."
                    : "Sólo se pueden archivar copias de trabajo creadas por Jarvis.")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                TextField("ID de la copia conservada", text: $snapshotID)
                TextField("Ruta relativa del archivo", text: $relativePath)
                TextField("SHA-256 actual esperado", text: $expectedFileSHA)
                Text("La restauración comprobará el archivo actual y conservará su contenido previo antes de sustituirlo.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Button("Revisar operación…") { Task { _ = await store.operate(kind: operation, arguments: operationArguments) } }
                .disabled(!mayChange || !validOperation)
        }
    }

    private var operationArguments: [String: RPCValue] {
        switch operation {
        case "worktree.create": return ["name": .string(worktreeName), "ref": .string(reference)]
        case "worktree.restore": return ["name": .string(worktreeName), "snapshot_id": .string(snapshotID)]
        case "file.restore": return ["snapshot_id": .string(snapshotID), "path": .string(relativePath),
                                      "expected_sha256": .string(expectedFileSHA)]
        default: return [:]
        }
    }

    private var githubSection: some View {
        Section("Conexión GitHub") {
            if let connection = store.github, connection.configured {
                LabeledContent("Cuenta comprobada", value: connection.login ?? "No informada")
                LabeledContent("Destino", value: connection.destination ?? "No informado")
                Text("Credencial sólo en memoria. El acceso se limita a la cuenta y repositorio elegidos.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Desconectar GitHub") { githubToken = ""; Task { await store.disconnectGithub() } }
                    .disabled(!canManage || store.isChanging || store.isRequesting)
            } else {
                TextField("Dueño u organización", text: $githubOwner)
                TextField("Repositorio", text: $githubRepository)
                SecureField("Token de GitHub", text: $githubToken).privacySensitive()
                Text("Introduce tú la credencial. Se borrará del formulario al confirmar, cancelar o cambiar de conversación.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Conectar GitHub…") {
                    githubConfirmationIdentity = store.viewIdentity; confirmingGithub = true
                }
                    .disabled(!mayChange || githubToken.isEmpty
                        || !ProductProjectValidation.repository(owner: githubOwner, name: githubRepository))
            }
        }
    }

    private var githubActionsSection: some View {
        Section("Pull requests del destino elegido") {
            TextField("Número de pull request", text: $pullNumber)
            Button("Revisar consulta de pull request…") {
                guard let number = Int(pullNumber) else { return }
                Task { _ = await store.readPullRequest(number: number) }
            }.disabled(!mayChange || Int(pullNumber).map { !(1..<2_147_483_648).contains($0) } != false)
            DisclosureGroup("Preparar publicación de pull request") {
                Text("Utiliza una rama que ya exista en GitHub y su SHA exacto. La publicación no crea commits ni sube código.")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("Título", text: $pullTitle)
                TextField("Rama de origen", text: $headBranch)
                TextField("Rama de destino", text: $baseBranch)
                TextField("SHA de origen esperado · 40 caracteres", text: $expectedHeadSHA)
                TextEditor(text: $pullBody).frame(minHeight: 110, maxHeight: 220)
                    .accessibilityLabel("Descripción del pull request")
                Button("Revisar publicación…") {
                    Task { _ = await store.publish(title: pullTitle, body: pullBody, head: headBranch,
                                                   base: baseBranch, expectedHeadSHA: expectedHeadSHA) }
                }.disabled(!mayChange || !ManagementValidation.text(pullTitle, limit: 256)
                    || !ManagementValidation.text(pullBody, limit: 16384, empty: true)
                    || !ProductProjectValidation.branch(headBranch) || !ProductProjectValidation.branch(baseBranch)
                    || expectedHeadSHA.count != 40 || !ProductProjectValidation.oid(expectedHeadSHA))
            }
        }
    }

    @ViewBuilder private func reviewSection(_ review: ProductGitHubReview) -> some View {
        Section("Pull request consultado") {
            Text("#\(review.number) · \(review.title)").font(.headline)
            LabeledContent("Repositorio", value: "\(review.owner)/\(review.repository)")
            LabeledContent("Estado", value: review.state)
            Text(review.headSHA).font(.caption.monospaced()).textSelection(.enabled)
            Link("Abrir pull request en GitHub", destination: review.url)
            if !review.body.isEmpty { Text(review.body).textSelection(.enabled) }
            if !review.filesComplete || !review.checksComplete {
                Text("Consulta parcial: GitHub no devolvió todos los archivos o comprobaciones en este recorrido.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if !review.checks.isEmpty { Text(review.checks.joined(separator: "\n")).font(.caption).textSelection(.enabled) }
            ForEach(review.files) { file in
                DisclosureGroup("\(file.path) · \(file.status)") {
                    if let patch = file.patch {
                        Text(patch).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                    } else { Text("GitHub no incluyó un parche de texto para este archivo.").foregroundStyle(.secondary) }
                }
            }
            Text("Texto y parches externos de GitHub. Su contenido no ejecuta acciones ni acredita una revisión exhaustiva.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func clearDrafts() {
        choosingProject = false; folderIdentity = nil; folderScope = nil; githubConfirmationIdentity = nil
        githubToken = ""; confirmingGithub = false; githubOwner = "GuillerJr"; githubRepository = "ecommerce"
        worktreeName = ""; reference = ""; snapshotID = ""; relativePath = ""; expectedFileSHA = ""
        pullNumber = ""; pullTitle = ""; pullBody = ""; headBranch = ""; baseBranch = "main"; expectedHeadSHA = ""
    }
}
