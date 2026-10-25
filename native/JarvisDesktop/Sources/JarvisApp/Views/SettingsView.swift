import DesktopCore
import SwiftUI

@MainActor struct SettingsView: View {
    let store: ChatStore
    let delegate: AppDelegate
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
                    Button("Abrir inicio de sesión") { Task { await store.login() } }.disabled(!store.ready || store.maintenance.blocksInteraction)
                    Button("Cerrar sesión") { Task { await store.logout() } }
                        .disabled(!store.connected || store.activeRunID != nil || store.maintenance.blocksInteraction)
                }
            }
            Section("Estado de esta base") {
                Text("Cliente nativo de desarrollo. Requiere el servicio Python del repositorio y el cliente oficial de Codex.")
                Text("Las capacidades disponibles se consultan en «Herramientas» y «Memoria». La paridad completa de la base permanece en construcción.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }.formStyle(.grouped).tabItem { Label("General", systemImage: "gearshape") }.tag("general")
            MaintenanceView(maintenance: store.maintenance, mayBegin: store.ready && !store.busy && store.activeRunID == nil && !store.voice.canStopDictation).tabItem { Label("Mantenimiento", systemImage: "externaldrive.badge.timemachine") }.tag("maintenance")
        }.frame(width: 760, height: 680)
            .background(SettingsWindowVisibilityBridge(delivery: visibilityDelivery) { visible in
                windowVisible = visible
                // Use the delivered value directly: teardown can outlive the
                // SwiftUI storage while still needing to retire this owner.
                store.maintenance.setVisible(maintenanceViewID, visible: visible && selectedTab == "maintenance")
            })
            .onChange(of: selectedTab) { _, _ in updateMaintenanceVisibility() }
            .onDisappear {
                windowVisible = false
                store.maintenance.setVisible(maintenanceViewID, visible: false)
            }
    }
    private func updateMaintenanceVisibility() {
        store.maintenance.setVisible(maintenanceViewID, visible: windowVisible && selectedTab == "maintenance")
    }
}
