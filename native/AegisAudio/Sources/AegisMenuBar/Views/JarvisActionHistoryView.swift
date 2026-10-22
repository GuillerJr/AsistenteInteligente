import SwiftUI

struct JarvisActionHistoryView: View {
    let model: MenuBarModel
    let interactive: Bool
    @State private var history: JarvisActionHistoryStore
    @State private var deletion: Deletion?
    @State private var selectedConversation: UUID?

    private struct Deletion: Identifiable {
        let entryID: UUID?
        let revision: UUID
        var id: String { entryID?.uuidString ?? "all" }
    }

    init(model: MenuBarModel, interactive: Bool = true, store: JarvisActionHistoryStore? = nil) {
        self.model = model
        self.interactive = interactive
        _history = State(initialValue: store ?? JarvisActionHistoryStore())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(String(localized: "Historial de acciones", bundle: .module)).font(.headline)
                    Text(String(localized: "Actividad registrada desde esta versión de Jarvis", bundle: .module))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(String(localized: "Actualizar", bundle: .module), systemImage: "arrow.clockwise") {
                    Task { await load() }
                }.buttonStyle(JarvisButtonStyle(compact: true))
                    .disabled(!available || history.loading || history.deleting)
                    .keyboardShortcut("r", modifiers: .command)
                Button(String(localized: "Vaciar historial", bundle: .module), systemImage: "trash", role: .destructive) {
                    if let revision = history.revision { deletion = Deletion(entryID: nil, revision: revision) }
                }.buttonStyle(JarvisButtonStyle(compact: true))
                    .disabled(!available || history.entries.isEmpty || history.loading || history.deleting || history.requiresRefresh)
            }.padding(16)
            HStack {
                Menu {
                    Button(String(localized: "Todas las conversaciones", bundle: .module)) {
                        selectedConversation = nil
                        Task { await load() }
                    }
                    Button(String(localized: "Conversación actual", bundle: .module)) {
                        selectedConversation = model.chat.conversationID
                        Task { await load() }
                    }.disabled(model.chat.conversationID == nil)
                } label: {
                    Label(selectedConversation == nil
                          ? String(localized: "Todas las conversaciones", bundle: .module)
                          : String(localized: "Una conversación", bundle: .module), systemImage: "line.3.horizontal.decrease")
                }.menuStyle(.borderlessButton).fixedSize()
                    .buttonStyle(JarvisButtonStyle(compact: true))
                    .accessibilityLabel(String(localized: "Filtrar historial por conversación", bundle: .module))
                    .disabled(!available || history.deleting)
                Spacer()
                Text(String(localized: "Se conservan hasta \(history.retentionLimit) entradas", bundle: .module))
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 16).padding(.bottom, 12)
            Divider()
            if let error = history.error {
                Text(error).font(.callout).foregroundStyle(.red).padding(16)
                    .accessibilityIdentifier("action-history-error")
            }
            if history.recordingIncomplete {
                Text(String(localized: "No se pudieron registrar algunas acciones de esta sesión.", bundle: .module))
                    .font(.callout).foregroundStyle(.secondary).padding(.horizontal, 16)
            }
            if !available && interactive {
                ContentUnavailableView(String(localized: "Jarvis no está disponible", bundle: .module),
                                       systemImage: "wifi.slash")
            } else if history.loading && !history.loaded {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if history.loaded && history.entries.isEmpty && history.error == nil {
                ContentUnavailableView(String(localized: "Sin acciones registradas", bundle: .module),
                                       systemImage: "clock.arrow.circlepath",
                                       description: Text(String(localized: "Las nuevas acciones aparecerán aquí. Los chats temporales no se guardan.", bundle: .module)))
            } else {
                List(history.entries) { entry in row(entry) }
                    .scrollContentBackground(.hidden)
                    .accessibilityIdentifier("action-history-list")
            }
            HStack {
                Text(String(localized: "El estado refleja el informe de la herramienta; no siempre verifica el efecto externo.", bundle: .module))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Spacer()
                if history.nextBeforeSequence != nil || history.requiresRefresh {
                    Button(history.requiresRefresh
                           ? String(localized: "Actualizar historial", bundle: .module)
                           : String(localized: "Cargar anteriores", bundle: .module)) {
                        Task { await load(append: !history.requiresRefresh) }
                    }.buttonStyle(JarvisButtonStyle(compact: true))
                        .disabled(!available || history.loading || history.deleting)
                }
                if history.loading || history.deleting { ProgressView().controlSize(.small) }
            }.padding(16)
        }
        .frame(minWidth: 540, minHeight: 380)
        .foregroundStyle(JarvisTheme.ink)
        .background(JarvisTheme.canvas)
        .task { if interactive { await load() } }
        .onDisappear { history.invalidate() }
        .confirmationDialog(deletion?.entryID == nil
                            ? String(localized: "¿Vaciar todo el historial de acciones?", bundle: .module)
                            : String(localized: "¿Eliminar esta entrada?", bundle: .module),
                            isPresented: Binding(get: { deletion != nil }, set: { if !$0 { deletion = nil } }),
                            titleVisibility: .visible) {
            Button(String(localized: "Eliminar", bundle: .module), role: .destructive) {
                guard interactive, let requested = deletion else { return }
                deletion = nil
                Task { await history.delete(entryID: requested.entryID, expectedRevision: requested.revision,
                                            execute: model.deleteActionHistory) }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) { deletion = nil }
        } message: {
            Text(deletion?.entryID == nil
                 ? String(localized: "Se borrarán las entradas de todas las conversaciones. Esto no deshace acciones ni elimina los registros técnicos de seguridad.", bundle: .module)
                 : String(localized: "Esto elimina la entrada del historial, pero no deshace la acción ni elimina los registros técnicos de seguridad.", bundle: .module))
        }
    }

    private var available: Bool { !interactive || model.canReadChatHistory }

    private func load(append: Bool = false) async {
        guard interactive else { return }
        await history.load(conversationID: selectedConversation, append: append, fetch: model.fetchActionHistory)
    }

    private func row(_ entry: JarvisActionEntry) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: entry.state == "failed" ? "exclamationmark.circle" : "wrench.and.screwdriver")
                .foregroundStyle(.secondary).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(entry.toolTitle ?? String(localized: "Acción de herramienta", bundle: .module))
                    .font(.body.weight(.medium))
                if entry.toolTitle == nil {
                    Text(entry.toolName).font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary)
                }
                Text(entry.stateLabel).font(.callout)
                if entry.executionCount > 1 {
                    Text(String(localized: "Intentos: \(entry.executionCount); fallos informados: \(entry.failureCount)", bundle: .module))
                        .font(.caption).foregroundStyle(.secondary)
                }
                HStack {
                    Text(entry.timestamp, format: .dateTime.day().month().hour().minute())
                    if let conversationID = entry.conversationID {
                        Button(String(localized: "Ver acciones de esta conversación", bundle: .module)) {
                            selectedConversation = conversationID
                            Task { await load() }
                        }.buttonStyle(.link).disabled(!available || history.deleting)
                    } else {
                        Text(String(localized: "Sin conversación asociada", bundle: .module))
                    }
                }.font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Button(String(localized: "Eliminar entrada", bundle: .module), systemImage: "trash", role: .destructive) {
                if let revision = history.revision { deletion = Deletion(entryID: entry.id, revision: revision) }
            }.labelStyle(.iconOnly).buttonStyle(.borderless)
                .accessibilityLabel(String(localized: "Eliminar entrada", bundle: .module))
                .disabled(!available || history.loading || history.deleting || history.requiresRefresh)
        }.padding(.vertical, 6)
    }
}
