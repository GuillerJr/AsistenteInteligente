import SwiftUI

struct JarvisPrivacySettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    @State private var query = ""
    @State private var deleting: JarvisMemoryHit?
    @FocusState private var queryFocused: Bool

    var body: some View {
        JarvisSettingsCard(title: String(localized: "Permisos del Mac", bundle: .module)) {
            MenuBarPermissionsView(model: model)
            Button(String(localized: "Actualizar permisos", bundle: .module)) { Task { await model.refreshPrivacyCapabilities() } }
        }
        JarvisSettingsCard(title: String(localized: "Tareas programadas", bundle: .module),
                          detail: String(localized: "Lo que Jarvis hace solo, como «cada mañana resúmeme mis correos». Pídelo en el chat; aquí puedes pausarlas, ejecutarlas ya o borrarlas.", bundle: .module)) {
            if store.scheduledTasks.isEmpty {
                Text(String(localized: "Ninguna. Pídele a Jarvis, por ejemplo: «cada mañana a las 8 resúmeme mis correos».", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            ForEach(store.scheduledTasks) { task in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title).font(.system(size: 12, weight: .semibold))
                        Text(task.schedule + (task.notify == "when_new" ? " · solo novedades" : ""))
                            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Toggle(String(localized: "Activa", bundle: .module), isOn: Binding(
                        get: { task.enabled },
                        set: { value in Task { await store.setScheduledTask(task, enabled: value) } }
                    )).labelsHidden().toggleStyle(.switch)
                    Button(String(localized: "Ejecutar ahora", bundle: .module)) { Task { await store.runScheduledTask(task) } }
                    Button { Task { await store.deleteScheduledTask(task) } } label: {
                        Image(systemName: "trash")
                    }.accessibilityLabel(String(localized: "Borrar la tarea \(task.title)", bundle: .module))
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadScheduledTasks() }
        JarvisSettingsCard(title: String(localized: "Objetivos", bundle: .module),
                          detail: String(localized: "Encargos de varios pasos que Jarvis trabaja solo hasta terminarlos, como «hazte cargo de revisar mis correos de la semana». Pídelos en el chat.", bundle: .module)) {
            if store.goals.isEmpty {
                Text(String(localized: "Ninguno. Pídele a Jarvis, por ejemplo: «hazte cargo hasta terminarlo: …».", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            ForEach(store.goals) { goal in
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(goal.title).font(.system(size: 12, weight: .semibold))
                        Text(String(localized: "\(goal.status_label) · \(goal.steps_used)/\(goal.max_steps) pasos", bundle: .module))
                            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                        if let last = goal.notes.last {
                            Text(last).font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                                .lineLimit(2)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if goal.status != "complete" {
                        Button(goal.active ? String(localized: "Detener", bundle: .module) : String(localized: "Reanudar", bundle: .module)) {
                            Task { await store.setGoal(goal, running: !goal.active) }
                        }
                    }
                    Button { Task { await store.deleteGoal(goal) } } label: {
                        Image(systemName: "trash")
                    }.accessibilityLabel(String(localized: "Borrar el objetivo \(goal.title)", bundle: .module))
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadGoals() }
        JarvisSettingsCard(title: String(localized: "Permitidas siempre", bundle: .module),
                          detail: String(localized: "Herramientas de solo lectura que aprobaste con «Aprobar siempre». Las que escriben, envían o borran siempre piden tu aprobación.", bundle: .module)) {
            if store.standingGrants.isEmpty {
                Text(String(localized: "Ninguna. Jarvis pide tu aprobación cada vez.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            ForEach(store.standingGrants) { grant in
                HStack(spacing: 12) {
                    Text(grant.label).font(.system(size: 12)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button(String(localized: "Revocar", bundle: .module)) { Task { await store.revokeStandingGrant(grant) } }
                        .accessibilityLabel(String(localized: "Revocar el permiso permanente de \(grant.label)", bundle: .module))
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadStandingGrants() }
        JarvisSettingsCard(title: String(localized: "Apps que Jarvis puede usar", bundle: .module),
                          detail: String(localized: "Aplicaciones en las que permitiste siempre el uso de la computadora. Las apps del sistema sensibles y los flujos de varios pasos siempre piden tu aprobación.", bundle: .module)) {
            if store.appGrants.isEmpty {
                Text(String(localized: "Ninguna. Jarvis pide tu aprobación cada vez que usa una app.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            ForEach(store.appGrants) { grant in
                HStack(spacing: 12) {
                    Text(JarvisApprovalCard.appName(grant.bundle_identifier)).font(.system(size: 12))
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button(String(localized: "Quitar", bundle: .module)) { Task { await store.revokeAppGrant(grant) } }
                        .accessibilityLabel(String(localized: "Quitar el permiso para usar \(grant.bundle_identifier)", bundle: .module))
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadAppGrants() }
        JarvisSettingsCard(title: String(localized: "Recuerdos guardados", bundle: .module),
                          detail: String(localized: "Busca en la memoria de este espacio de Jarvis. Puedes eliminar cada recuerdo después de revisarlo.", bundle: .module)) {
            HStack {
                TextField(String(localized: "Buscar un recuerdo…", bundle: .module), text: $query)
                    .focused($queryFocused).jarvisInput(focused: queryFocused)
                    .onSubmit { Task { await store.searchMemories(query) } }
                    .accessibilityLabel(String(localized: "Buscar recuerdos de Jarvis", bundle: .module))
                Button(String(localized: "Buscar", bundle: .module)) { Task { await store.searchMemories(query) } }
                    .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.snapshot == nil)
            }
            ForEach(store.memoryHits) { hit in
                HStack(alignment: .top, spacing: 12) {
                    Text(hit.excerpt).font(.system(size: 12)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button { deleting = hit } label: { Image(systemName: "trash") }
                        .accessibilityLabel(String(localized: "Eliminar este recuerdo", bundle: .module))
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .alert(String(localized: "¿Eliminar este recuerdo?", bundle: .module), isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }
        ), presenting: deleting) { hit in
            Button(String(localized: "Eliminar", bundle: .module), role: .destructive) { Task { await store.deleteMemory(hit) } }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: { hit in Text(hit.excerpt + String(localized: "\n\nEsta eliminación no se puede deshacer.", bundle: .module)) }
    }
}
