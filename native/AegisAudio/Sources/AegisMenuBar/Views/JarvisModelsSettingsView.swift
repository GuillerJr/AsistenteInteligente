import SwiftUI

struct JarvisModelsSettingsView: View {
    let store: JarvisSettingsStore
    private var draft: JarvisModelPreferences? {
        get { store.modelDraft }
        nonmutating set { store.modelDraft = newValue }
    }
    @State private var secret = ""
    @State private var adding = false
    @State private var modelID = ""
    @State private var images = false
    @State private var tools = false

    private var changed: Bool { draft != store.snapshot?.models }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            JarvisSettingsCard(title: "NVIDIA API", detail: "La clave se guarda en el llavero de macOS y nunca se muestra al consultar la configuración.") {
                Label(store.snapshot?.credential == "configured" ? "Clave configurada" : "Clave no disponible",
                      systemImage: "key")
                    .font(.system(size: 12)).foregroundStyle(.white.opacity(0.7))
                HStack {
                    SecureField("Nueva clave de NVIDIA", text: $secret).textFieldStyle(.roundedBorder)
                        .accessibilityLabel("Nueva clave de la API de NVIDIA")
                    Button("Guardar clave") {
                        let value = secret.trimmingCharacters(in: .whitespacesAndNewlines)
                        secret = ""
                        Task { await store.perform("settings.credential.set", payload: ["secret": value],
                                                   success: "Clave de NVIDIA guardada en el llavero.") }
                    }
                    .disabled(secret.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            if let draft {
                if store.snapshot?.models_valid == false {
                    Label("La configuración guardada no es válida. Revisa y guarda esta selección para repararla.",
                          systemImage: "exclamationmark.circle")
                        .font(.system(size: 12)).foregroundStyle(HUDStyle.accent(for: .warning))
                }
                JarvisSettingsCard(title: "Modelos por tarea", detail: "Las alternativas se usan cuando el principal no está disponible. Los cambios se aplican al reiniciar el servicio.") {
                    ForEach(JarvisModelTask.roles, id: \.self) { role in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(JarvisModelTask.title(role)).font(.system(size: 12, weight: .medium))
                            let available = draft.models.filter { JarvisModelTask.compatible($0, role: role) }
                            Picker("Principal", selection: primaryBinding(role)) {
                                ForEach(available) { model in Text(model.id).tag(model.id) }
                            }
                            Picker("Alternativa", selection: fallbackBinding(role)) {
                                Text("Sin alternativa").tag("")
                                ForEach(available.filter { $0.id != draft.roles[role]?.primary }) { model in
                                    Text(model.id).tag(model.id)
                                }
                            }
                            if role != JarvisModelTask.roles.last { Divider().padding(.top, 6) }
                        }
                    }
                    HStack {
                        Text(changed ? "Tienes cambios sin guardar" : "Selección guardada")
                            .font(.system(size: 11)).foregroundStyle(.white.opacity(0.5))
                        Spacer()
                        Button("Descartar") { self.draft = store.snapshot?.models }.disabled(!changed)
                        Button("Guardar modelos") { Task { await store.saveModels(draft) } }
                            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                            .disabled(!changed && store.snapshot?.models_valid != false)
                    }
                }
                JarvisSettingsCard(title: "Tus modelos", detail: "La prueba envía texto y, si corresponde, una imagen sintética a NVIDIA. No utiliza tus conversaciones ni archivos.") {
                    ForEach(draft.models) { model in
                        HStack(spacing: 10) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(model.id).font(.system(size: 12, weight: .medium)).textSelection(.enabled)
                                Text([model.tools ? "Herramientas" : nil, model.images ? "Imágenes" : nil,
                                      "Texto"].compactMap { $0 }.joined(separator: " · "))
                                    .font(.system(size: 10)).foregroundStyle(.white.opacity(0.55))
                            }
                            Spacer()
                            Button("Probar") {
                                Task { await store.perform("settings.models.test", payload: [
                                    "model_id": model.id, "images": model.images, "tools": model.tools,
                                ], success: "El modelo respondió correctamente a la prueba.") }
                            }
                            let assigned = draft.roles.values.contains { $0.primary == model.id || $0.fallback == model.id }
                            Button { self.draft?.models.removeAll { $0.id == model.id } } label: {
                                Image(systemName: "trash")
                            }
                            .disabled(assigned)
                            .help(assigned ? "Cambia sus asignaciones antes de quitarlo" : "Quitar de la selección de Jarvis")
                            .accessibilityLabel("Quitar modelo \(model.id)")
                        }
                    }
                    Button("Añadir modelo…", systemImage: "plus") {
                        modelID = ""; images = false; tools = false; adding = true
                    }
                }
            } else {
                Text("Actualiza el estado para cargar la selección de modelos.")
                    .font(.system(size: 12)).foregroundStyle(.white.opacity(0.6))
            }
        }
        .disabled(store.busy || store.snapshot == nil)
        .onAppear { if draft == nil { draft = store.snapshot?.models } }
        .onChange(of: store.snapshot?.models) { _, value in
            if !changed || draft == nil || store.message?.hasPrefix("Modelos guardados") == true { draft = value }
        }
        .sheet(isPresented: $adding) {
            VStack(alignment: .leading, spacing: 18) {
                Text("Añadir modelo de NVIDIA").font(.system(size: 20, weight: .semibold))
                TextField("proveedor/modelo", text: $modelID).textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Identificador del modelo de NVIDIA")
                Text("Usa el ID del catálogo de NVIDIA e indica las capacidades documentadas del modelo.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                Toggle("Admite herramientas", isOn: $tools)
                Toggle("Admite imágenes", isOn: $images)
                HStack {
                    Button("Cancelar") { adding = false }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button("Añadir") {
                        draft?.models.append(.init(model_id: modelID, images: images, tools: tools))
                        adding = false
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!validModelID)
                }
            }
            .padding(24).frame(width: 430)
        }
    }

    private var validModelID: Bool {
        modelID.count <= 256 && modelID.range(of: "^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$",
                                             options: .regularExpression) != nil
            && draft?.models.contains { $0.id == modelID } == false
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
