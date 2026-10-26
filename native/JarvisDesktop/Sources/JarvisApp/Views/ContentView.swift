import AppKit
import SwiftUI

struct ContentView: View {
    @Bindable var store: ChatStore
    @State private var showInspector = false
    @State private var inspectorTab = "tools"

    var body: some View {
        Group {
            if store.isRecoveryOnly {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Recuperación local").font(.title2)
                    Text(store.statusText)
                    Text("Este inicio conserva cerradas las conversaciones y las tareas para recuperar sus archivos. No conecta cuentas. Abre Ajustes para revisar el perfil y preparar copias en carpetas nuevas.")
                    SettingsLink { Text("Abrir Mantenimiento y Recuperación…") }
                    if let error = store.errorMessage { Text(error).foregroundStyle(.orange).textSelection(.enabled) }
                    if store.starting { ProgressView() }
                }.padding(32).frame(minWidth: 720, minHeight: 520)
            } else { chatBody }
        }
    }

    private var chatBody: some View {
        NavigationSplitView {
            SidebarView(store: store)
                .navigationSplitViewColumnWidth(min: 200, ideal: 245, max: 320)
        } detail: {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    Circle().fill(store.connected ? .green : .secondary).frame(width: 7, height: 7)
                    Text(store.statusText).font(.callout).foregroundStyle(.secondary)
                    if store.selected != nil { Text("· \(store.selectedAgentName)").font(.caption).foregroundStyle(.secondary) }
                    if let selected = store.selected, selected.archived {
                        Text("Archivada").font(.caption).foregroundStyle(.secondary)
                        Button("Restaurar") { Task { await store.setArchived(selected, archived: false) } }
                            .disabled(!store.canChangeSession)
                    }
                    Spacer()
                    if !store.connected {
                        Button("Conectar ChatGPT") { Task { await store.login() } }.disabled(!store.ready)
                    }
                    if !store.ready && !store.starting && !store.isShuttingDown {
                        Button("Reconectar") { Task { await store.reconnect() } }
                    }
                }.padding(.horizontal, 24).padding(.vertical, 12)
                Divider()
                MessageListView(messages: store.messages, temporary: store.selected?.temporary ?? false,
                                streamingText: store.streamingText, canRead: store.canUseVoice && !store.voice.canStopDictation,
                                onRead: store.readMessage)
                ApprovalListView(access: store.access)
                if !store.receipts.isEmpty {
                    ReceiptListView(receipts: store.receipts)
                        .padding(.horizontal, 24)
                }
                if let error = store.access.errorMessage {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle")
                        Text(error).textSelection(.enabled)
                        Spacer()
                        Button("Ocultar") { store.access.errorMessage = nil }
                    }.font(.callout).foregroundStyle(.red).padding(12)
                }
                if let error = store.management.errorMessage {
                    HStack {
                        Text(error).textSelection(.enabled)
                        Spacer()
                        Button("Ocultar") { store.management.errorMessage = nil }
                    }.font(.callout).foregroundStyle(.red).padding(12)
                }
                if let error = store.errorMessage {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle")
                        Text(error).textSelection(.enabled)
                        Spacer()
                        Button("Ocultar") { store.errorMessage = nil }
                    }
                    .font(.callout).foregroundStyle(.red)
                    .padding(12).background(.quaternary, in: .rect(cornerRadius: 10))
                    .padding(.horizontal, 24)
                }
                if store.lostTemporaryRun {
                    Button("Entendido: dejar el resultado sin confirmar") { store.acknowledgeLostTemporaryRun() }
                        .padding(.horizontal, 20)
                }
                ComposerView(store: store).padding(20)
            }
            .navigationTitle(store.selected?.title ?? "Jarvis")
            .toolbar {
                ToolbarItemGroup {
                    Button("Herramientas", systemImage: "wrench.and.screwdriver") {
                        inspectorTab = "tools"; showInspector = true
                    }.disabled(!store.ready)
                    Button("MCP", systemImage: "puzzlepiece.extension") {
                        inspectorTab = "mcp"; showInspector = true
                    }.disabled(!store.ready)
                    Button("Memoria", systemImage: "tray") {
                        inspectorTab = "memory"; showInspector = true
                    }.disabled(!store.ready || store.selected == nil)
                    Menu("Gestionar", systemImage: "slider.horizontal.3") {
                        Button("Agentes") { inspectorTab = "agents"; showInspector = true }
                        Button("Skills") { inspectorTab = "skills"; showInspector = true }
                        Button("Programador") { inspectorTab = "schedules"; showInspector = true }
                        Button("Procesos") { inspectorTab = "processes"; showInspector = true }
                        Button("Telegram") { inspectorTab = "telegram"; showInspector = true }
                    }.disabled(!store.ready)
                }
                ToolbarItem {
                    if store.busy || store.starting { ProgressView().controlSize(.small) }
                }
            }
        }
        .inspector(isPresented: $showInspector) {
            VStack(spacing: 0) {
                Picker("Panel", selection: $inspectorTab) {
                    Text("Herramientas").tag("tools")
                    Text("Memoria").tag("memory")
                    Text("MCP").tag("mcp")
                    Text("Agentes").tag("agents")
                    Text("Skills").tag("skills")
                    Text("Programador").tag("schedules")
                    Text("Procesos").tag("processes")
                    Text("Telegram").tag("telegram")
                }.pickerStyle(.menu).padding()
                if inspectorTab == "mcp" { MCPView(store: store) }
                else if inspectorTab == "memory" { MemoryView(store: store) }
                else if inspectorTab == "agents" { AgentsView(store: store) }
                else if inspectorTab == "skills" { SkillsView(store: store) }
                else if inspectorTab == "schedules" { SchedulesView(store: store) }
                else if inspectorTab == "processes" { ProcessesView(store: store) }
                else if inspectorTab == "telegram" { TelegramView(store: store) }
                else { CapabilitiesView(store: store) }
            }
            .inspectorColumnWidth(min: 360, ideal: 420, max: 560)
        }
        .frame(minWidth: 720, minHeight: 520)
        .onDisappear { store.stopVoice() }
        .onChange(of: store.selected?.scope) { _, _ in store.stopVoice() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.willCloseNotification)) { notification in
            if (notification.object as? NSWindow)?.identifier?.rawValue == "jarvis.main" {
                store.stopVoice(); store.telegram.clearSecretInput()
            }
        }
    }
}
