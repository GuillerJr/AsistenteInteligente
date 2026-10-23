import AppKit
import SwiftUI

struct JarvisCodexSettingsView: View {
    let store: JarvisCodexSettingsStore
    var interactive = true
    @State private var selectedModel = ""
    @State private var confirmDisconnect = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            JarvisSettingsCard(title: "Codex · ChatGPT", detail: String(localized: "Conecta tu cuenta de ChatGPT mediante Codex. El inicio de sesión se completa en el navegador; Jarvis no te pide contraseñas ni claves de API.", bundle: .module)) {
                Label(store.status?.title ?? String(localized: "Comprobando Codex…", bundle: .module),
                      systemImage: store.status?.ready == true ? "checkmark.circle" : "person.crop.circle")
                    .font(.system(size: 12))
                HStack {
                    if store.status?.ready == true {
                        Button(String(localized: "Desconectar cuenta…", bundle: .module)) { confirmDisconnect = true }
                            .disabled(store.busy || store.shouldPoll)
                    } else {
                        Button(String(localized: "Iniciar sesión con ChatGPT", bundle: .module)) {
                            Task { await store.connect() }
                        }.disabled(!store.canConnect)
                    }
                    if let url = store.loginURL {
                        Button(String(localized: "Abrir inicio de sesión", bundle: .module)) { NSWorkspace.shared.open(url) }
                    }
                    if store.canCancel {
                        Button(String(localized: "Cancelar operación", bundle: .module)) { Task { await store.cancel() } }
                    }
                    Button(String(localized: "Actualizar estado", bundle: .module)) { Task { await store.refresh() } }
                        .disabled(store.loading)
                }
                Text(String(localized: "El contenido de tus solicitudes y las capturas que autorices se procesará con Codex (OpenAI). Jarvis mantiene sus propias aprobaciones para controlar apps y ejecutar herramientas.", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                if store.status?.state == .missing {
                    Text(String(localized: "Instala Codex y vuelve a actualizar el estado. La app necesita su servicio local para usar tu cuenta de ChatGPT.", bundle: .module))
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            if let status = store.status, status.ready {
                JarvisSettingsCard(title: String(localized: "Modelo para Jarvis", bundle: .module),
                                   detail: String(localized: "Jarvis requiere un modelo que admita texto e imágenes. La lista muestra los modelos compatibles disponibles en tu conexión de Codex.", bundle: .module)) {
                    if status.compatibleModels.isEmpty {
                        Text(String(localized: "No hay modelos compatibles con imágenes. Actualiza el estado para volver a consultarlos.", bundle: .module))
                    } else {
                        if !status.hasCompatibleSelection {
                            Label(String(localized: "El modelo guardado no está disponible o no admite imágenes. Elige y guarda un modelo compatible para continuar.", bundle: .module), systemImage: "exclamationmark.circle")
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.warning)
                        }
                        JarvisMenuPicker(title: String(localized: "Modelo de Codex", bundle: .module), selection: $selectedModel,
                                         options: status.compatibleModels.map { .init(value: $0.id, title: $0.display_name) })
                        HStack {
                            Button(String(localized: "Guardar modelo", bundle: .module)) { Task { await store.saveModel(selectedModel) } }
                                .disabled(store.busy || store.shouldPoll || selectedModel == status.model_id
                                          || !status.compatibleModels.contains(where: { $0.id == selectedModel }))
                            Button(String(localized: "Probar conexión", bundle: .module)) { Task { await store.test() } }
                                .disabled(!store.canTest)
                        }
                    }
                    Text(String(localized: "La prueba envía solo un mensaje sintético a Codex, sin conversaciones ni archivos personales.", bundle: .module))
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            if store.busy || store.loading || store.shouldPoll {
                ProgressView(String(localized: "Esperando a Codex…", bundle: .module)).controlSize(.small)
            }
            if let message = store.message {
                Label(message, systemImage: store.failed ? "exclamationmark.circle" : "info.circle")
                    .font(.system(size: 12)).foregroundStyle(store.failed ? JarvisTheme.warning : JarvisTheme.inkSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .disabled(!interactive)
        .task { if interactive { await store.refresh(); reconcileModel() } }
        .task(id: store.shouldPoll) { if interactive && store.shouldPoll { await store.monitor() } }
        .onChange(of: store.status?.model_id) { _, _ in reconcileModel() }
        .onChange(of: store.status?.models) { _, _ in reconcileModel() }
        .confirmationDialog(String(localized: "¿Desconectar ChatGPT de Codex?", bundle: .module), isPresented: $confirmDisconnect) {
            Button(String(localized: "Desconectar", bundle: .module), role: .destructive) { Task { await store.disconnect() } }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: {
            Text(String(localized: "Se cerrará la sesión de Codex usada por Jarvis. La sesión de la app Codex seguirá abierta. Jarvis necesitará que vuelvas a iniciar sesión para responder.", bundle: .module))
        }
    }

    private func reconcileModel() {
        guard let status = store.status else { return }
        if selectedModel.isEmpty || !status.compatibleModels.contains(where: { $0.id == selectedModel }) {
            let saved = status.hasCompatibleSelection ? status.model_id : nil
            selectedModel = saved ?? status.compatibleModels.first(where: \.is_default)?.id
                ?? status.compatibleModels.first?.id ?? ""
        }
    }
}
