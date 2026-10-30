import DesktopCore
import SwiftUI

struct ProductInteractionWidgets: View {
    @Bindable var store: ProductInteractionStore
    let interaction: ProductInteractionRecord
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        if let panel = interaction.panel {
            VStack(alignment: .leading, spacing: 14) {
                Text(verbatim: panel.title).font(.headline)
                LazyVGrid(columns: panel.layout == "stack" ? [GridItem(.flexible())]
                    : [GridItem(.adaptive(minimum: 220), spacing: 18)], alignment: .leading, spacing: 18) {
                    ForEach(panel.widgets) { widget in
                        ProductWidgetView(store: store, interaction: interaction, widget: widget)
                    }
                }
                if panel.widgets.isEmpty {
                    Text("Personaliza el panel para añadir texto, listas o progreso.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            .padding(14)
            .background(.background, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .environment(\.colorScheme, panel.appearance == "light" ? .light
                : panel.appearance == "dark" ? .dark : scheme)
            .tint(ProductWidgetStyle.color(panel.accent))
            .contrast(panel.appearance == "high_contrast" ? 1.18 : 1)
        }
    }
}

private struct ProductWidgetView: View {
    @Bindable var store: ProductInteractionStore
    let interaction: ProductInteractionRecord
    let widget: ProductCanvasWidget
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(widget.title, systemImage: widget.symbol)
                .font(.subheadline.weight(.semibold)).foregroundStyle(ProductWidgetStyle.color(widget.color))
            if let text = widget.text, !text.isEmpty { Text(verbatim: text).textSelection(.enabled) }
            if let value = widget.value { Text(verbatim: value).font(.title3.weight(.medium)).monospacedDigit() }
            if widget.type == "progress" {
                if let value = widget.progress {
                    ProgressView(value: value, total: 100).accessibilityLabel(widget.title)
                    Text(value / 100, format: .percent.precision(.fractionLength(0)))
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                } else {
                    Text("Progreso pendiente de actualizar").font(.caption).foregroundStyle(.secondary)
                }
            }
            ForEach(widget.items) { item in
                if widget.type == "checklist" {
                    Toggle(item.text, isOn: Binding(get: { item.checked }, set: { checked in
                        Task { await store.toggle(interaction, widgetID: widget.id,
                                                  itemIndex: item.index, checked: checked) }
                    })).toggleStyle(.checkbox).disabled(store.blocksInteraction)
                } else {
                    Text(verbatim: item.text).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if let source = widget.source {
                Text(verbatim: source.label).font(.caption.weight(.medium))
                Text(verbatim: source.text).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .accessibilityElement(children: .contain)
    }
}

enum ProductWidgetStyle {
    static func color(_ name: String) -> Color {
        switch name {
        case "secondary": return .secondary
        case "accent": return .accentColor
        case "success": return .green
        case "warning": return .orange
        case "error": return .red
        default: return .primary
        }
    }
    static func title(_ name: String) -> String {
        ["text": "Texto", "stat": "Dato", "list": "Lista", "checklist": "Lista de tareas",
         "progress": "Progreso", "notice": "Aviso", "stack": "Una columna", "grid": "Cuadrícula",
         "columns": "Columnas", "system": "Sistema", "light": "Claro", "dark": "Oscuro",
         "high_contrast": "Alto contraste", "primary": "Principal", "secondary": "Secundario",
         "accent": "Acento", "success": "Completado", "warning": "Atención", "error": "Error"][name] ?? name
    }
}

/// The editor keeps a deliberate local draft. Its parent keys it to the scope
/// and observed panel revision; polling the same revision preserves unsaved edits.
struct ProductCanvasEditor: View {
    @Bindable var store: ProductInteractionStore
    let interaction: ProductInteractionRecord?
    @State private var draft: ProductCanvasDefinition
    init(store: ProductInteractionStore, interaction: ProductInteractionRecord?) {
        self.store = store; self.interaction = interaction
        _draft = State(initialValue: interaction?.panel ?? ProductCanvasDefinition())
    }
    private var valid: Bool { (try? ProductCanvasDefinition(draft.submission)) != nil }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("Nombre del panel", text: $draft.title).textFieldStyle(.roundedBorder)
            Picker("Distribución", selection: $draft.layout) {
                ForEach(["stack", "grid", "columns"], id: \.self) { Text(ProductWidgetStyle.title($0)).tag($0) }
            }
            Picker("Apariencia", selection: $draft.appearance) {
                ForEach(["system", "light", "dark", "high_contrast"], id: \.self) { Text(ProductWidgetStyle.title($0)).tag($0) }
            }
            Picker("Acento", selection: $draft.accent) {
                ForEach(ProductCanvasWidget.colors, id: \.self) { Text(ProductWidgetStyle.title($0)).tag($0) }
            }
            ForEach($draft.widgets) { $widget in
                ProductWidgetEditor(widget: $widget) { draft.widgets.removeAll { $0.id == widget.id } }
            }
            Menu("Añadir widget", systemImage: "plus") {
                ForEach(["text", "stat", "list", "checklist", "progress"], id: \.self) { type in
                    Button(ProductWidgetStyle.title(type)) { add(type) }
                }
            }.disabled(draft.widgets.count >= 24)
            Button("Guardar panel") {
                let submitted = draft
                let identity = store.viewIdentity
                Task {
                    _ = await store.setPanel(submitted, revision: interaction?.revision ?? 0,
                                             runID: interaction?.scopeRunID ?? "host", expectedIdentity: identity)
                }
            }.disabled(!valid || store.blocksInteraction || !store.connected)
        }
        .disabled(store.isChanging)
    }
    private func add(_ type: String) {
        guard draft.widgets.count < 24 else { return }
        let id = "widget_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        draft.widgets.append(ProductCanvasWidget(id: id, type: type, title: ProductWidgetStyle.title(type),
            text: type == "text" ? "Texto del panel" : nil, value: type == "stat" ? "0" : nil,
            progress: type == "progress" ? 0 : nil))
    }
}

private struct ProductWidgetEditor: View {
    @Binding var widget: ProductCanvasWidget
    let remove: () -> Void
    var body: some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 8) {
                TextField("Título", text: $widget.title).textFieldStyle(.roundedBorder)
                if ["text", "notice"].contains(widget.type) {
                    TextField("Contenido", text: Binding(get: { widget.text ?? "" }, set: { widget.text = $0 }), axis: .vertical)
                        .lineLimit(2...6).textFieldStyle(.roundedBorder)
                }
                if widget.type == "stat" {
                    TextField("Valor", text: Binding(get: { widget.value ?? "" }, set: { widget.value = $0 }))
                        .textFieldStyle(.roundedBorder)
                }
                if widget.type == "progress" {
                    Slider(value: Binding(get: { widget.progress ?? 0 }, set: { widget.progress = $0 }), in: 0...100)
                        .accessibilityLabel("Porcentaje de \(widget.title)")
                    Text((widget.progress ?? 0) / 100, format: .percent.precision(.fractionLength(0))).font(.caption).monospacedDigit()
                }
                if ["list", "checklist"].contains(widget.type) {
                    TextField("Un elemento por línea", text: Binding(
                        get: { widget.items.map(\.text).joined(separator: "\n") },
                        set: { text in
                            widget.items = text.split(separator: "\n", omittingEmptySubsequences: false).prefix(50).enumerated().map { index, line in
                                ProductWidgetItem(id: "\(widget.id)-\(index)", index: index, text: String(line))
                            }
                        }), axis: .vertical).lineLimit(2...8).textFieldStyle(.roundedBorder)
                }
                Button("Retirar widget", role: .destructive, action: remove)
            }.padding(.top, 6)
        } label: { Text(verbatim: widget.title).font(.subheadline) }
    }
}
