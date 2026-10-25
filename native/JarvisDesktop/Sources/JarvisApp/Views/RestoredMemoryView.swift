import DesktopCore
import SwiftUI

struct RestoredMemoryView: View {
    @Bindable var restoration: RestoredMemoryStore
    let agents: [AgentDescriptor]
    let mayBegin: Bool
    var body: some View {
        Form {
            Section("Memoria conservada del perfil restaurado") {
                Text("Elige un agente y un ámbito conservado para copiar sus notas a la carpeta de trabajo actual, que debe estar vacía de memoria para ese agente. El origen se conserva.")
                Text("No se trasladan cuentas, permisos ni archivos. Las etiquetas de origen no se verifican y no se abren sus recursos. Esta operación no consulta ningún modelo.").font(.caption).foregroundStyle(.secondary)
                Picker("Agente", selection: Binding(get: { restoration.agentID }, set: { restoration.selectAgent($0) })) {
                    Text("Elige un agente").tag("")
                    ForEach(agents) { agent in Text(agent.name).tag(agent.id) }
                }.disabled(!restoration.mayPrepare)
                HStack {
                    Button("Buscar ámbitos conservados") { Task { await restoration.loadInventory() } }
                        .disabled(!mayBegin || !restoration.mayPrepare || !agents.contains(where: { $0.id == restoration.agentID }))
                    Button("Actualizar estado") { Task { await restoration.refresh() } }.disabled(restoration.isRequesting)
                }
                if restoration.isRequesting || restoration.isRefreshing { ProgressView().controlSize(.small) }
            }
            if restoration.readAwaitingSettlement { Section { Text("La consulta local todavía no tiene cierre confirmado. Actualiza el estado; no se repetirá la solicitud.").foregroundStyle(.orange) } }
            if let error = restoration.errorMessage { Section { Text(error).foregroundStyle(.red) } }
            if let inventory = restoration.inventory {
                Section("Ámbitos disponibles") {
                    Text("Destino: \(inventory.workspacePath)").textSelection(.enabled)
                    Text(inventory.destinationEmpty ? "El ámbito de destino está vacío." : "El destino ya contiene memoria: no se puede copiar aquí.").foregroundStyle(inventory.destinationEmpty ? Color.secondary : Color.orange)
                    if restoration.sources.isEmpty { Text("No se encontraron ámbitos duraderos con notas para este agente.") }
                    ForEach(Array(restoration.sources.enumerated()), id: \.element.id) { index, source in
                        Button {
                            restoration.selectSource(source.id)
                        } label: {
                            HStack {
                                Image(systemName: restoration.selectedSourceID == source.id ? "largecircle.fill.circle" : "circle")
                                VStack(alignment: .leading) {
                                    Text("Ámbito conservado \(index + 1)")
                                    Text("\(source.documents) notas · \(source.bytes) bytes").font(.caption).foregroundStyle(.secondary)
                                    if !source.eligible { Text("Supera los límites de esta copia.").font(.caption).foregroundStyle(.orange) }
                                }
                            }
                        }.buttonStyle(.plain).disabled(!source.eligible || !restoration.mayPrepare)
                    }
                    if inventory.nextCursor != nil {
                        Button("Cargar más ámbitos") { Task { await restoration.loadMore() } }.disabled(!restoration.mayPrepare || !restoration.inventoryIsCurrent)
                    }
                    Text("Se excluyeron \(inventory.excludedSessionScopes) ámbitos ligados a una sesión. La lista no deduce cuál es el más reciente ni inspecciona sus carpetas originales.").font(.caption).foregroundStyle(.secondary)
                    Button("Revisar copia seleccionada") { Task { await restoration.prepareReview() } }
                        .disabled(!mayBegin || !restoration.mayPrepare || restoration.selectedSource?.eligible != true || !inventory.destinationEmpty || !restoration.inventoryIsCurrent)
                }
            }
            if let review = restoration.preview {
                Section("Revisión de la copia") {
                    Text("\(review.documents) notas · \(review.bytes) bytes")
                    Text("Destino: \(review.workspacePath)").textSelection(.enabled)
                    Text(review.migrationRequired ? "Esta decisión también actualizará la memoria del formato 1 al 2 en la misma transacción." : "La memoria ya usa el formato 2.")
                    Text("Cada nota copiada tendrá un identificador nuevo y revisión 1. El agente, texto y etiquetas se conservan; no se crea ninguna autorización reutilizable.").font(.caption)
                    HStack {
                        Button("Solicitar aprobación de la copia") { Task { await restoration.submit() } }.buttonStyle(.borderedProminent).disabled(!mayBegin || !restoration.maySubmit)
                        Button("Descartar revisión") { restoration.discardPreview() }.disabled(!restoration.mayPrepare)
                    }
                    DisclosureGroup("Identidad de esta revisión") { Text(review.digest).font(.caption.monospaced()).textSelection(.enabled) }
                }
            }
            if restoration.responseUnconfirmed {
                Section("Solicitud sin confirmar") {
                    Text("No se reenviará. Consulta el estado para buscar la operación exacta. La ausencia de un registro no demuestra que la copia se deshiciera.")
                    if restoration.operation == nil || restoration.operation?.settled == true {
                        Button("No reintentar solicitud anterior") { restoration.acknowledgeUnconfirmedLocally() }.disabled(restoration.isRequesting)
                    }
                }
            }
            if let operation = restoration.operation {
                Section("Operación de copia") {
                    Text(operation.phase == "publication_unknown" ? "Confirmación de la copia desconocida" : MaintenancePresentation.phase(operation.phase)).font(.headline)
                    if let review = restoration.effectReview, let approval = restoration.approval, review.matches(approval, operation: operation) {
                        Text("Autoriza sólo esta copia revisada. Se conserva el origen y el destino debe seguir vacío.")
                        HStack {
                            Button("Autorizar esta copia") { Task { await restoration.decide(allow: true, ticket: approval) } }.buttonStyle(.borderedProminent)
                            Button("No autorizar") { Task { await restoration.decide(allow: false, ticket: approval) } }
                        }.disabled(!restoration.canDecide)
                    }
                    if let notice = restoration.operationNotice, notice != restoration.errorMessage { Text(notice).foregroundStyle(.orange) }
                    if let receipt = operation.memoryCopyReceipt {
                        if operation.success { Text("Copia confirmada: \(receipt.documents) notas, \(receipt.bytes) bytes.") }
                        else if receipt.status == "unknown" { Text("El recibo no confirma si la transacción quedó aplicada. No se repetirá automáticamente.").foregroundStyle(.orange) }
                        else { Text("No se confirmó una copia completada. Consulta el estado antes de continuar.") }
                    }
                    if operation.cleanupOwnerPending {
                        Text("Hay recursos propios pendientes de cierre. La consulta del registro espera a que termine esa limpieza.").foregroundStyle(.orange)
                        Button("Reintentar sólo la limpieza propia") { Task { await restoration.cleanup() } }.disabled(restoration.isRequesting)
                    } else if !operation.settled {
                        Button("Cancelar operación") { Task { await restoration.cancel() } }.disabled(restoration.isRequesting || operation.cancellationRequested)
                    }
                    Button("Consultar registro local") { Task { await restoration.queryReceipt() } }.disabled(!restoration.hasObservationTarget)
                    if restoration.pollingPaused { Button("Consultar estado de nuevo") { restoration.resumePolling() } }
                    DisclosureGroup("Detalles técnicos") {
                        Text(operation.id).font(.caption.monospaced()).textSelection(.enabled)
                        if let review = restoration.effectReview { Text(review.digest).font(.caption.monospaced()).textSelection(.enabled) }
                    }
                }
            }
            if restoration.observationWasQueried {
                Section("Registro local de sólo lectura") {
                    if let observed = restoration.observation {
                        Text("Existe un registro local de \(observed.documents) notas y \(observed.bytes) bytes.")
                        Text(observed.documentsMatch ? "Las notas actuales aún coinciden con ese registro." : "Algunas notas actuales cambiaron o se borraron después de la copia.")
                        if !observed.generationMatches { Text("La generación del destino cambió después de la copia.") }
                    } else { Text("No se observó un registro local exacto. Esto no demuestra rollback ni permite repetir la copia.") }
                    Text("Esta consulta no acredita la confirmación anterior, no modifica su recibo y no autoriza otra copia.").font(.caption).foregroundStyle(.secondary)
                }
            }
        }.formStyle(.grouped)
    }
}
