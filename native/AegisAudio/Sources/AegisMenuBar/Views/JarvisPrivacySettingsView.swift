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
