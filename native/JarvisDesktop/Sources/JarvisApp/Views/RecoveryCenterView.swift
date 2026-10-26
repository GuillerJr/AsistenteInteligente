import DesktopCore
import SwiftUI

struct RecoveryCenterView: View {
    @Bindable var recovery: RecoveryExtensionStore
    let workspacePath: String?
    let profilePath: String?
    let verificationID: String?
    let mayBegin: Bool
    @State private var entries: [RecoveryWorkspaceEntry] = []
    @State private var entryPath = ""
    @State private var entryRole = "workspace_file"
    @State private var entryKind = "file"
    @State private var parent = ""
    @State private var name = "jarvis-archivos"
    @State private var archive = ""
    @State private var expectedHash = ""
    @State private var repairRole = "memory"
    @State private var repairKind = "memory_fts_rebuild"
    @State private var artifact = ""
    @State private var installation = ""
    @State private var expectedCurrent = ""
    private var enabled: Bool { mayBegin && recovery.mayPrepare }
    private var validTarget: Bool { MaintenanceValidation.path(parent) && MaintenanceValidation.name(name) }
    var body: some View {
        Form {
            Section("Centro de recuperación") {
                Text("Archivos seleccionados, copias reparadas y versiones locales de desarrollo. Cada efecto requiere revisar su origen, destino e identidad y una decisión tuya.")
                HStack {
                    Button("Actualizar estado") { Task { await recovery.refresh() } }.disabled(recovery.isRequesting)
                    if recovery.isRequesting || recovery.isRefreshing { ProgressView().controlSize(.small) }
                }
            }
            if let error = recovery.errorMessage { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
            if recovery.readAwaitingSettlement { Section { Text("La lectura no tiene cierre confirmado. Consulta el estado o completa su limpieza; no se repetirá.").foregroundStyle(.orange) } }
            if recovery.responseUnconfirmed {
                Section("Solicitud sin confirmar") {
                    Text("El efecto anterior no se reenviará. La ausencia de un registro no demuestra que se deshiciera.").foregroundStyle(.orange)
                    if recovery.operation == nil { Button("Cerrar sólo la espera local") { recovery.acknowledgeUnconfirmedLocally() }.disabled(recovery.isRequesting) }
                }
            }
            if let review = recovery.preview ?? recovery.effectReview { reviewDetails(review) }
            if let review = recovery.preview {
                Section {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        VStack(alignment: .leading) {
                            Text(review.expiresAt > context.date ? "La revisión caduca en \(max(0, Int(review.expiresAt.timeIntervalSince(context.date)))) segundos." : "La revisión caducó. Descártala y prepara otra antes de solicitar permiso.").font(.caption).foregroundStyle(.secondary)
                            Button("Solicitar aprobación de esta revisión") { Task { await recovery.submit() } }.buttonStyle(.borderedProminent).disabled(!mayBegin || !recovery.maySubmit || review.expiresAt <= context.date)
                        }
                    }
                    Button("Descartar revisión") { recovery.discardPreview() }.disabled(!recovery.mayPrepare)
                }
            }
            if let operation = recovery.operation { operationDetails(operation) }
            if let cleanup = recovery.cleanupOperation {
                Section("Custodia pendiente") {
                    Text("La operación \(cleanup.id) conserva recursos propios. La limpieza no repite la copia, reparación ni publicación.").font(.caption)
                    Button("Reintentar sólo la limpieza propia") { Task { await recovery.cleanup() } }.disabled(recovery.isRequesting || recovery.isRefreshing)
                }
            }
            Section("Destino nuevo de archivos o copia reparada") {
                TextField("Ruta absoluta de la carpeta contenedora", text: $parent)
                TextField("Nombre de carpeta nueva", text: $name)
                Text("La carpeta contenedora ya debe existir; Jarvis creará dentro una carpeta nueva con este nombre. No sustituirá otra carpeta.").font(.caption).foregroundStyle(.secondary)
            }.disabled(!recovery.mayPrepare)
            Section("Copiar archivos seleccionados") {
                if let workspacePath { Text("Carpeta de trabajo actual: \(workspacePath)").font(.caption).textSelection(.enabled) }
                else { Text("Selecciona primero una carpeta de trabajo en la ventana principal.").foregroundStyle(.orange) }
                Text("Añade rutas relativas exactas. Un directorio conserva sólo ese directorio; sus hijos no se incluyen automáticamente. Los archivos pueden contener información sensible. Credenciales, estado privado, enlaces y temporales conocidos se rechazan.").font(.caption)
                TextField("Ruta relativa de un archivo o directorio", text: $entryPath)
                HStack {
                    Picker("Tipo", selection: $entryKind) { Text("Archivo").tag("file"); Text("Directorio sin hijos").tag("directory") }
                    Picker("Contenido", selection: $entryRole) { Text("Archivo de trabajo").tag("workspace_file"); Text("Artefacto").tag("artifact") }
                    Button("Añadir") {
                        if let entry = try? RecoveryWorkspaceEntry(path: entryPath, role: entryRole, kind: entryKind), !entries.contains(where: { $0.path == entry.path }) { entries.append(entry); entryPath = "" }
                    }.disabled(!RecoveryExtensionValidation.relativePath(entryPath) || entries.count >= 256)
                }
                ForEach(entries) { entry in
                    HStack { Text(entry.path).textSelection(.enabled); Spacer(); Text(entry.kind == "directory" ? "Sólo directorio" : entry.role == "artifact" ? "Artefacto" : "Archivo").font(.caption); Button("Quitar") { entries.removeAll { $0.id == entry.id } } }
                }
                Button("Revisar copia de archivos seleccionados") { Task { await recovery.prepareWorkspaceBackup(entries: entries, parent: parent, name: name) } }.disabled(!enabled || !validTarget || workspacePath == nil || entries.isEmpty)
            }.disabled(!recovery.mayPrepare)
            Section("Restaurar archivo portátil en carpeta nueva") {
                TextField("Ruta absoluta de la carpeta portátil", text: $archive)
                TextField("SHA-256 esperado del manifiesto (opcional)", text: $expectedHash)
                Text("Se verifica el inventario y cada archivo. El destino se queda sin seleccionar: no se ejecutan artefactos, no se cambia la asociación de memoria y no se activa un perfil.").font(.caption)
                Button("Revisar restauración de archivos") { Task { await recovery.prepareWorkspaceRestore(archive: archive, hash: expectedHash, parent: parent, name: name) } }.disabled(!enabled || !validTarget || !MaintenanceValidation.path(archive) || !expectedHash.isEmpty && !ManagementValidation.hash(expectedHash))
            }.disabled(!recovery.mayPrepare)
            Section("Preparar copia reparada") {
                Picker("Base de datos", selection: $repairRole) { Text("Memoria").tag("memory"); Text("Estado").tag("state"); Text("Tareas").tag("schedules") }
                Picker("Reparación", selection: $repairKind) { Text("Reconstruir índice de memoria").tag("memory_fts_rebuild"); Text("Conservar prefijo WAL confirmado").tag("wal_valid_prefix") }
                Text("El original se conserva. La reparación se publica como un respaldo nuevo que después debes verificar y restaurar desde Mantenimiento. Un prefijo WAL puede perder transacciones; la revisión indicará los bytes descartados.").font(.caption)
                Button("Revisar candidato de reparación") { Task { await recovery.prepareRepair(role: repairRole, kind: repairKind, parent: parent, name: name) } }.disabled(!enabled || !validTarget || repairKind == "memory_fts_rebuild" && repairRole != "memory")
            }.disabled(!recovery.mayPrepare)
            releaseControls
            if let notice = recovery.installationNotice { Section("Lectura de la instalación") { Text(notice).textSelection(.enabled) } }
            if let plan = recovery.launchPlan {
                Section("Plan de arranque verificado, sin ejecutar") {
                    Text("Versión: \(plan.versionKey)")
                    Text("App: \(plan.nativeExecutable)").font(.caption).textSelection(.enabled)
                    Text("Python: \(plan.pythonSource)").font(.caption).textSelection(.enabled)
                    Text("CURRENT: \(plan.currentSHA)").font(.caption.monospaced()).textSelection(.enabled)
                    Text("Ambas entradas pertenecen a la misma versión. Necesitan Python externo y el cliente oficial de Codex. Esta consulta no inicia la app ni activa un perfil.").font(.caption)
                }
            }
        }.formStyle(.grouped).task { await recovery.refresh() }
    }
    private var releaseControls: some View {
        Section("Versiones locales de desarrollo") {
            TextField("Carpeta absoluta del artefacto de versión", text: $artifact)
            TextField("Carpeta absoluta de instalación local", text: $installation)
            TextField("SHA-256 de CURRENT (opcional para consultar plan)", text: $expectedCurrent)
            Text("Primero verifica en Mantenimiento un respaldo que aún coincida con el perfil actual. No se crea una copia oculta. Elegir una versión no toca los datos del perfil, no la ejecuta y no sustituye /Applications/Jarvis.app.").font(.caption)
            if verificationID == nil { Text("Falta verificar el respaldo del perfil actual en Mantenimiento.").font(.caption).foregroundStyle(.orange) }
            HStack {
                Button("Revisar actualización local") { if let verificationID { Task { await recovery.prepareRelease(artifact: artifact, installation: installation, verificationID: verificationID) } } }.disabled(!enabled || verificationID == nil || !MaintenanceValidation.path(artifact) || !MaintenanceValidation.path(installation))
                Button("Revisar rollback local") { if let verificationID { Task { await recovery.prepareRollback(installation: installation, verificationID: verificationID) } } }.disabled(!enabled || verificationID == nil || !MaintenanceValidation.path(installation))
            }
            HStack {
                Button("Inspeccionar selección local") { Task { await recovery.inspectRelease(installation: installation) } }.disabled(!enabled || !MaintenanceValidation.path(installation))
                Button("Verificar plan de arranque") { if let verificationID { Task { await recovery.resolveLaunch(installation: installation, verificationID: verificationID, expectedCurrent: expectedCurrent) } } }.disabled(!enabled || verificationID == nil || !MaintenanceValidation.path(installation) || !expectedCurrent.isEmpty && !ManagementValidation.hash(expectedCurrent))
            }
        }.disabled(!recovery.mayPrepare)
    }
    @ViewBuilder private func reviewDetails(_ review: RecoveryExtensionReview) -> some View {
        Section("Revisión exacta antes de solicitar permiso") {
            if let profilePath { Text("Perfil actual: \(profilePath)").font(.caption).textSelection(.enabled) }
            Text("Identidad del perfil: \(review.scope.profileID) · generación \(review.scope.generation)").font(.caption).textSelection(.enabled)
            if let detail = review.workspace {
                Text("Origen: \(detail.sourcePath)").textSelection(.enabled)
                Text("Destino nuevo: \(detail.destinationParent)/\(detail.destinationName)").textSelection(.enabled)
                Text("\(detail.summary.files) archivos · \(detail.summary.artifacts) artefactos · \(detail.summary.directories) directorios · \(detail.summary.bytes) bytes")
                ForEach(detail.selection, id: \.reviewedID) { entry in Text("\(entry.path) — \(entry.kind == "directory" ? "sólo directorio" : entry.role == "artifact" ? "artefacto" : "archivo")").font(.caption).textSelection(.enabled) }
                Text("Manifiesto: \(detail.summary.manifestSHA)").font(.caption.monospaced()).textSelection(.enabled)
                Text("Sólo esta selección; sin recursión, credenciales ni activación. Los contenidos seleccionados pueden contener secretos. Los hashes no acreditan quién creó el archivo ni la verdad de su contenido.").font(.caption).foregroundStyle(.orange)
            } else if let detail = review.repair {
                Text("Base: \(MaintenancePresentation.role(detail.role))")
                Text("Destino de respaldo nuevo: \(detail.destinationParent)/\(detail.destinationName)").textSelection(.enabled)
                if let bytes = detail.discardedBytes { Text("Se descartarán \(bytes) bytes, en \(detail.discardedFrameSlots ?? 0) espacios de trama. Se desconoce cuántas transacciones confirmadas pueden perderse.").foregroundStyle(.orange) }
                else { Text("Se reconstruirá sólo el índice derivado: \(detail.derivedRows ?? 0) filas. Se conservan las tablas base.") }
                Text("Imagen: \(detail.imageSHA) · \(detail.imageBytes) bytes").font(.caption.monospaced()).textSelection(.enabled)
                Text("Huella del origen: \(detail.sourceFingerprint)").font(.caption.monospaced()).textSelection(.enabled)
                Text("El original no se modifica. La copia reparada necesita una restauración y activación independientes.").font(.caption)
            } else if let detail = review.release {
                Text("Artefacto: \(detail.artifactPath)").textSelection(.enabled)
                Text("Instalación local: \(detail.installRoot)").textSelection(.enabled)
                Text("Versión propuesta: \(detail.versionKey) · \(detail.files) archivos · \(detail.bytes) bytes")
                Text("Manifiesto: \(detail.manifestSHA)").font(.caption.monospaced()).textSelection(.enabled)
                Text("Respaldo verificado: \(detail.backupPath)").font(.caption).textSelection(.enabled)
                Text("SHA-256 del respaldo: \(detail.backupSHA)").font(.caption.monospaced()).textSelection(.enabled)
                Text("No acredita autenticidad, no inicia la versión ni altera el perfil. Python y Codex siguen siendo dependencias externas.").font(.caption).foregroundStyle(.orange)
                DisclosureGroup("Evidencia de plataforma") { Text(detail.signatureEvidence).font(.caption.monospaced()).textSelection(.enabled) }
            }
            Text("Revisión: \(review.digest)").font(.caption.monospaced()).textSelection(.enabled)
        }
    }
    @ViewBuilder private func operationDetails(_ operation: MaintenanceOperation) -> some View {
        Section("Operación de recuperación") {
            Text(MaintenancePresentation.phase(operation.phase)).font(.headline)
            if let review = recovery.effectReview, let ticket = recovery.approval, review.matches(ticket, operation: operation) {
                Text("La decisión aplica únicamente a la revisión y al destino mostrados arriba. No crea un permiso reutilizable.")
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    VStack(alignment: .leading) {
                        Text(min(review.expiresAt, ticket.expiresAt) > context.date ? "Esta decisión caduca en \(max(0, Int(min(review.expiresAt, ticket.expiresAt).timeIntervalSince(context.date)))) segundos." : "La decisión caducó. Consulta el estado antes de continuar.").font(.caption).foregroundStyle(.secondary)
                        HStack {
                            Button("Autorizar esta operación") { Task { await recovery.decide(allow: true, ticket: ticket) } }.buttonStyle(.borderedProminent)
                            Button("No autorizar") { Task { await recovery.decide(allow: false, ticket: ticket) } }
                        }.disabled(!recovery.canDecide || !review.matches(ticket, operation: operation, now: context.date))
                    }
                }
            }
            if operation.success {
                if RecoveryExtensionValidation.reads.contains(operation.kind) { Text("Lectura local completada. No ejecutó ni autorizó un efecto.") }
                else { Text("Operación confirmada con recibo durable. El origen se conserva; no se ejecutó ni se activó un perfil.") }
            }
            if let code = operation.errorCode {
                Text(RecoveryExtensionPresentation.code(code)).foregroundStyle(.orange)
                Text("Diagnóstico: \(code)").font(.caption.monospaced()).textSelection(.enabled)
            }
            if operation.phase == "publication_unknown" || operation.receipt?.status == "unknown" { Text("El efecto quedó sin confirmar. No se repetirá automáticamente; cerrar recursos no convierte ese recibo en éxito.").foregroundStyle(.orange) }
            if let hash = operation.receipt?.manifestHash { Text("Manifiesto del recibo: \(hash)").font(.caption.monospaced()).textSelection(.enabled) }
            if !operation.settled { Button("Cancelar operación") { Task { await recovery.cancel() } }.disabled(recovery.isRequesting || operation.cancellationRequested) }
            if recovery.pollingPaused { Button("Retomar consulta del estado") { recovery.resumePolling() } }
            Text(operation.id).font(.caption.monospaced()).textSelection(.enabled)
        }
    }
}
