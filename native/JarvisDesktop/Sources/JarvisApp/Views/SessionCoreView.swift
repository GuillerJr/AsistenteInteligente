import DesktopCore
import SwiftUI

struct SessionCoreView: View {
    @Bindable var store: ChatStore
    @State private var decisions = ""
    @State private var branchTitle = "Rama de conversación"
    @State private var rewindPoint = 0

    private var points: [ChatMessage] { store.messages.filter { $0.sequence != nil && $0.role != "system" } }
    private var mayChange: Bool { store.canChangeSession && store.sessionCore.scope == store.selected?.scope && store.sessionCore.snapshot != nil }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Contexto y ramas").font(.title3.bold())
                Text("Revisa el contexto que continuará el modelo. El historial y los registros de acciones conservan su identidad.")
                    .font(.callout).foregroundStyle(.secondary)
                Button("Consultar contexto", systemImage: "arrow.clockwise") {
                    Task { await store.refreshSessionContext() }
                }.disabled(!store.ready || store.busy || store.sessionCore.isRequesting)
                if store.sessionCore.isRequesting { ProgressView("Consultando o publicando…") }
                if let snapshot = store.sessionCore.snapshot {
                    LabeledContent("Revisión", value: String(snapshot.revision))
                    LabeledContent("Mensajes y registros", value: String(snapshot.messageCount))
                    if let checkpoint = snapshot.checkpoint {
                        Text("Último contexto: \(checkpoint.keptMessages) mensajes conservados, \(checkpoint.omittedMessages) omitidos del contexto; \(checkpoint.byteLength) bytes.")
                            .font(.callout)
                        Text("\(checkpoint.decisions) decisiones explícitas · \(checkpoint.receipts) recibos históricos")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Divider()
                    Text("Decisiones que añadir al contexto").font(.headline)
                    Text("Una por línea, hasta 16 en total. Si lo dejas vacío, conserva las anteriores. Son datos de contexto y no autorizaciones de herramientas.")
                        .font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: $decisions).frame(minHeight: 90).border(.quaternary)
                        .accessibilityLabel("Decisiones para conservar en el contexto")
                    Button("Revisar compactación…") { Task { await store.compactSession(decisions: decisions) } }
                        .disabled(!mayChange || snapshot.revision == 0)
                    Divider()
                    Text("Continuar desde un punto anterior").font(.headline)
                    Text("Crea otra conversación. Los efectos ya ejecutados se conservan.")
                        .font(.caption).foregroundStyle(.secondary)
                    Picker("Último mensaje de la rama", selection: $rewindPoint) {
                        Text("Selecciona un punto").tag(0)
                        ForEach(points) { message in
                            Text("\(message.sequence ?? 0) · \(message.role == "user" ? "Tú" : "Jarvis") · \(message.text.prefix(50))")
                                .tag(message.sequence ?? 0)
                        }
                    }
                    TextField("Título de la rama", text: $branchTitle)
                    Button("Revisar creación de rama…") {
                        Task { await store.rewindSession(until: rewindPoint, title: branchTitle) }
                    }.disabled(!mayChange || rewindPoint == 0 || branchTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                if let result = store.sessionCore.result {
                    Text(result.isHistoricalObservation ? "Registro local consultado; no confirma la recepción original ni repite la operación." : "Operación registrada en Jarvis.")
                        .font(.callout)
                    Text(result.id).font(.caption.monospaced()).textSelection(.enabled)
                    if let branch = result.branch {
                        Button("Abrir rama creada") { Task { await store.openSessionBranch(branch) } }
                            .disabled(!store.canChangeSession)
                    }
                }
                if !store.sessionCore.operations.isEmpty {
                    Divider()
                    Text("Registros de esta conversación").font(.headline)
                    Text("Consultar un registro recupera su resultado local sin ejecutar de nuevo la operación.")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(store.sessionCore.operations, id: \.id) { operation in
                        Button("\(operation.kind == "compact" ? "Compactación" : "Rama") · \(operation.id.prefix(12))") {
                            Task { await store.sessionCore.inspect(operation) }
                        }.disabled(store.sessionCore.blocksInteraction)
                    }
                    if store.sessionCore.hasMoreOperations {
                        Button("Consultar más registros") { Task { await store.sessionCore.loadMoreOperations() } }
                            .disabled(store.sessionCore.isRequesting)
                    }
                }
                if let error = store.sessionCore.errorMessage {
                    Text(error).foregroundStyle(.orange).font(.callout).textSelection(.enabled)
                }
                if store.sessionCore.responseUnconfirmed {
                    Text("La respuesta se perdió. Consulta el recibo con el mismo identificador antes de continuar.")
                        .font(.callout)
                    Button("Conservar resultado sin confirmar") { store.sessionCore.retainUnconfirmed() }
                        .disabled(store.sessionCore.isRequesting)
                }
            }.padding(20)
        }
        .task(id: store.selected?.scope) {
            rewindPoint = 0; decisions = ""
            await store.refreshSessionContext()
        }
    }
}
