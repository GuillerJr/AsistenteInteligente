import SwiftUI

struct JarvisPrivacySettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    @State private var query = ""
    @State private var deleting: JarvisMemoryHit?
    @FocusState private var queryFocused: Bool

    var body: some View {
        JarvisSettingsCard(title: "Permisos del Mac") {
            MenuBarPermissionsView(model: model)
            Button("Actualizar permisos") { Task { await model.refreshPrivacyCapabilities() } }
        }
        JarvisSettingsCard(title: "Tareas programadas",
                          detail: "Lo que Jarvis hace solo, como «cada mañana resúmeme mis correos». Pídelo en el chat; aquí puedes pausarlas, ejecutarlas ya o borrarlas.") {
            if store.scheduledTasks.isEmpty {
                Text("Ninguna. Pídele a Jarvis, por ejemplo: «cada mañana a las 8 resúmeme mis correos».")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            ForEach(store.scheduledTasks) { task in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.title).font(.system(size: 12, weight: .semibold))
                        Text(task.schedule + (task.notify == "when_new" ? " · solo novedades" : ""))
                            .font(.system(size: 11)).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Toggle("Activa", isOn: Binding(
                        get: { task.enabled },
                        set: { value in Task { await store.setScheduledTask(task, enabled: value) } }
                    )).labelsHidden().toggleStyle(.switch)
                    Button("Ejecutar ahora") { Task { await store.runScheduledTask(task) } }
                    Button { Task { await store.deleteScheduledTask(task) } } label: {
                        Image(systemName: "trash")
                    }.accessibilityLabel("Borrar la tarea \(task.title)")
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadScheduledTasks() }
        JarvisSettingsCard(title: "Objetivos",
                          detail: "Encargos de varios pasos que Jarvis trabaja solo hasta terminarlos, como «hazte cargo de revisar mis correos de la semana». Pídelos en el chat.") {
            if store.goals.isEmpty {
                Text("Ninguno. Pídele a Jarvis, por ejemplo: «hazte cargo hasta terminarlo: …».")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            ForEach(store.goals) { goal in
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(goal.title).font(.system(size: 12, weight: .semibold))
                        Text("\(goal.status_label) · \(goal.steps_used)/\(goal.max_steps) pasos")
                            .font(.system(size: 11)).foregroundStyle(.secondary)
                        if let last = goal.notes.last {
                            Text(last).font(.system(size: 11)).foregroundStyle(.secondary)
                                .lineLimit(2)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if goal.status != "complete" {
                        Button(goal.active ? "Detener" : "Reanudar") {
                            Task { await store.setGoal(goal, running: !goal.active) }
                        }
                    }
                    Button { Task { await store.deleteGoal(goal) } } label: {
                        Image(systemName: "trash")
                    }.accessibilityLabel("Borrar el objetivo \(goal.title)")
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadGoals() }
        JarvisSettingsCard(title: "Permitidas siempre",
                          detail: "Herramientas de solo lectura que aprobaste con «Aprobar siempre». Las que escriben, envían o borran siempre piden tu aprobación.") {
            if store.standingGrants.isEmpty {
                Text("Ninguna. Jarvis pide tu aprobación cada vez.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
            ForEach(store.standingGrants) { grant in
                HStack(spacing: 12) {
                    Text(grant.label).font(.system(size: 12)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Revocar") { Task { await store.revokeStandingGrant(grant) } }
                        .accessibilityLabel("Revocar el permiso permanente de \(grant.label)")
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .task { await store.loadStandingGrants() }
        JarvisSettingsCard(title: "Recuerdos guardados",
                          detail: "Busca en la memoria de este espacio de Jarvis. Puedes eliminar cada recuerdo después de revisarlo.") {
            HStack {
                TextField("Buscar un recuerdo…", text: $query)
                    .focused($queryFocused).jarvisInput(focused: queryFocused)
                    .onSubmit { Task { await store.searchMemories(query) } }
                    .accessibilityLabel("Buscar recuerdos de Jarvis")
                Button("Buscar") { Task { await store.searchMemories(query) } }
                    .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.snapshot == nil)
            }
            ForEach(store.memoryHits) { hit in
                HStack(alignment: .top, spacing: 12) {
                    Text(hit.excerpt).font(.system(size: 12)).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button { deleting = hit } label: { Image(systemName: "trash") }
                        .accessibilityLabel("Eliminar este recuerdo")
                }
                .padding(12).voiceEnrollmentCard()
            }
        }
        .alert("¿Eliminar este recuerdo?", isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }
        ), presenting: deleting) { hit in
            Button("Eliminar", role: .destructive) { Task { await store.deleteMemory(hit) } }
            Button("Cancelar", role: .cancel) {}
        } message: { hit in Text(hit.excerpt + "\n\nEsta eliminación no se puede deshacer.") }
    }
}
