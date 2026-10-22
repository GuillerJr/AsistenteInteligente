import SwiftUI

struct JarvisModelsSettingsView: View {
    let store: JarvisSettingsStore
    let openAddModel: () -> Void
    private var draft: JarvisModelPreferences? {
        get { store.modelDraft }
        nonmutating set { store.modelDraft = newValue }
    }
    @State private var secret = ""
    @FocusState private var credentialFocused: Bool

    private var changed: Bool { draft != store.snapshot?.models }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            JarvisSettingsCard(title: String(localized: "NVIDIA API", bundle: .module), detail: String(localized: "La clave se guarda en el llavero de macOS y nunca se muestra al consultar la configuración.", bundle: .module)) {
                Label(store.snapshot?.credential == "configured" ? String(localized: "Clave configurada", bundle: .module) : String(localized: "Clave no disponible", bundle: .module),
                      systemImage: "key")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                HStack {
                    SecureField(String(localized: "Nueva clave de NVIDIA", bundle: .module), text: $secret)
                        .focused($credentialFocused).jarvisInput(focused: credentialFocused)
                        .accessibilityLabel(String(localized: "Nueva clave de la API de NVIDIA", bundle: .module))
                    Button(String(localized: "Guardar clave", bundle: .module)) {
                        let value = secret.trimmingCharacters(in: .whitespacesAndNewlines)
                        secret = ""
                        Task { await store.perform("settings.credential.set", payload: ["secret": value],
                                                   success: String(localized: "Clave de NVIDIA guardada en el llavero.", bundle: .module)) }
                    }
                    .disabled(secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            if let draft {
                if store.snapshot?.models_valid == false {
                    Label(String(localized: "La configuración guardada no es válida. Revisa y guarda esta selección para repararla.", bundle: .module),
                          systemImage: "exclamationmark.circle")
                        .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
                }
                JarvisSettingsCard(title: String(localized: "Modelos por tarea", bundle: .module), detail: String(localized: "Las alternativas se usan cuando el principal no está disponible. Los cambios se aplican al reiniciar el servicio.", bundle: .module)) {
                    ForEach(JarvisModelTask.roles, id: \.self) { role in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(JarvisModelTask.title(role)).font(.system(size: 12, weight: .medium))
                            let available = draft.models.filter { JarvisModelTask.compatible($0, role: role) }
                            LabeledContent(String(localized: "Principal", bundle: .module)) {
                                JarvisMenuPicker(title: String(localized: "Principal · \(JarvisModelTask.title(role))", bundle: .module),
                                                 selection: primaryBinding(role),
                                                 options: available.map { .init(value: $0.id, title: $0.id) })
                            }
                            LabeledContent(String(localized: "Alternativa", bundle: .module)) {
                                JarvisMenuPicker(title: String(localized: "Alternativa · \(JarvisModelTask.title(role))", bundle: .module),
                                                 selection: fallbackBinding(role),
                                                 options: [.init(value: "", title: String(localized: "Sin alternativa", bundle: .module))]
                                                 + available.filter { $0.id != draft.roles[role]?.primary }
                                                     .map { .init(value: $0.id, title: $0.id) })
                            }
                            if role != JarvisModelTask.roles.last { Divider().padding(.top, 6) }
                        }
                    }
                    HStack {
                        Text(changed ? String(localized: "Tienes cambios sin guardar", bundle: .module) : String(localized: "Selección guardada", bundle: .module))
                            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                        Spacer()
                        Button(String(localized: "Descartar", bundle: .module)) { self.draft = store.snapshot?.models }.disabled(!changed)
                        Button(String(localized: "Guardar modelos", bundle: .module)) { Task { await store.saveModels(draft) } }
                            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                            .disabled(!changed && store.snapshot?.models_valid != false)
                    }
                }
                JarvisSettingsCard(title: String(localized: "Tus modelos", bundle: .module), detail: String(localized: "La prueba envía texto y, si corresponde, una imagen sintética a NVIDIA. No utiliza tus conversaciones ni archivos.", bundle: .module)) {
                    ForEach(draft.models) { model in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(model.id).font(.system(size: 12, weight: .medium)).textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true).help(model.id)
                                Text([model.tools ? String(localized: "Herramientas", bundle: .module) : nil, model.images ? String(localized: "Imágenes", bundle: .module) : nil,
                                      String(localized: "Texto", bundle: .module)].compactMap { $0 }.joined(separator: " · "))
                                    .font(.system(size: 10)).foregroundStyle(JarvisTheme.inkMuted)
                            }
                            Spacer()
                            Button(String(localized: "Probar", bundle: .module)) {
                                Task { await store.perform("settings.models.test", payload: [
                                    "model_id": model.id, "images": model.images, "tools": model.tools,
                                ], success: String(localized: "El modelo respondió correctamente a la prueba.", bundle: .module)) }
                            }
                            let assigned = draft.roles.values.contains { $0.primary == model.id || $0.fallback == model.id }
                            Button { self.draft?.models.removeAll { $0.id == model.id } } label: {
                                Image(systemName: "trash")
                            }
                            .disabled(assigned)
                            .help(assigned ? String(localized: "Cambia sus asignaciones antes de quitarlo", bundle: .module) : String(localized: "Quitar de la selección de Jarvis", bundle: .module))
                            .accessibilityLabel(String(localized: "Quitar modelo \(model.id)", bundle: .module))
                        }
                    }
                    Button(String(localized: "Añadir modelo…", bundle: .module), systemImage: "plus", action: openAddModel)
                }
            } else {
                Text(String(localized: "Actualiza el estado para cargar la selección de modelos.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            }
        }
        .disabled(store.busy || store.snapshot == nil)
        .onAppear { if draft == nil { draft = store.snapshot?.models } }
        .onChange(of: store.snapshot?.models) { previous, value in
            store.reconcileModelDraft(previous: previous, current: value)
        }
    }
    private func primaryBinding(_ role: String) -> Binding<String> {
        Binding(get: { draft?.roles[role]?.primary ?? "" }, set: { value in
            draft?.roles[role]?.primary = value
            if draft?.roles[role]?.fallback == value { draft?.roles[role]?.fallback = nil }
        })
    }
    private func fallbackBinding(_ role: String) -> Binding<String> {
        Binding(get: { draft?.roles[role]?.fallback ?? "" },
                set: { draft?.roles[role]?.fallback = $0.isEmpty ? nil : $0 })
    }
}
