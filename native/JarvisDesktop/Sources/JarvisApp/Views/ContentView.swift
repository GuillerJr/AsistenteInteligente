import AppKit
import DesktopCore
import SwiftUI

@MainActor struct ContentView: View {
    @Bindable var store: ChatStore
    @State private var inspector: InspectorPresentation

    init(store: ChatStore, inspector: InspectorPresentation? = nil) {
        _store = Bindable(wrappedValue: store)
        _inspector = State(initialValue: inspector ?? InspectorPresentation())
    }

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

    private var navigation: some View {
        NavigationSplitView {
            SidebarView(store: store, onManage: {
                inspector.selection = "coordination"; inspector.isPresented = true
            })
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
                if let notice = store.memoryRecallNotice {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "exclamationmark.bubble")
                        Text(notice)
                        Spacer(minLength: 8)
                        Button("Ver memoria") { inspector.selection = "memory"; inspector.isPresented = true }
                    }
                    .font(.caption).foregroundStyle(.secondary)
                    .padding(.horizontal, 24).padding(.vertical, 8)
                    .background(.quaternary)
                }
                MessageListView(messages: store.messages, temporary: store.selected?.temporary ?? false,
                                streamingText: store.streamingText, canRead: store.canUseVoice && !store.voice.canStopDictation,
                                onRead: store.readMessage)
                if store.productInteractions.scope == store.selected?.scope,
                   !store.productInteractions.pendingQuestions.isEmpty {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Jarvis necesita una decisión").font(.headline)
                            ForEach(store.productInteractions.pendingQuestions) { question in
                                ProductInteractionQuestionView(store: store.productInteractions, interaction: question)
                                    .id("\(store.productInteractions.viewIdentity):\(question.id):\(question.revision)")
                            }
                        }.padding(.horizontal, 20).padding(.vertical, 12)
                    }.frame(maxHeight: 300)
                }
                if store.ready {
                    ApprovalListView(access: store.access)
                    if store.delegation.scope == store.selected?.scope {
                        DelegatedApprovalsView(coordination: store.delegation)
                    }
                }
                if let goal = store.delegation.goal, goal.scope == store.selected?.scope, !goal.settled {
                    HStack {
                        Text("Objetivo: \(goal.objective)").lineLimit(1).font(.caption)
                        Spacer()
                        Button("Ver tareas delegadas") { inspector.selection = "delegation"; inspector.isPresented = true }
                    }.padding(.horizontal, 24).padding(.vertical, 8)
                }
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
                        inspector.selection = "tools"; inspector.isPresented = true
                    }.disabled(!store.ready)
                    Button("MCP", systemImage: "puzzlepiece.extension") {
                        inspector.selection = "mcp"; inspector.isPresented = true
                    }.disabled(!store.ready)
                    Button("Memoria", systemImage: "tray") {
                        inspector.selection = "memory"; inspector.isPresented = true
                    }.disabled(!store.ready || store.selected == nil)
                    Menu("Gestionar", systemImage: "slider.horizontal.3") {
                        Button("Mac y navegador") { inspector.selection = "automation"; inspector.isPresented = true }
                        Button("Actividad y panel") { inspector.selection = "product"; inspector.isPresented = true }
                        Button("Conversaciones y colaboración") { inspector.selection = "coordination"; inspector.isPresented = true }
                        Button("Proyectos y GitHub") { inspector.selection = "projects"; inspector.isPresented = true }
                        Button("Contexto y ramas") { inspector.selection = "sessionCore"; inspector.isPresented = true }
                        Button("Clientes locales") { inspector.selection = "clients"; inspector.isPresented = true }
                        Button("Delegación y objetivos") { inspector.selection = "delegation"; inspector.isPresented = true }
                        Button("Turno y cola") { inspector.selection = "runInput"; inspector.isPresented = true }
                        Button("Agentes") { inspector.selection = "agents"; inspector.isPresented = true }
                        Button("Skills") { inspector.selection = "skills"; inspector.isPresented = true }
                        Button("Plugins") { inspector.selection = "plugins"; inspector.isPresented = true }
                        Button("Programador") { inspector.selection = "schedules"; inspector.isPresented = true }
                        Button("Procesos") { inspector.selection = "processes"; inspector.isPresented = true }
                        Button("Telegram") { inspector.selection = "telegram"; inspector.isPresented = true }
                    }.disabled(!store.ready)
                }
                ToolbarItem {
                    if store.busy || store.starting { ProgressView().controlSize(.small) }
                }
            }
        }
        .frame(minWidth: 0, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
    }

    private var chatBody: some View {
        HStack(spacing: 0) {
            navigation
            if inspector.isPresented {
                InspectorResizeHandle(presentation: inspector)
                inspectorPanel.frame(width: inspector.width)
            }
        }
        .coordinateSpace(name: "jarvis.chat.layout")
        .frame(minWidth: 720, minHeight: 520)
        .onDisappear { store.stopVoice() }
        .onChange(of: store.selected?.scope) { _, _ in store.stopVoice() }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.willCloseNotification)) { notification in
            if (notification.object as? NSWindow)?.identifier?.rawValue == "jarvis.main" {
                store.stopVoice(); store.telegram.clearSecretInput()
            }
        }
    }

    private var inspectorPanel: some View {
        InspectorViewport {
            VStack(spacing: 0) {
                HStack {
                    Picker("Panel", selection: $inspector.selection) {
                        Text("Mac y navegador").tag("automation")
                        Text("Actividad y panel").tag("product")
                        Text("Conversaciones y colaboración").tag("coordination")
                        Text("Proyectos y GitHub").tag("projects")
                        Text("Contexto y ramas").tag("sessionCore")
                        Text("Clientes locales").tag("clients")
                        Text("Delegación y objetivos").tag("delegation")
                        Text("Turno y cola").tag("runInput")
                        Text("Herramientas").tag("tools")
                        Text("Memoria").tag("memory")
                        Text("MCP").tag("mcp")
                        Text("Agentes").tag("agents")
                        Text("Skills").tag("skills")
                        Text("Plugins").tag("plugins")
                        Text("Programador").tag("schedules")
                        Text("Procesos").tag("processes")
                        Text("Telegram").tag("telegram")
                    }.pickerStyle(.menu)
                    Button { inspector.isPresented = false } label: { Image(systemName: "xmark") }
                        .buttonStyle(.plain).help("Cerrar panel").accessibilityLabel("Cerrar panel")
                }.padding()
                Divider()
                if inspector.selection == "automation" { AutomationControlView(store: store) }
                else if inspector.selection == "product" {
                    VStack(spacing: 0) {
                        HStack {
                            if store.productNotifications.permissionGranted {
                                Label("Avisos de macOS permitidos", systemImage: "bell").font(.caption).foregroundStyle(.secondary)
                            } else {
                                Button("Permitir avisos de macOS…") {
                                    Task { await store.productNotifications.requestPermission() }
                                }.disabled(!store.ready || store.productCoordination.snapshot?.isOwner != true
                                    || store.productNotifications.isRequestingPermission)
                            }
                            Spacer()
                        }.padding(.horizontal, 20).padding(.top, 12)
                        if let error = store.productNotifications.errorMessage {
                            Text(error).font(.caption).foregroundStyle(.orange).padding(.horizontal, 20)
                        }
                        ProductInteractionPanel(store: store.productInteractions,
                            onDraft: store.appendProductDraft,
                            onNavigate: { navigation in Task { await store.navigateProductNotification(navigation) } })
                    }
                }
                else if inspector.selection == "coordination" {
                    ProductCoordinationView(store: store.productCoordination,
                        onCreateSession: { id in Task { await store.createProductSession(workspaceID: id) } },
                        onSelectSession: { scope in Task { await store.selectSession(scope.sessionID) } },
                        onRunStarted: store.observeProductRun, canManage: store.canChangeSession,
                        canCreateSession: store.canNavigateSession)
                }
                else if inspector.selection == "projects" {
                    ProductProjectsView(store: store.productProjects,
                        onRunStarted: store.observeProductRun, canManage: store.canChangeSession)
                }
                else if inspector.selection == "mcp" { MCPView(store: store) }
                else if inspector.selection == "sessionCore" { SessionCoreView(store: store) }
                else if inspector.selection == "clients" { ClientIdentityView(store: store) }
                else if inspector.selection == "delegation" { DelegationView(store: store) }
                else if inspector.selection == "runInput" { RunInputView(store: store) }
                else if inspector.selection == "memory" { MemoryView(store: store, page: $inspector.memoryPage) }
                else if inspector.selection == "agents" { AgentsView(store: store) }
                else if inspector.selection == "skills" { SkillsView(store: store) }
                else if inspector.selection == "plugins" { PluginsView(store: store) }
                else if inspector.selection == "schedules" { SchedulesView(store: store) }
                else if inspector.selection == "processes" { ProcessesView(store: store) }
                else if inspector.selection == "telegram" { TelegramView(store: store) }
                else { CapabilitiesView(store: store) }
            }
        }
        .onChange(of: store.productCoordination.snapshot?.binding) {
            Task { await store.refreshProductWorkspace() }
        }
    }

}
