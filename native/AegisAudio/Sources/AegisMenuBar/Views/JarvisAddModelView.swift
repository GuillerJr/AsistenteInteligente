import SwiftUI

/// A dedicated sheet keeps form state and focus inside its own window.
struct JarvisAddModelView: View {
    let store: JarvisSettingsStore
    @Environment(\.dismiss) private var dismiss
    @State private var modelID = ""
    @State private var images = false
    @State private var tools = false
    @FocusState private var identifierFocused: Bool

    private var validModelID: Bool {
        modelID.count <= 256
            && modelID.range(of: "^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$",
                             options: .regularExpression) != nil
            && store.modelDraft?.models.contains { $0.id == modelID } == false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(String(localized: "Añadir modelo de NVIDIA", bundle: .module)).font(.system(size: 20, weight: .semibold))
            TextField(String(localized: "proveedor/modelo", bundle: .module), text: $modelID)
                .focused($identifierFocused).jarvisInput(focused: identifierFocused)
                .accessibilityLabel(String(localized: "Identificador del modelo de NVIDIA", bundle: .module))
            Text(String(localized: "Usa el ID del catálogo de NVIDIA e indica las capacidades documentadas del modelo.", bundle: .module))
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            Toggle(String(localized: "Admite herramientas", bundle: .module), isOn: $tools).font(.system(size: 12))
            Toggle(String(localized: "Admite imágenes", bundle: .module), isOn: $images).font(.system(size: 12))
            HStack {
                Button(String(localized: "Cancelar", bundle: .module)) { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(String(localized: "Añadir", bundle: .module)) {
                    guard validModelID, !store.busy else { return }
                    store.modelDraft?.models.append(.init(model_id: modelID, images: images, tools: tools))
                    dismiss()
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                .disabled(!validModelID || store.busy)
            }
        }
        .padding(24).frame(width: 460).jarvisSheet().toggleStyle(JarvisSwitchStyle())
        .onAppear { identifierFocused = true }
    }
}
