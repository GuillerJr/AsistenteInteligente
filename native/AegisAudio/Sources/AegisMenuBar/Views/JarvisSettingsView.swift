import AegisAudioCore
import SwiftUI

enum JarvisSettingsSheet: Identifiable {
    case addModel
    case extensionReview(JarvisExtensionSheet)
    case browser
    case googleAccount(JarvisIntegrationSettings)

    var id: String {
        switch self {
        case .addModel: "add-model"
        case .extensionReview(let item): "extension-\(item.id.uuidString)"
        case .browser: "browser"
        case .googleAccount(let server): "google-\(server.id)"
        }
    }
}

struct JarvisSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    let restart: @MainActor () async throws -> Void
    var interactive = true
    @SceneStorage("jarvis.settings.section") private var section = JarvisSettingsSection.general.rawValue
    @State private var restarting = false
    @State private var presentedSheet: JarvisSettingsSheet?
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast

    private var selected: JarvisSettingsSection {
        JarvisSettingsSection(rawValue: section) ?? .general
    }

    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 8) {
                    Circle().fill(JarvisTheme.clay).frame(width: 10, height: 10).accessibilityHidden(true)
                    Text("Ajustes").font(.system(size: 14, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                }
                .padding(.horizontal, 20).padding(.top, 18).padding(.bottom, 8)
                // Own rows, so the selection is a warm wash instead of the system blue.
                ScrollView {
                    VStack(spacing: 1) {
                        ForEach(JarvisSettingsSection.allCases) { item in
                            Button {
                                JarvisPopupButton.dismissMenus()
                                section = item.rawValue
                            } label: {
                                Label(item.title, systemImage: item.symbol)
                            }
                            .buttonStyle(JarvisRowButtonStyle(selected: section == item.rawValue))
                            .accessibilityAddTraits(section == item.rawValue ? .isSelected : [])
                        }
                    }.padding(.horizontal, 10)
                }
            }
            .frame(width: 210)
            .background(JarvisTheme.sidebar)
            Rectangle().fill(JarvisTheme.hairline).frame(width: 1).accessibilityHidden(true)
            VStack(spacing: 0) {
                HStack(alignment: .top, spacing: 12) {
                    JarvisSectionHeader(title: selected.title, detail: selected.detail, symbol: selected.symbol)
                    if store.loading { ProgressView().controlSize(.small) }
                }
                .padding(.horizontal, 32).padding(.top, 26).padding(.bottom, 10)
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        content.disabled(!interactive || store.busy || restarting)
                    }
                    .frame(maxWidth: 680, alignment: .leading)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 32).padding(.bottom, 24).padding(.top, 8)
                }
                .scrollBounceBehavior(.basedOnSize)
                .id(selected)
                JarvisSeparator()
                footer.disabled(!interactive)
            }
            .background(HUDSurface(opaque: reduceTransparency || contrast == .increased))
            .foregroundStyle(JarvisTheme.ink)
        }
        .frame(minWidth: JarvisInterfaceStyle.settingsMinimumSize.width, idealWidth: 920,
               minHeight: JarvisInterfaceStyle.settingsMinimumSize.height, idealHeight: 660)
        .tint(HUDStyle.accent(for: .active))
        .buttonStyle(MenuBarSmallButtonStyle())
        .navigationTitle("Configuración de Jarvis")
        .onChange(of: section) { _, _ in JarvisPopupButton.dismissMenus() }
        .onDisappear { JarvisPopupButton.dismissMenus() }
        .sheet(item: $presentedSheet) { sheet in
            switch sheet {
            case .addModel:
                JarvisAddModelView(store: store)
            case .extensionReview(let item):
                JarvisExtensionReviewView(item: item, store: store)
            case .browser:
                JarvisBrowserSetupView(store: store)
            case .googleAccount(let server):
                JarvisAccountsGoogleView(store: store, server: server)
            }
        }
        .task {
            guard interactive else { return }
            await model.refreshPrivacyCapabilities()
            await store.refresh()
        }
    }

    @ViewBuilder private var content: some View {
        switch selected {
        case .general: JarvisGeneralSettingsView(model: model, store: store)
        case .accounts: JarvisAccountsSettingsView(store: store, present: { presentedSheet = $0 })
        case .telegram: JarvisTelegramSettingsView()
        case .models: JarvisModelsSettingsView(store: store, openAddModel: { presentedSheet = .addModel })
        case .extensions: JarvisExtensionsSettingsView(store: store, present: {
            presentedSheet = .extensionReview($0)
        })
        case .voice: JarvisVoiceSettingsView(model: model)
        case .privacy: JarvisPrivacySettingsView(model: model, store: store)
        case .devices: JarvisDevicesSettingsView(store: store, openAccounts: {
            section = JarvisSettingsSection.accounts.rawValue
        })
        case .diagnostics: JarvisDiagnosticsSettingsView(model: model, store: store, restart: restart)
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
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                    if store.operationState == "awaiting_authorization" {
                        Button("Cancelar") { Task { await store.cancelOperation() } }
                    }
                } else {
                    Label("Configuración en este Mac", systemImage: "lock.shield")
                        .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                }
                Spacer()
                Button("Actualizar", systemImage: "arrow.clockwise") {
                    Task { await model.refreshDaemon(); await store.refresh() }
                }
                .disabled(store.busy || restarting || store.loading)
                if store.restartRequired {
                    Button("Aplicar y reiniciar") {
                        Task {
                            restarting = true
                            defer { restarting = false }
                            do {
                                try await restart()
                                await model.refreshDaemon()
                                await store.refresh()
                                if !store.restartRequired {
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
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                if let detail {
                    Text(detail).font(.system(size: 12)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            content().font(.system(size: 12.5))
                .toggleStyle(JarvisSwitchStyle())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .voiceEnrollmentCard()
    }
}
