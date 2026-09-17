import AegisAudioCore
import SwiftUI

struct JarvisSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    let restart: @MainActor () async throws -> Void
    @SceneStorage("jarvis.settings.section") private var section = JarvisSettingsSection.general.rawValue
    @State private var restarting = false
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast

    private var selected: JarvisSettingsSection {
        JarvisSettingsSection(rawValue: section) ?? .general
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                Label("Jarvis", systemImage: "waveform")
                    .font(.system(size: 17, weight: .semibold))
                    .padding(20)
                JarvisSeparator()
                List(selection: Binding<String?>(get: { section }, set: { if let value = $0 { section = value } })) {
                    ForEach(JarvisSettingsSection.allCases) { item in
                        Label(item.title, systemImage: item.symbol).tag(item.rawValue)
                            .padding(.vertical, 5)
                    }
                }
                .listStyle(.sidebar)
            }
            .frame(width: 220)
            .background(.regularMaterial)
            Divider()
            VStack(spacing: 0) {
                HStack(alignment: .top, spacing: 12) {
                    JarvisSectionHeader(title: selected.title, detail: selected.detail, symbol: selected.symbol)
                    if store.loading { ProgressView().controlSize(.small) }
                }
                .padding(24)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        content.disabled(store.busy || restarting)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(24)
                }
                .scrollBounceBehavior(.basedOnSize)
                .id(selected)
                Divider()
                footer
            }
            .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
            .foregroundStyle(.white.opacity(0.95))
        }
        .frame(minWidth: JarvisInterfaceStyle.settingsMinimumSize.width, idealWidth: 920,
               minHeight: JarvisInterfaceStyle.settingsMinimumSize.height, idealHeight: 660)
        .preferredColorScheme(.dark)
        .tint(HUDStyle.accent(for: .active))
        .buttonStyle(MenuBarSmallButtonStyle())
        .navigationTitle("Configuración de Jarvis")
        .task {
            await model.refreshPrivacyCapabilities()
            await store.refresh()
        }
    }

    @ViewBuilder private var content: some View {
        switch selected {
        case .general: JarvisGeneralSettingsView(model: model, store: store)
        case .accounts: JarvisAccountsSettingsView(store: store)
        case .models: JarvisModelsSettingsView(store: store)
        case .voice: JarvisVoiceSettingsView(model: model)
        case .privacy: JarvisPrivacySettingsView(model: model, store: store)
        case .devices: JarvisDevicesSettingsView(store: store, openAccounts: {
            section = JarvisSettingsSection.accounts.rawValue
        })
        case .diagnostics: JarvisDiagnosticsSettingsView(model: model, store: store)
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let message = store.message {
                HStack(alignment: .top, spacing: 12) {
                    Label(message, systemImage: store.failed ? "exclamationmark.circle" : "checkmark.circle")
                        .font(.system(size: 12)).lineSpacing(3)
                        .foregroundStyle(HUDStyle.accent(for: store.failed ? .warning : .success))
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityLabel(message)
                    Spacer(minLength: 0)
                    MenuBarIconButton(symbol: "xmark", title: "Cerrar aviso de configuración") { store.message = nil }
                }
            }
            HStack(spacing: 10) {
                if store.busy || restarting {
                    ProgressView().controlSize(.small)
                    Text(restarting ? "Reiniciando servicio…" : store.operationState == "awaiting_authorization"
                         ? "Completa la autorización en tu navegador" : "Procesando…")
                        .font(.system(size: 11)).foregroundStyle(.white.opacity(0.65))
                    if store.operationState == "awaiting_authorization" {
                        Button("Cancelar") { Task { await store.cancelOperation() } }
                    }
                } else {
                    Label("Configuración en este Mac", systemImage: "lock.shield")
                        .font(.system(size: 11)).foregroundStyle(.white.opacity(0.55))
                }
                Spacer()
                Button("Actualizar", systemImage: "arrow.clockwise") {
                    Task { await model.refreshDaemon(); await store.refresh() }
                }
                .disabled(store.busy || restarting || store.loading)
                if store.snapshot?.restart_required == true {
                    Button("Aplicar y reiniciar") {
                        Task {
                            restarting = true
                            defer { restarting = false }
                            do {
                                try await restart()
                                await model.refreshDaemon()
                                await store.refresh()
                                if store.snapshot?.restart_required == false {
                                    store.message = "Cambios aplicados."
                                }
                            } catch { store.fail("No se pudo reiniciar el servicio. Inténtalo nuevamente.") }
                        }
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(store.busy || restarting || model.pendingApproval != nil
                              || model.speakerModelTrainingState.isBusy
                              || model.voiceState.isBusy
                              || model.voiceState == .speaking || model.voiceState == .awaitingAuthorization
                              || model.speakerEnrollmentState.isBusy || model.wakeWordEnrollmentState.isBusy)
                }
            }
        }
        .padding(.horizontal, 24).padding(.vertical, 16)
    }
}

struct JarvisSettingsCard<Content: View>: View {
    let title: String
    var detail: String? = nil
    @ViewBuilder var content: () -> Content
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 14, weight: .semibold))
                if let detail {
                    Text(detail).font(.system(size: 12)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            content().font(.system(size: 12))
                .toggleStyle(JarvisSwitchStyle())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(18)
        .voiceEnrollmentCard()
    }
}
