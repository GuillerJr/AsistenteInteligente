import DesktopCore
import SwiftUI

@MainActor struct SettingsView: View {
    let store: ChatStore
    let delegate: AppDelegate
    let profiles: AppProfileCoordinator
    @AppStorage("jarvis.openShortcut") private var shortcut = "commandShiftJ"
    @State private var selectedTab = "general"
    @State private var windowVisible = false
    @State private var maintenanceViewID = UUID()
    @State private var visibilityDelivery = WindowVisibilityDelivery()

    var body: some View {
        TabView(selection: $selectedTab) {
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
                    Button("Abrir inicio de sesión") { Task { await store.login() } }.disabled(!store.ready || store.maintenance.blocksInteraction || store.memoryRestoration.blocksInteraction || store.restored.blocksInteraction || profiles.isOpening)
                    Button("Cerrar sesión") { Task { await store.logout() } }
                        .disabled(!store.connected || store.activeRunID != nil || store.maintenance.blocksInteraction || store.memoryRestoration.blocksInteraction || store.restored.blocksInteraction || profiles.isOpening)
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
            MaintenanceView(maintenance: store.maintenance, mayBegin: !store.memoryRestoration.blocksInteraction && !store.restored.blocksInteraction && store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation).tabItem { Label("Mantenimiento", systemImage: "externaldrive.badge.timemachine") }.tag("maintenance")
            RestoreProfileView(restored: store.restored, profiles: profiles, runtimeID: store.runtime.id, mayBegin: !store.memoryRestoration.blocksInteraction && !store.maintenance.blocksInteraction && store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation && !profiles.isOpening).tabItem { Label("Perfil restaurado", systemImage: "arrow.triangle.2.circlepath") }.tag("restored")
            RestoredMemoryView(restoration: store.memoryRestoration, agents: store.management.agents, mayBegin: store.ready && !store.starting && !store.handoffPreparing && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation && !profiles.isOpening && !store.maintenance.blocksInteraction && !store.restored.blocksInteraction).tabItem { Label("Memoria conservada", systemImage: "brain") }.tag("memory-restored")
        }.frame(width: 760, height: 680)
            .background(SettingsWindowVisibilityBridge(delivery: visibilityDelivery) { visible in
                windowVisible = visible
                // Use the delivered value directly: teardown can outlive the
                // SwiftUI storage while still needing to retire this owner.
                store.maintenance.setVisible(maintenanceViewID, visible: visible && selectedTab == "maintenance")
                store.restored.setVisible(maintenanceViewID, visible: visible && selectedTab == "restored")
                store.memoryRestoration.setVisible(maintenanceViewID, visible: visible && selectedTab == "memory-restored")
            })
            .onChange(of: selectedTab) { _, _ in updateMaintenanceVisibility() }
            .onDisappear {
                windowVisible = false
                store.maintenance.setVisible(maintenanceViewID, visible: false)
                store.restored.setVisible(maintenanceViewID, visible: false)
                store.memoryRestoration.setVisible(maintenanceViewID, visible: false)
            }
    }
    private func updateMaintenanceVisibility() {
        store.maintenance.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "maintenance")
        store.restored.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "restored")
        store.memoryRestoration.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "memory-restored")
    }
}
