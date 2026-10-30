import DesktopCore
import SwiftUI

/// An ordinary answer belongs to this exact question/run. Field values remain
/// view-local and are cleared when the selected chat changes or delivery ends.
struct ProductInteractionQuestionView: View {
    @Bindable var store: ProductInteractionStore
    let interaction: ProductInteractionRecord
    @State private var choices: [String: String] = [:]
    @State private var freeform: [String: String] = [:]

    private var answers: [String: ProductQuestionAnswer] {
        var result: [String: ProductQuestionAnswer] = [:]
        for question in interaction.questions {
            guard let choice = choices[question.id], !choice.isEmpty else { continue }
            result[question.id] = choice == "freeform"
                ? .text(freeform[question.id] ?? "") : .option(choice)
        }
        return result
    }
    private var maySubmit: Bool {
        interaction.mayAnswer && !store.blocksInteraction
            && interaction.questions.allSatisfy { answers[$0.id]?.valid(for: $0) == true }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Jarvis necesita tu respuesta", systemImage: "questionmark.circle")
                .font(.headline)
            ForEach(interaction.questions) { question in
                VStack(alignment: .leading, spacing: 8) {
                    Text(verbatim: question.prompt).fixedSize(horizontal: false, vertical: true)
                    Picker(question.header, selection: Binding(
                        get: { choices[question.id] ?? "" },
                        set: { choices[question.id] = $0 }
                    )) {
                        Text("Elige una opción").tag("")
                        ForEach(question.options) { option in
                            Text(verbatim: option.label).tag(option.id).help(option.description)
                        }
                        Text("Otra respuesta").tag("freeform")
                    }
                    .pickerStyle(.radioGroup)
                    .disabled(store.blocksInteraction || !interaction.mayAnswer)
                    if let option = question.options.first(where: { $0.id == choices[question.id] }) {
                        Text(verbatim: option.description).font(.callout).foregroundStyle(.secondary)
                    }
                    if choices[question.id] == "freeform" {
                        TextField("Tu respuesta", text: Binding(
                            get: { freeform[question.id] ?? "" },
                            set: { freeform[question.id] = $0 }
                        ), axis: .vertical)
                        .lineLimit(2...5).textFieldStyle(.roundedBorder)
                        .disabled(store.blocksInteraction || !interaction.mayAnswer)
                        .accessibilityLabel("Respuesta libre: \(question.header)")
                    }
                }
            }
            Text("Las respuestas orientan el chat. Cada acción conserva su propia revisión de permisos.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Enviar respuesta") {
                    let submitted = answers
                    Task {
                        _ = await store.answer(interaction, answers: submitted)
                        choices = [:]; freeform = [:]
                    }
                }.disabled(!maySubmit)
                Button("Omitir pregunta") {
                    Task {
                        await store.cancelQuestion(interaction)
                        choices = [:]; freeform = [:]
                    }
                }.disabled(store.blocksInteraction || !interaction.mayAnswer)
                if store.isChanging { ProgressView().controlSize(.small) }
            }
        }
        .padding(14)
        .background(.quaternary, in: .rect(cornerRadius: 12))
        .onChange(of: store.viewIdentity) { _, _ in choices = [:]; freeform = [:] }
        .onChange(of: interaction.revision) { _, _ in choices = [:]; freeform = [:] }
        .accessibilityElement(children: .contain)
    }
}
