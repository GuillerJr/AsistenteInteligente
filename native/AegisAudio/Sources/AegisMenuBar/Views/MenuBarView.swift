import AegisAudioCore
import AppKit
import SwiftUI

/// A lightweight companion to the HUD, sharing its surface, palette and actions.
struct MenuBarView: View {
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    let model: MenuBarModel
    var interactive = true
    var maximumContentHeight: CGFloat = 560
    var forceHighContrast = false
    @State private var terminalError: String?
    @State private var openingTerminal = false

    private var highContrast: Bool { forceHighContrast || contrast == .increased }

    private var contentHeight: CGFloat {
        // Leave room for the fixed header/footer and the menu's screen margin.
        let available = (NSScreen.main?.visibleFrame.height ?? 800) - 138
        return min(maximumContentHeight, max(240, available))
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            separator
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    MenuBarStatusView(status: model.presentationStatus).padding(.horizontal, 6)
                    if model.presentationStatus.kind == .browserSelection {
                        browserSelection
                    }
                    MenuBarVoiceAction(model: model, interactive: interactive)
                    quickActions
                    separator
                    MenuBarPermissionsView(model: model, interactive: interactive)
                    DisclosureGroup {
                        MenuBarPreferencesView(model: model, interactive: interactive).padding(.horizontal, 6)
                    } label: {
                        Label("Preferencias", systemImage: "slider.horizontal.3")
                    }
                    .font(.system(size: 13))
                    .disclosureGroupStyle(MenuBarDisclosureStyle())
                    DisclosureGroup {
                        VStack(alignment: .leading, spacing: 8) {
                            Button {
                                openingTerminal = true
                                terminalError = nil
                                Task {
                                    defer { openingTerminal = false }
                                    do { try await JarvisTerminalLauncher.open() }
                                    catch { terminalError = "No pude abrir la CLI. Comprueba que Jarvis esté instalado y Terminal esté disponible." }
                                }
                            } label: { Label(openingTerminal ? "Abriendo terminal…" : "Abrir terminal", systemImage: "terminal") }
                                .buttonStyle(MenuBarSmallButtonStyle())
                                .disabled(!interactive || openingTerminal)
                            Text("Sesión de ingeniería con la CLI de Jarvis.")
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                            if let terminalError { Text(terminalError).font(.system(size: 11)).foregroundStyle(JarvisTheme.warning) }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    } label: {
                        Label("Herramientas de desarrollo", systemImage: "terminal")
                    }
                    .font(.system(size: 13))
                    .disclosureGroupStyle(MenuBarDisclosureStyle())
                }
                .padding(10)
            }
            .scrollBounceBehavior(.basedOnSize)
            .scrollIndicators(.visible)
            // Expansion changes the scrollable content, never the host window.
            .frame(height: contentHeight)
            separator
            footer
        }
        .frame(width: 340)
        // MenuBarExtra owns the outer mask and border. A second rounded shell
        // exposes the host background in the corners and doubles the outline.
        .background(JarvisTheme.sidebar)
        .tint(HUDStyle.accent(for: .active))
        .task {
            guard interactive else { return }
            await model.refreshPrivacyCapabilities()
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) { _ in
            guard interactive else { return }
            Task { await model.refreshPrivacyCapabilities() }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Circle().fill(JarvisTheme.clay).frame(width: 12, height: 12).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text("Jarvis").font(.system(size: 14, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                Text(model.pendingAttentionCount > 0
                     ? "\(model.presentationStatus.compactTitle) · \(model.pendingAttentionCount) pendientes"
                     : model.presentationStatus.compactTitle)
                    .font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary).lineLimit(1)
            }
            Spacer(minLength: 4)
            MenuBarIconButton(symbol: "arrow.clockwise", title: model.runtimeProbeInProgress
                              ? "Actualizando estado" : "Actualizar estado") {
                Task {
                    await model.refreshDaemon()
                    await model.refreshPrivacyCapabilities()
                }
            }
            .disabled(!interactive || model.runtimeProbeInProgress)
        }
        .padding(.horizontal, 16)
        .frame(height: 56)
    }

    private var separator: some View {
        Rectangle().fill(highContrast ? JarvisTheme.hairlineStrong : JarvisTheme.hairline)
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    private var quickActions: some View {
        VStack(spacing: 1) {
            menuRow("Abrir chat", symbol: "bubble.left.and.bubble.right", trailing: "⇧⌘J") {
                RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
            }
            if model.pendingAttentionCount > 0 {
                menuRow("Requiere tu atención", symbol: "bell", trailing: "\(model.pendingAttentionCount)") {
                    model.chat.showHistory = true
                    RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
                }
            }
            menuRow("Pregunta rápida", symbol: "sparkles", trailing: "⌃⇧J") {
                QuickEntryPanelController.shared.toggle(model: model)
            }
            menuRow("Preguntar sobre una imagen", symbol: "photo") {
                guard let url = ImageFilePicker.chooseImage() else { return }
                Task { await model.startImageVoiceTurn(fileURL: url) }
            }
            .disabled(!interactive || !model.canStartVoiceTurn)
            menuRow("Preguntar sobre la pantalla", symbol: "macbook") {
                Task { await model.startScreenVoiceTurn() }
            }
            .disabled(!interactive || !model.canStartScreenTurn)
            menuRow("Abrir HUD", symbol: "rectangle.on.rectangle") {
                HUDPanelController.shared.show(model: model)
            }
        }
        .disabled(!interactive)
    }

    private func menuRow(_ title: String, symbol: String, trailing: String? = nil,
                         action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: symbol).frame(width: 18).foregroundStyle(JarvisTheme.inkSecondary)
                Text(title)
                Spacer(minLength: 4)
                if let trailing {
                    Text(trailing).font(.system(size: 12)).foregroundStyle(JarvisTheme.inkMuted)
                }
            }
        }
        .buttonStyle(JarvisRowButtonStyle())
    }

    @ViewBuilder private var browserSelection: some View {
        if let selection = model.pendingBrowserSelection {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140))], spacing: 8) {
                ForEach(selection.options, id: \.bundleIdentifier) { option in
                    Button {
                        Task { await model.selectBrowser(bundleIdentifier: option.bundleIdentifier) }
                    } label: {
                        HStack {
                            Image(systemName: "globe")
                            Text(option.name).lineLimit(1)
                            Spacer(minLength: 0)
                            Image(systemName: "arrow.up.right").font(.system(size: 10))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .warning)))
                    .disabled(!interactive)
                    .help("Continuar en " + option.name)
                }
            }
        }
    }

    private var footer: some View {
        VStack(spacing: 1) {
            menuRow("Ajustes…", symbol: "gearshape", trailing: "⌘,") {
                RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
            }
            menuRow("Salir de Jarvis", symbol: "power", trailing: "⌘Q") { NSApplication.shared.terminate(nil) }
            Text(model.voiceShortcutAvailable ? "\(JarvisVoiceShortcut.current.title) para hablar" : "Atajo de voz no disponible")
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 10).padding(.top, 6)
        }
        .disabled(!interactive)
        .padding(10)
    }
}
