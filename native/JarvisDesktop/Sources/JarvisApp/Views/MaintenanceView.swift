import DesktopCore
import SwiftUI

struct MaintenanceView: View {
    @Bindable var maintenance: MaintenanceStore
    let navigation: SettingsOwnerNavigation
    let mayBegin: Bool
    @State private var backupParent = ""
    @State private var backupName = "jarvis-copia"
    @State private var backupPath = ""
    @State private var expectedHash = ""
    @State private var restoreParent = ""
    @State private var restoreName = "jarvis-restaurado"

    var body: some View {
        Form {
            Section("Mantenimiento local") {
                Text("Las copias incluyen datos privados de Jarvis. No incluyen archivos de la carpeta de trabajo ni credenciales del proveedor, tokens, comandos MCP ni el journal de mantenimiento. El texto que guardaste puede contener información sensible.")
                    .font(.callout).textSelection(.enabled)
                Text("Restaurar prepara un perfil nuevo y desarmado. No lo activa, inicia sesión ni arranca herramientas, Telegram o tareas.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Actualizar estado") { Task { await maintenance.refresh() } }
                    if maintenance.isRequesting || maintenance.isRefreshing { ProgressView().controlSize(.small) }
                    if let status = maintenance.status, !status.blockers.isEmpty { Text("El perfil tiene trabajo o efectos pendientes; la revisión puede quedar bloqueada.").font(.caption).foregroundStyle(.orange) }
                    if let status = maintenance.status { Text("Generación \(status.scope.generation) · \(status.previousCount) recibos anteriores").font(.caption).foregroundStyle(.secondary) }
                }
            }
            if let error = maintenance.errorMessage { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
            if maintenance.responseUnconfirmed {
                Section {
                    Text("Solicitud sin confirmar. No se repetirá. Actualiza el estado para buscar su operación exacta, aunque la tarjeta ya haya caducado.").foregroundStyle(.orange)
                    if maintenance.operation == nil {
                        Text("Si ya no aparece en este servicio, puedes cerrar sólo este aviso local. El resultado anterior seguirá sin confirmar; consulta los recibos anteriores.").font(.caption)
                        Button("No reintentar solicitud anterior") { maintenance.acknowledgeUnconfirmedLocally() }.disabled(maintenance.isRequesting)
                    }
                }
            }
            if maintenance.readAwaitingSettlement && maintenance.operation == nil {
                Section {
                    Text("Lectura sin confirmación. El servicio puede conservar recursos privados de recuperación; cerrar este panel no los libera. No se iniciará otra operación ni se repetirá la lectura.")
                        .foregroundStyle(.orange)
                }
            }
            if let acknowledgement = maintenance.localAcknowledgement { Section { Text(acknowledgement).font(.caption).foregroundStyle(.secondary) } }
            if let review = maintenance.review { reviewSection(review) }
            if let operation = maintenance.operation { operationSection(operation) }
            Section("Crear copia") {
                TextField("Carpeta padre absoluta", text: $backupParent)
                TextField("Nombre de carpeta nueva", text: $backupName)
                Button("Preparar revisión de copia") { Task { await maintenance.prepareBackup(parent: backupParent, name: backupName) } }
                    .disabled(!mayBegin || !maintenance.mayPrepare || !MaintenanceValidation.path(backupParent) || !MaintenanceValidation.name(backupName))
            }
            Section("Verificar copia") {
                TextField("Carpeta absoluta del respaldo", text: $backupPath)
                TextField("SHA-256 del manifiesto (opcional)", text: $expectedHash)
                Button("Verificar bytes y formato") { Task { await maintenance.verify(path: backupPath, expectedHash: expectedHash) } }
                    .disabled(!mayBegin || !maintenance.mayPrepare || !MaintenanceValidation.path(backupPath) || (!expectedHash.isEmpty && !ManagementValidation.hash(expectedHash)))
                if let verified = maintenance.verification {
                    if let path = maintenance.verificationPath { Text("Origen verificado: \(path)").font(.caption).textSelection(.enabled) }
                    Text("Copia verificada técnicamente. Esto no acredita la autenticidad ni la verdad del contenido.").font(.caption)
                    Text("Manifiesto: \(verified.manifestHash)").font(.caption.monospaced()).textSelection(.enabled)
                }
            }
            Section("Restaurar en perfil nuevo") {
                TextField("Carpeta padre absoluta", text: $restoreParent)
                TextField("Nombre de perfil nuevo", text: $restoreName)
                Button("Preparar revisión de restauración") { Task { await maintenance.prepareRestore(parent: restoreParent, name: restoreName) } }
                    .disabled(!mayBegin || !maintenance.mayPrepare || maintenance.verification == nil || !MaintenanceValidation.path(restoreParent) || !MaintenanceValidation.name(restoreName))
            }
            Section("Diagnóstico de sólo lectura") {
                Text("Comprueba permisos, formato, integridad y relaciones del perfil actual. Si hay un WAL compatible, inspecciona el último estado confirmado en una copia privada. No modifica ni repara el original.").font(.caption).foregroundStyle(.secondary)
                Button("Revisar perfil actual") { Task { await maintenance.runDoctor() } }.disabled(!mayBegin || !maintenance.mayPrepare)
                if let doctor = maintenance.doctor {
                    Text(doctor.status == "ok" ? "Comprobaciones acreditadas" : "Hay comprobaciones bloqueadas, parciales o no acreditadas")
                    ForEach(doctor.checks) { check in
                        HStack { Text(MaintenancePresentation.role(check.role)); Spacer(); Text(checkLabel(check.status)).foregroundStyle(check.status == "ok" ? Color.secondary : Color.orange) }
                    }
                }
            }
        }
        .formStyle(.grouped)
        .ownerDecisionScrolling(navigation, tab: "maintenance")
        .task { await maintenance.refresh() }
    }

    @ViewBuilder private func reviewSection(_ review: MaintenanceReview) -> some View {
        Section(review.kind == "backup.create" ? "Revisión de copia" : "Revisión de restauración") {
            Text("Destino nuevo: \(review.destinationParent)/\(review.destinationName)").textSelection(.enabled)
            Text("Identidad del padre: \(review.destinationIdentity.device):\(review.destinationIdentity.inode)").font(.caption.monospaced()).textSelection(.enabled)
            if review.kind == "restore.stage" {
                if let path = maintenance.verificationPath {
                    Text("Respaldo seleccionado: \(path)").textSelection(.enabled)
                }
                if let hash = review.metadata["manifest_sha256"].string {
                    Text("Manifiesto del respaldo: \(hash)").font(.caption.monospaced()).textSelection(.enabled)
                }
                Text("Perfil actual comprobado para conservarlo. Las cifras siguientes no son los tamaños del respaldo.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            MaintenanceReviewSourceRows(review: review)
            Text("No se sobrescribe un destino existente. Las autorizaciones, fuentes externas de skills, archivos de la carpeta de trabajo y credenciales quedan excluidos.").font(.caption)
            if review.kind == "restore.stage" { Text("Agentes y memoria conservan sus ámbitos; skills, tareas y rutas quedan deshabilitadas. La asociación con otra carpeta de trabajo necesita una revisión posterior.").font(.caption) }
            Text("Revisión exacta: \(review.digest)").font(.caption.monospaced()).textSelection(.enabled)
            if review.sources.contains(where: { $0.disposition == "unavailable" }) {
                Text("Hay un almacén aún no abierto; no se modificó para preparar el respaldo. Descarta esta revisión, consulta ese almacén desde Jarvis y vuelve a revisar.").font(.caption).foregroundStyle(.orange)
            }
            if maintenance.operation == nil {
                HStack {
                    Button("Solicitar aprobación para esta revisión") { Task { await maintenance.submitReview() } }
                        .disabled(!mayBegin || !maintenance.mayPrepare || review.expiresAt <= Date() || (review.kind == "backup.create" && review.sources.contains(where: { $0.disposition == "unavailable" })))
                    Button("Descartar revisión") { maintenance.discardReview() }.disabled(maintenance.responseUnconfirmed)
                }
            }
        }.id(review.id)
    }
    @ViewBuilder private func operationSection(_ operation: MaintenanceOperation) -> some View {
        Section("Operación de mantenimiento") {
            Text(MaintenancePresentation.phase(operation.phase)).font(.headline)
            Text("Operación: \(operation.id)").font(.caption.monospaced()).textSelection(.enabled)
            if let code = operation.errorCode {
                Text("Diagnóstico: \(code)").font(.caption.monospaced()).textSelection(.enabled)
            }
            if let notice = OwnerDecisionNotice(tab: "maintenance", operation: operation, reviewID: maintenance.review?.id, expiresAt: maintenance.review?.expiresAt, unconfirmed: maintenance.responseUnconfirmed) {
                OwnerDecisionStatusView(notice: notice)
            }
            if operation.workerPending { Text("El servicio mantiene la propiedad del trabajo hasta terminar su limpieza.").font(.caption) }
            if operation.cleanupOwnerPending {
                Text("El trabajo terminó, pero el servicio aún conserva recursos de recuperación. La conversación y el cambio de perfil seguirán bloqueados hasta confirmar su limpieza.").font(.caption).foregroundStyle(.orange)
                Button("Completar limpieza") { Task { await maintenance.cleanup() } }
                    .disabled(!maintenance.canCleanup)
            }
            if maintenance.canDecide, let approval = maintenance.approval, let review = maintenance.review, approval.matches(review, operation: operation) {
                OwnerDecisionValidityView(expiresAt: min(review.expiresAt, approval.expiresAt)) {
                Text("Esta decisión corresponde al dueño. Autoriza únicamente el destino, revisión y generación que se muestran arriba.").font(.callout)
                HStack {
                    Button(review.kind == "backup.create" ? "Autorizar crear esta copia" : "Autorizar preparar este perfil") { Task { await maintenance.decide(allow: true) } }
                        .buttonStyle(.borderedProminent).disabled(maintenance.isRequesting)
                    Button("No autorizar") { Task { await maintenance.decide(allow: false) } }.disabled(maintenance.isRequesting)
                }
                }
            }
            if !operation.settled {
                HStack {
                    Button("Cancelar operación") { Task { await maintenance.cancel() } }.disabled(maintenance.isRequesting || operation.cancellationRequested)
                    if maintenance.pollingPaused { Button("Consultar de nuevo") { maintenance.resumePolling() } }
                }
            }
            if operation.success {
                if operation.kind == "restore.stage" { Text("Perfil preparado y desarmado. La instalación activa no cambió.") }
                else if operation.kind == "backup.create" { Text("Copia publicada con bytes y manifiesto verificados; recibo durable confirmado.") }
            } else if operation.settled && ["publication_unknown", "cleanup_pending"].contains(operation.phase) {
                Text("Resultado sin confirmar; revisa el recibo. El efecto o la limpieza pueden seguir pendientes. No repitas la operación automáticamente.").foregroundStyle(.orange)
            }
            if let code = operation.errorCode, let message = MaintenancePresentation.operationError(code) {
                Text(code == "profile_cold_wal_cleanup_pending" && !operation.resourcesPending
                     ? "La limpieza quedó confirmada. La operación original falló y no se repitió."
                     : message).font(.caption).foregroundStyle(.orange)
            }
            if let receipt = operation.receipt, let hash = receipt.manifestHash { Text("Manifiesto: \(hash)").font(.caption.monospaced()).textSelection(.enabled) }
        }.id(operation.id)
    }
    private func checkLabel(_ value: String) -> String {
        ["ok":"Correcto", "absent":"Ausente", "uninitialized":"Sin inicializar", "unsafe":"Acceso inseguro", "corrupt":"Integridad inválida", "blocked":"Bloqueado", "unknown":"No acreditado"][value] ?? "No acreditado"
    }
}
