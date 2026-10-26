import DesktopCore
import SwiftUI

@MainActor struct SettingsView: View {
    let store: ChatStore
    let delegate: AppDelegate
    let profiles: AppProfileCoordinator
    @AppStorage("jarvis.openShortcut") private var shortcut = "commandShiftJ"
    @State private var windowVisible = false
    @State private var maintenanceViewID = UUID()
    @State private var visibilityDelivery = WindowVisibilityDelivery()
    private var navigation: SettingsOwnerNavigation { delegate.settingsNavigation }
    private var selectedTab: String { navigation.selectedTab }
    private var notices: [OwnerDecisionNotice] { delegate.ownerDecisionNotices }

    var body: some View {
        VStack(spacing: 0) {
            if let notice = navigation.attention(in: notices) { OwnerDecisionBanner(notice: notice, navigation: navigation) }
        TabView(selection: Binding(get: { navigation.selectedTab }, set: { navigation.select($0) })) {
        Form {
            Section("Escritorio") {
                Picker("Abrir Jarvis", selection: $shortcut) {
                    Text("⌘⇧J").tag("commandShiftJ")
                    Text("⌥Espacio").tag("optionSpace")
                    Text("Desactivado").tag("disabled")
                }
                .onChange(of: shortcut) { _, value in delegate.installShortcut(value) }
                if let error = delegate.shortcutError { Text(error).foregroundStyle(.red) }
                Text("Cerrar la ventana mantiene Jarvis en la barra de menús. Usa «Salir de Jarvis» para cerrar también el servicio.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Cuenta de ChatGPT") {
                Text(store.statusText)
                HStack {
                    Button("Abrir inicio de sesión") { Task { await store.login() } }.disabled(store.isRecoveryOnly || !store.ready || store.maintenance.blocksInteraction || store.channelRestoration.blocksInteraction || store.memoryRestoration.blocksInteraction || store.recovery.blocksInteraction || store.restored.blocksInteraction || profiles.isOpening)
                    Button("Cerrar sesión") { Task { await store.logout() } }
                        .disabled(store.isRecoveryOnly || !store.connected || store.activeRunID != nil || store.maintenance.blocksInteraction || store.channelRestoration.blocksInteraction || store.memoryRestoration.blocksInteraction || store.recovery.blocksInteraction || store.restored.blocksInteraction || profiles.isOpening)
                }
            }
            Section("Perfil actual") {
                if let path = store.launchOptions?.state { Text(path).font(.caption).textSelection(.enabled) }
                Text("La elección del perfil es local a esta sesión de la app; no cambia el perfil de un próximo inicio.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Estado de esta base") {
                Text("Cliente nativo de desarrollo. Requiere el servicio Python del repositorio y el cliente oficial de Codex.")
                Text("Las capacidades disponibles se consultan en «Herramientas» y «Memoria». La paridad completa de la base permanece en construcción.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped).tabItem { Label("General", systemImage: "gearshape") }.tag("general")
            MaintenanceView(maintenance: store.maintenance, navigation: navigation, mayBegin: !store.channelRestoration.blocksInteraction && !store.memoryRestoration.blocksInteraction && !store.recovery.blocksInteraction && !store.restored.blocksInteraction && store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation).tabItem { Label("Mantenimiento", systemImage: "externaldrive.badge.timemachine") }.tag("maintenance")
            if !store.isRecoveryOnly {
            RestoreProfileView(restored: store.restored, navigation: navigation, profiles: profiles, runtimeID: store.runtime.id, mayBegin: !store.channelRestoration.blocksInteraction && !store.memoryRestoration.blocksInteraction && !store.recovery.blocksInteraction && !store.maintenance.blocksInteraction && store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation && !profiles.isOpening).tabItem { Label("Perfil restaurado", systemImage: "arrow.triangle.2.circlepath") }.tag("restored")
            RestoredChannelsView(channels: store.channelRestoration, telegram: store.telegram, navigation: navigation, mayBegin: store.ready && !store.starting && !store.busy && !store.handoffPreparing && !profiles.isOpening && store.activeRunID == nil && !store.voice.canStopDictation && !store.maintenance.blocksInteraction && !store.memoryRestoration.blocksInteraction && !store.restored.blocksInteraction && !store.recovery.blocksInteraction).tabItem { Label("Canales", systemImage: "point.3.connected.trianglepath.dotted") }.tag("channels-restored")
            RestoredMemoryView(restoration: store.memoryRestoration, navigation: navigation, agents: store.management.agents, mayBegin: store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation && !profiles.isOpening && !store.maintenance.blocksInteraction && !store.channelRestoration.blocksInteraction && !store.restored.blocksInteraction && !store.recovery.blocksInteraction).tabItem { Label("Memoria conservada", systemImage: "brain") }.tag("memory-restored")
            }
            RecoveryCenterView(recovery: store.recovery, navigation: navigation, workspacePath: store.access.workspacePath, profilePath: store.launchOptions?.state, verificationID: store.maintenance.verification?.id, mayBegin: store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation && !profiles.isOpening && !store.maintenance.blocksInteraction && !store.channelRestoration.blocksInteraction && !store.memoryRestoration.blocksInteraction && !store.restored.blocksInteraction).tabItem { Label("Recuperación", systemImage: "externaldrive.badge.checkmark") }.tag("recovery")
        }
        }.frame(width: 760, height: 680)
            .onAppear { navigation.configure(runtimeID: store.runtime.id); navigation.detect(notices, visible: windowVisible) }
            .onChange(of: notices) { _, rows in navigation.detect(rows, visible: windowVisible) }
            .background(SettingsWindowVisibilityBridge(delivery: visibilityDelivery) { visible in
                let justOpened = visible && !windowVisible
                windowVisible = visible
                if justOpened, let notice = navigation.attention(in: notices) { navigation.reveal(notice) }
                // Use the delivered value directly: teardown can outlive the
                // SwiftUI storage while still needing to retire this owner.
                store.maintenance.setVisible(maintenanceViewID, visible: visible && selectedTab == "maintenance")
                store.restored.setVisible(maintenanceViewID, visible: visible && selectedTab == "restored")
                store.channelRestoration.setVisible(maintenanceViewID, visible: visible && selectedTab == "channels-restored")
                store.memoryRestoration.setVisible(maintenanceViewID, visible: visible && selectedTab == "memory-restored")
                store.recovery.setVisible(maintenanceViewID, visible: visible && selectedTab == "recovery")
            })
            .onChange(of: selectedTab) { _, _ in updateMaintenanceVisibility() }
            .onDisappear {
                windowVisible = false
                store.maintenance.setVisible(maintenanceViewID, visible: false)
                store.restored.setVisible(maintenanceViewID, visible: false)
                store.channelRestoration.setVisible(maintenanceViewID, visible: false)
                store.memoryRestoration.setVisible(maintenanceViewID, visible: false)
                store.recovery.setVisible(maintenanceViewID, visible: false)
            }
    }
    private func updateMaintenanceVisibility() {
        store.maintenance.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "maintenance")
        store.restored.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "restored")
        store.channelRestoration.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "channels-restored")
        store.memoryRestoration.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "memory-restored")
        store.recovery.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "recovery")
    }
}
