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
            Text("Añadir modelo de NVIDIA").font(.system(size: 20, weight: .semibold))
            TextField("proveedor/modelo", text: $modelID)
                .focused($identifierFocused).jarvisInput(focused: identifierFocused)
                .accessibilityLabel("Identificador del modelo de NVIDIA")
            Text("Usa el ID del catálogo de NVIDIA e indica las capacidades documentadas del modelo.")
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            Toggle("Admite herramientas", isOn: $tools).font(.system(size: 12))
            Toggle("Admite imágenes", isOn: $images).font(.system(size: 12))
            HStack {
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Añadir") {
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
