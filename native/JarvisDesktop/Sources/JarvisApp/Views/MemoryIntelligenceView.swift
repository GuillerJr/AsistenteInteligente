import DesktopCore
import SwiftUI

/// Explicit owner settings; no automatic model acquisition when this panel opens.
struct MemoryIntelligenceView: View {
    @Bindable var store: MemoryIntelligenceStore
    @State private var visibilityID = UUID()
    var body: some View {
        Form {
            Section {
                Text("La preparación del modelo y la búsqueda semántica se hacen en este Mac, con CPU. No se envían vectores a otro servicio.")
                Text("Solo se indexan notas que ya aprobaste. Las direcciones de origen son etiquetas: no permiten leer archivos, carpetas ni enlaces.")
                    .foregroundStyle(.secondary)
                if store.scope?.temporary == true {
                    Label("Conversación temporal: política e índice en memoria RAM; se descartan al cerrar la sesión.", systemImage: "clock")
                }
            } header: { Text("Memoria local") }
            if let status = store.status {
                engine(status)
                policy(status)
                index(status)
                if let recall = status.lastRecall {
                    Section("Última recuperación") {
                        LabeledContent("Estado", value: stateTitle(recall.status))
                        LabeledContent("Modo", value: recall.mode.title)
                        LabeledContent("Citas", value: recall.citations.formatted())
                        Text("Este resumen no conserva el texto recuperado.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            } else {
                Text(store.scope == nil ? "Selecciona una conversación para ver su ámbito de memoria." : "Actualiza para consultar el estado local.")
                    .foregroundStyle(.secondary)
            }
            if let job = store.job { jobSection(job) }
            if let review = store.review { policyReview(review) }
            if let confirmation = store.confirmation { confirmationSection(confirmation) }
            if let error = store.errorMessage {
                Section { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            }
            if store.responseUnconfirmed {
                Section {
                    Text("El registro conserva una ventana limitada de trabajos. Si una solicitud no aparece, su ausencia no permite reenviarla automáticamente ni confirmar que no tuvo efecto.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Section {
                HStack {
                    Button("Actualizar estado") { Task { await store.refresh() } }.disabled(store.scope == nil || store.isBusy)
                    if store.isBusy { ProgressView().controlSize(.small) }
                }
                if store.pollingPaused { Text("Se pausó la consulta automática. Actualiza para continuar comprobando el trabajo.").foregroundStyle(.secondary) }
            }
        }
        .formStyle(.grouped)
        .task { store.setVisible(visibilityID, visible: true); await store.refresh() }
        .onDisappear { store.setVisible(visibilityID, visible: false) }
    }
    private func engine(_ status: MemoryIntelligenceStatus) -> some View {
        Section("Motor local") {
            LabeledContent("Modelo", value: status.model.id)
            LabeledContent("Estado", value: stateTitle(status.model.state))
            LabeledContent("Archivos verificados", value: status.model.available ? "Disponibles" : "No disponibles")
            LabeledContent("Runtime CPU", value: status.model.runtimeAvailable ? "Disponible" : "No disponible")
            LabeledContent("Archivos del modelo", value: status.model.available
                ? ByteCountFormatter.string(fromByteCount: Int64(status.model.bytes), countStyle: .file) : "Sin verificación")
            Button(modelNeedsRepair(status.model) ? "Preparar copia nueva…"
                : status.model.state == "unverified" ? "Verificar/preparar copia…" : "Preparar modelo local…") { store.requestModelPreparation() }
                .disabled(!store.mayStartJob || status.scope.temporary || (!status.model.available && status.model.previousCopy != nil))
            if let previous = status.model.previousCopy {
                LabeledContent("Copia anterior en desuso", value: ByteCountFormatter.string(fromByteCount: Int64(previous.bytes), countStyle: .file))
                Button("Retirar copia anterior…") { store.requestPreviousModelCopyRetirement() }.disabled(!store.mayStartJob || status.scope.temporary)
                if !status.model.available {
                    Text("Ya se conservan dos copias. Retira explícitamente la anterior antes de preparar otra; tus notas no se modifican.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Text("La preparación puede descargar hasta 256 MB de archivos públicos fijados. Nunca se descarga durante una consulta.")
                .font(.caption).foregroundStyle(.secondary)
            if status.scope.temporary {
                Text("Administra las copias del modelo desde una conversación conservada. Esta conversación temporal puede usar un modelo ya preparado, con sus notas e índice solo en RAM.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    private func policy(_ status: MemoryIntelligenceStatus) -> some View {
        Section("Uso automático") {
            Toggle("Recuperar contexto pertinente en cada turno", isOn: $store.draft.autoRecallEnabled)
            Picker("Modo de recuperación", selection: $store.draft.mode) {
                ForEach(MemoryRecallMode.allCases, id: \.self) { Text($0.title).tag($0) }
            }
            Toggle("Actualizar el índice al cambiar notas aprobadas", isOn: $store.draft.autoIndexEnabled)
            Text("Si activas la recuperación, Jarvis enviará los fragmentos pertinentes a Codex con tu cuenta de ChatGPT al preparar una respuesta. La búsqueda vectorial permanece local.")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("Revisar cambios…") { Task { await store.reviewPolicy() } }.disabled(!store.mayReview || !store.hasUnsavedChanges)
                Button("Desactivar uso automático…") { Task { await store.revokeAutomation() } }
                    .disabled(!store.mayReview || (!status.policy.settings.autoRecallEnabled && !status.policy.settings.autoIndexEnabled))
            }
            LabeledContent("Revisión de política", value: status.policy.revision.formatted())
            Text("Revocar evita nuevas recuperaciones. No retira información que ya recibió el proveedor ni borra mensajes anteriores.")
                .font(.caption).foregroundStyle(.secondary)
        }.disabled(store.isBusy || store.responseUnconfirmed)
    }
    private func index(_ status: MemoryIntelligenceStatus) -> some View {
        Section("Índice de notas aprobadas") {
            LabeledContent("Estado", value: stateTitle(status.index.state))
            if status.index.isOpened {
                LabeledContent("Generación de notas", value: status.index.sourceGeneration.formatted())
                LabeledContent("Generación indexada", value: status.index.generation.map { $0.formatted() } ?? "Sin índice")
                LabeledContent("Documentos / fragmentos", value: "\(status.index.documentCount) / \(status.index.chunkCount)")
                LabeledContent("Espacio usado", value: ByteCountFormatter.string(fromByteCount: Int64(status.index.bytes), countStyle: .file))
            } else {
                Text("Abre Notas para consultar explícitamente la memoria de este ámbito. Los recuentos y la generación aún no se han leído.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            HStack {
                Button("Reconstruir índice…") { store.requestIndexRebuild() }
                    .disabled(!store.mayStartJob || !status.model.available || !status.model.runtimeAvailable || !status.index.isOpened)
                Button("Comprobar índice") { Task { await store.verifyIndex() } }
                    .disabled(!store.mayReview || store.job?.settled == false || !status.index.isOpened)
            }
            if let verification = store.verification {
                Label(verification.verified ? "Índice coherente con la generación consultada" : "El índice no quedó verificado",
                      systemImage: verification.verified ? "checkmark.seal" : "exclamationmark.triangle")
            }
            if let error = status.index.error { Text("Código del índice: \(error)").font(.caption).foregroundStyle(.secondary) }
            Text("Una coincidencia semántica ayuda a recuperar una nota; no demuestra que su contenido sea verdadero.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
    private func jobSection(_ job: MemoryIntelligenceJob) -> some View {
        Section("Trabajo local") {
            LabeledContent("Estado", value: stateTitle(job.phase.rawValue))
            if job.documentsTotal > 0 {
                ProgressView(value: Double(job.documentsDone), total: Double(job.documentsTotal))
                Text("\(job.documentsDone) de \(job.documentsTotal) documentos · \(job.chunks) fragmentos").font(.caption)
            }
            if let error = job.error { Text("Código del trabajo: \(error)").font(.caption).foregroundStyle(.secondary) }
            if job.kind == .modelRetire, [.failed, .cancelled, .cleanupPending].contains(job.phase) {
                Text("No se confirmó el retiro completo de la copia. Revisa el estado y el recibo del trabajo; la operación no se repetirá automáticamente.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if job.kind == .modelPrepare, [.failed, .cancelled, .cleanupPending].contains(job.phase) {
                Text("La preparación o verificación no quedó confirmada. Tus notas se conservan; revisa el estado de las copias antes de volver a autorizar una operación.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if job.phase == .cleanupPending { Text("La limpieza sigue pendiente. No se iniciará otro trabajo hasta confirmar su cierre.").foregroundStyle(.secondary) }
            Button("Cancelar trabajo") { Task { await store.cancelJob() } }.disabled(!store.mayCancel)
        }
    }
    private func policyReview(_ review: MemoryPolicyChangeReview) -> some View {
        Section("Revisar autorización") {
            Text(review.scope.temporary ? "Solo esta conversación temporal; se conserva en RAM." : "Este cliente, agente y espacio de trabajo. La autorización no se incluye en copias del perfil.")
            Text("Recuperación: \(review.settings.autoRecallEnabled ? "activada" : "desactivada") · Índice automático: \(review.settings.autoIndexEnabled ? "activado" : "desactivado") · \(review.settings.mode.title)")
            if review.settings.autoRecallEnabled {
                Text("Autorizas enviar fragmentos pertinentes de notas aprobadas a Codex con ChatGPT en los turnos siguientes. El modelo de búsqueda usa exclusivamente este Mac.")
            }
            HStack {
                Button("Autorizar estos cambios") { authorizePolicy(review) }.disabled(!store.mayApply)
                Button("Descartar") { store.discardReview() }.disabled(store.isRequesting)
            }
        }
    }
    private func confirmationSection(_ confirmation: MemoryIntelligenceConfirmation) -> some View {
        Section("Confirmar operación local") {
            if confirmation.kind == .prepareModel {
                Text(preparationDetail)
                Button("Autorizar preparación del modelo") { authorizeJob(confirmation) }
                    .disabled(store.isBusy || confirmation.expiresAt <= Date())
            } else if confirmation.kind == .retireModelCopy, let copy = confirmation.previousCopy {
                Text("Se retirará únicamente la copia anterior en desuso de archivos públicos del modelo: \(ByteCountFormatter.string(fromByteCount: Int64(copy.bytes), countStyle: .file)). Las notas y la copia activa se conservan. Se comprobará que ningún worker use esa copia.")
                Button("Autorizar retirar copia anterior") { authorizeJob(confirmation) }
                    .disabled(store.isBusy || confirmation.expiresAt <= Date())
            } else {
                Text("Se reconstruirá el índice de notas aprobadas de la generación \(confirmation.sourceGeneration). La publicación comprobará de nuevo la política y la fuente; tus notas se conservarán.")
                Button("Autorizar reconstrucción del índice") { authorizeJob(confirmation) }
                    .disabled(store.isBusy || confirmation.expiresAt <= Date())
            }
            Button("Descartar") { store.discardReview() }.disabled(store.isRequesting)
        }
    }
    private func authorizePolicy(_ review: MemoryPolicyChangeReview) {
        let detail = (review.scope.temporary ? "Solo esta conversación temporal; la política se conserva en RAM."
            : "Este cliente, agente y espacio de trabajo. La autorización no se incluye en respaldos.")
            + "\n\nRecuperación: \(review.settings.autoRecallEnabled ? "activada" : "desactivada"). Índice automático: \(review.settings.autoIndexEnabled ? "activado" : "desactivado"). Modo: \(review.settings.mode.title)."
            + (review.settings.autoRecallEnabled
                ? "\n\nAutorizas enviar fragmentos pertinentes de notas ya aprobadas a Codex con tu cuenta de ChatGPT. El cálculo de vectores y la búsqueda se hacen únicamente en este Mac."
                : "\n\nNo se retirarán fragmentos que el proveedor ya recibió ni se borrarán mensajes anteriores.")
        guard OwnerManagementReview.confirm(title: "Configurar memoria de Jarvis", action: "Aplicar estos cambios",
            detail: detail, isCurrent: { store.mayApply && store.review?.id == review.id && store.review?.digest == review.digest }) else { return }
        Task { await store.applyPolicy() }
    }
    private func authorizeJob(_ confirmation: MemoryIntelligenceConfirmation) {
        let title: String, action: String, detail: String
        switch confirmation.kind {
        case .prepareModel:
            let repair = modelNeedsRepair(store.status?.model)
            title = repair ? "Preparar copia nueva" : store.status?.model.state == "unverified" ? "Verificar/preparar copia" : "Preparar modelo local"
            action = repair ? "Preparar copia nueva" : "Preparar este modelo"
            detail = preparationDetail
        case .retireModelCopy:
            guard let copy = confirmation.previousCopy else { return }
            title = "Retirar copia anterior"; action = "Retirar esta copia en desuso"
            detail = "Copia: \(copy.id)\nArchivos públicos del modelo: \(ByteCountFormatter.string(fromByteCount: Int64(copy.bytes), countStyle: .file)).\n\nSolo se retirará esa copia anterior e inactiva después de comprobar que los workers estén cerrados y su identidad no cambió. Las notas y la copia activa se conservan. No se descargará otro modelo."
        case .rebuildIndex:
            title = "Reconstruir índice local"; action = "Reconstruir este índice"
            detail = "Se reconstruirá únicamente el índice de notas ya aprobadas de la generación \(confirmation.sourceGeneration). Tus notas se conservan y no se abrirán los archivos o enlaces de sus etiquetas de origen."
        }
        guard OwnerManagementReview.confirm(title: title, action: action, detail: detail,
            isCurrent: { !store.isBusy && !store.responseUnconfirmed && store.confirmation?.id == confirmation.id
                && store.status?.model.previousCopy == confirmation.previousCopy && confirmation.expiresAt > Date() }) else { return }
        Task {
            switch confirmation.kind {
            case .prepareModel: await store.prepareModel()
            case .retireModelCopy: await store.retirePreviousModelCopy()
            case .rebuildIndex: await store.rebuildIndex()
            }
        }
    }
    private func modelNeedsRepair(_ model: MemoryLocalModelStatus?) -> Bool {
        guard let model else { return false }
        return ["invalid", "corrupt", "invalid_model"].contains(model.state)
    }
    private var preparationDetail: String {
        let base = "MiniLM para CPU puede descargar hasta256MB de archivos públicos fijados y verificados. No activa la recuperación automática ni envía notas."
        if modelNeedsRepair(store.status?.model) {
            return "Se preparará una copia nueva y se conservará la anterior. Retirarla requiere una autorización independiente.\n\n" + base
        }
        if store.status?.model.state == "unverified" {
            return "Primero se verificarán los archivos locales. Si no son válidos, se preparará una copia nueva y se conservará la anterior.\n\n" + base
        }
        return base
    }
    private func stateTitle(_ code: String) -> String {
        switch code {
        case "missing", "absent", "missing_model": return "No disponible"
        case "disabled": return "Desactivado"
        case "empty": return "Vacío"
        case "not_opened": return "Notas aún sin consultar"
        case "unverified": return "Pendiente de verificación"
        case "ready", "available": return "Disponible"
        case "stale": return "Necesita actualizarse"
        case "queued": return "En cola"
        case "running", "building", "preparing": return "En curso"
        case "cancelling": return "Cancelando"
        case "complete": return "Terminado"
        case "failed", "invalid", "corrupt", "unavailable": return "No disponible; revisa el diagnóstico"
        case "cancelled": return "Cancelado"
        case "cleanup_pending": return "Limpieza pendiente"
        default: return "Estado local: \(code)"
        }
    }
}
