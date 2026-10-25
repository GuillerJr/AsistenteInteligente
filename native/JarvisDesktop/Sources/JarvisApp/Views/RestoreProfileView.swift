import DesktopCore
import SwiftUI

struct RestoreProfileView: View {
    @Bindable var restored: RestoreProfileStore
    let profiles: AppProfileCoordinator
    let runtimeID: UUID
    let mayBegin: Bool
    @State private var profilePath = ""
    var body: some View {
        Form {
            Section("Usar un perfil restaurado") {
                Text("Primero revisa y autoriza su activación local. El perfil actual seguirá abierto hasta que pulses «Abrir perfil activado».")
                Text("La apertura crea una carpeta de trabajo privada nueva y vacía. No recupera archivos externos, cuentas, permisos de herramientas ni la asociación de la memoria con otra carpeta. Las tareas, rutas y skills restauradas siguen deshabilitadas.")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("Carpeta del perfil preparado", text: $profilePath)
                HStack {
                    Button("Revisar activación") { Task { await restored.review(path: profilePath) } }
                    Button("Inspeccionar admisión local") { Task { await restored.inspect(path: profilePath) } }
                }.disabled(!mayBegin || !restored.mayPrepare || !MaintenanceValidation.path(profilePath))
                Button("Actualizar estado") { Task { await restored.refresh() } }.disabled(restored.isRequesting)
                if restored.isRequesting { ProgressView().controlSize(.small) }
            }
            if let error = restored.errorMessage { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
            if restored.responseUnconfirmed {
                Section("Solicitud sin confirmar") {
                    Text("No se reenviará. Actualiza el estado para buscar la operación exacta; una inspección posterior no cambia su recibo.")
                    if restored.operation == nil { Button("No reintentar solicitud anterior") { restored.acknowledgeUnconfirmedLocally() } }
                }
            }
            if let review = restored.review {
                Section("Revisión exacta de activación") {
                    Text(review.path).textSelection(.enabled)
                    Text("Identidad: \(review.identity.device):\(review.identity.inode)").font(.caption.monospaced())
                    ForEach(review.resources) { resource in
                        HStack { Text(MaintenancePresentation.role(resource.role)); Spacer(); Text(resource.present ? "\(resource.bytes) bytes" : "Ausente").foregroundStyle(.secondary) }
                    }
                    Text("Se conserva el origen de la restauración. La integridad técnica no acredita autenticidad frente a otro programa de tu usuario.").font(.caption)
                    if restored.operation == nil {
                        HStack {
                            Button("Solicitar aprobación de activación") { Task { await restored.submit() } }
                                .disabled(!mayBegin || !restored.mayPrepare || review.expiresAt <= Date())
                            Button("Descartar revisión") { restored.discardReview() }.disabled(!restored.mayPrepare)
                        }
                    }
                }
            }
            if let operation = restored.operation {
                Section("Resultado de la operación") {
                    Text(MaintenancePresentation.phase(operation.phase)).font(.headline)
                    Text("Operación: \(operation.id)").font(.caption.monospaced()).textSelection(.enabled)
                    if let review = restored.review, let approval = restored.approval, review.matches(approval, operation: operation) {
                        Text("Esta aprobación admite localmente sólo el perfil revisado. Todavía no cambia el perfil abierto ni conecta ninguna cuenta.")
                        HStack {
                            Button("Autorizar activación local") { Task { await restored.decide(allow: true) } }.buttonStyle(.borderedProminent)
                            Button("No autorizar") { Task { await restored.decide(allow: false) } }
                        }.disabled(restored.isRequesting)
                    }
                    if operation.cleanupOwnerPending {
                        Text("La operación conserva recursos propios pendientes. No se puede abrir el destino mientras su limpieza siga sin confirmar.").foregroundStyle(.orange)
                        Button("Reintentar sólo la limpieza propia") { Task { await restored.cleanup() } }.disabled(restored.isRequesting)
                    } else if !operation.settled {
                        Button("Cancelar operación") { Task { await restored.cancel() } }.disabled(restored.isRequesting || operation.cancellationRequested)
                    }
                    if operation.phase == "publication_unknown" || operation.phase == "cleanup_pending" {
                        Text("Resultado sin confirmar. Limpiar o inspeccionar no convierte este recibo en éxito ni repite la activación.").foregroundStyle(.orange)
                    }
                    if restored.pollingPaused { Button("Consultar de nuevo") { restored.resumePolling() } }
                }
            }
            if let inspection = restored.inspection {
                Section("Inspección local de sólo lectura") {
                    Text(inspection.path).textSelection(.enabled)
                    Text(inspection.state == "local_present_valid" ? "Hay una admisión local válida en este momento." : "El perfil aún no tiene una admisión válida para abrirlo.")
                    Text("Esta inspección no acredita el ACK ni la durabilidad de una operación anterior. No modifica ningún recibo.").font(.caption)
                }
            }
            if restored.previousUnconfirmed != nil { Section { Text("El recibo anterior sigue sin confirmar, aunque esta nueva inspección permita elegir una apertura explícita.").foregroundStyle(.orange) } }
            if let target = restored.openTarget {
                Section("Abrir con un servicio nuevo") {
                    Text("Se detendrán voz y consultas del perfil actual. Jarvis sólo iniciará el nuevo después de confirmar el cierre limpio del servicio anterior. Los borradores duraderos se conservarán separados sólo en memoria durante esta sesión de la app. Todavía no hay un selector para recuperarlos. Los chats y borradores temporales no se trasladan.").font(.callout)
                    if target.followsUnconfirmedOperation { Text("Es una nueva elección de apertura basada en la inspección actual; no confirma el resultado anterior.").font(.caption).foregroundStyle(.orange) }
                    Button("Abrir perfil activado") { Task { await profiles.open(target, from: runtimeID) } }
                        .buttonStyle(.borderedProminent).disabled(!mayBegin || profiles.isOpening || restored.isRequesting)
                }
            }
            if let notice = profiles.notice { Section { Text(notice).font(.callout).textSelection(.enabled) } }
        }.formStyle(.grouped)
    }
}
