import DesktopCore
import SwiftUI

struct ProductInteractionPanel: View {
    @Bindable var store: ProductInteractionStore
    var onDraft: (String) -> Void = { _ in }
    var onNavigate: (ProductNotificationNavigation) -> Void = { _ in }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    Text("Actividad y panel").font(.title3.bold())
                    Spacer()
                    Button("Actualizar", systemImage: "arrow.clockwise") { Task { await store.refresh() } }
                        .disabled(store.isRequesting || store.isChanging || store.scope == nil)
                }
                if store.scope == nil {
                    Text("Elige un chat para consultar su actividad y personalizar el panel.")
                        .foregroundStyle(.secondary)
                } else if store.snapshot == nil, store.errorMessage == nil {
                    Text("Consultando la actividad del chat…").font(.callout).foregroundStyle(.secondary)
                }
                if let error = store.errorMessage {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange).textSelection(.enabled)
                }
                if store.responseUnconfirmed {
                    Text("Puedes consultar el estado sin repetir el envío anterior.").font(.caption)
                    Button("Continuar con el estado consultado") { store.acknowledgeUnconfirmed() }
                        .disabled(store.snapshot == nil || store.isChanging)
                }
                ForEach(store.pendingQuestions) { row in
                    ProductInteractionQuestionView(store: store, interaction: row)
                        .id("\(store.viewIdentity):\(row.id):\(row.revision)")
                }
                activity
                suggestionSection
                ForEach(store.panels) { row in ProductInteractionWidgets(store: store, interaction: row) }
                if store.snapshot != nil {
                    Divider()
                    DisclosureGroup("Personalizar panel") {
                        ProductCanvasEditor(store: store, interaction: store.panels.first)
                            .id("\(store.viewIdentity):\(store.panels.first?.id ?? "new"):\(store.panels.first?.revision ?? 0)")
                            .padding(.top, 10)
                    }
                    notificationSection
                }
            }.padding(20)
        }
        .task(id: store.viewIdentity) { await store.pollWhileVisible() }
    }

    @ViewBuilder private var activity: some View {
        if let presence = store.presence?.presence {
            Label(presence == "working" ? "Jarvis está trabajando"
                : presence == "waiting_user" ? "Esperando tu respuesta" : "Jarvis está disponible",
                  systemImage: presence == "waiting_user" ? "questionmark.circle" : "circle")
                .font(.callout).foregroundStyle(.secondary)
        }
        ForEach(store.progress) { row in
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(verbatim: row.label ?? "Actividad").font(.subheadline.weight(.medium))
                    Spacer()
                    Text(Self.status(row.status)).font(.caption).foregroundStyle(.secondary)
                }
                if let value = row.progress {
                    ProgressView(value: value, total: 100).accessibilityLabel(row.label ?? "Progreso")
                }
            }
        }
    }
    @ViewBuilder private var suggestionSection: some View {
        if !store.suggestions.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Puedes continuar con…").font(.headline)
                ForEach(store.suggestions) { row in
                    ForEach(row.suggestions) { suggestion in
                        Button(suggestion.label, systemImage: "text.badge.plus") {
                            Task {
                                if let text = await store.selectSuggestion(row, suggestionID: suggestion.id) { onDraft(text) }
                            }
                        }.help(suggestion.text).disabled(store.blocksInteraction)
                    }
                }
                Text("La sugerencia se coloca en tu borrador para que la revises.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    @ViewBuilder private var notificationSection: some View {
        if let preferences = store.snapshot?.notificationPreferences {
            Divider()
            VStack(alignment: .leading, spacing: 10) {
                Text("Avisos locales de este chat").font(.headline)
                Toggle("Permitir avisos", isOn: Binding(get: { preferences.enabled }, set: { enabled in
                    let identity = store.viewIdentity
                    Task { await store.configureNotifications(enabled: enabled, silenced: preferences.silenced, expectedIdentity: identity) }
                })).disabled(store.blocksInteraction)
                Toggle("Silenciar avisos", isOn: Binding(get: { preferences.silenced }, set: { silenced in
                    let identity = store.viewIdentity
                    Task { await store.configureNotifications(enabled: preferences.enabled, silenced: silenced, expectedIdentity: identity) }
                })).disabled(store.blocksInteraction || !preferences.enabled)
                Text("Los avisos del sistema dependen del permiso que concedas a Jarvis en macOS.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(store.notifications) { row in
                    HStack {
                        Label(Self.status(row.status), systemImage: "bell")
                            .font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        if ["queued", "delivered", "failed", "unknown"].contains(row.status), row.isCurrent {
                            Button("Ver chat") {
                                Task { if let navigation = await store.resumeNotification(row) { onNavigate(navigation) } }
                            }.disabled(store.blocksInteraction)
                        }
                    }
                }
            }
        }
    }
    private static func status(_ status: String) -> String {
        ["pending": "Pendiente", "queued": "En espera", "running": "En curso", "waiting": "En espera",
         "completed": "Completado", "failed": "Falló", "cancelled": "Cancelado", "unknown": "Sin confirmar",
         "dispatching": "Aviso en preparación", "delivered": "Aviso entregado", "resumed": "Chat abierto",
         "revoked": "Retirado", "expired": "Caducado", "interrupted": "Interrumpido"][status] ?? "Actividad local"
    }
}
