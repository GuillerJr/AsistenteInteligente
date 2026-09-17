import AegisAudioCore
import AppKit
import SwiftUI

/// A lightweight companion to the HUD, sharing its surface, palette and actions.
struct MenuBarView: View {
    @Environment(\.openSettings) private var openSettings
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

    var body: some View {
        VStack(spacing: 0) {
            header
            separator
            ScrollView {
                VStack(spacing: 18) {
                    MenuBarStatusView(status: model.presentationStatus)
                    if model.presentationStatus.kind == .browserSelection {
                        browserSelection
                    }
                    MenuBarVoiceAction(model: model, interactive: interactive)
                    Button {
                        RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
                    } label: {
                        Label("Abrir chat", systemImage: "bubble.left.and.bubble.right")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!interactive)
                    quickActions
                    DisclosureGroup("Herramientas de desarrollo") {
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
                                .font(.system(size: 11)).foregroundStyle(.secondary)
                            if let terminalError { Text(terminalError).font(.system(size: 11)).foregroundStyle(.orange) }
                        }.padding(.top, 8).frame(maxWidth: .infinity, alignment: .leading)
                    }.font(.system(size: 12))
                    separator
                    MenuBarPermissionsView(model: model, interactive: interactive)
                    separator
                    MenuBarPreferencesView(model: model, interactive: interactive)
                }
                .padding(20)
            }
            .scrollBounceBehavior(.basedOnSize)
            .frame(maxHeight: maximumContentHeight)
            .fixedSize(horizontal: false, vertical: true)
            separator
            footer
        }
        .frame(width: 380)
        // MenuBarExtra owns the outer mask and border. A second rounded shell
        // exposes the host background in the corners and doubles the outline.
        .background(HUDSurface(opaque: reduceTransparency || highContrast))
        .preferredColorScheme(.dark)
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
            Image(systemName: "waveform")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(.white.opacity(0.85))
                .frame(width: 32, height: 32)
                .background(.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
                .accessibilityHidden(true)
            Text("Jarvis").font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.white.opacity(0.95))
            Text("ASISTENTE")
                .font(.system(size: 9, weight: .medium, design: .monospaced))
                .tracking(1.5)
                .foregroundStyle(.white.opacity(0.5))
            Spacer(minLength: 4)
            MenuBarIconButton(symbol: "gearshape", title: "Configuración de Jarvis") {
                RequestedWindowFocus.open("settings") { openSettings() }
            }
            .disabled(!interactive)
            MenuBarIconButton(symbol: "arrow.clockwise", title: model.runtimeProbeInProgress
                              ? "Actualizando estado" : "Actualizar estado") {
                Task {
                    await model.refreshDaemon()
                    await model.refreshPrivacyCapabilities()
                }
            }
            .disabled(!interactive || model.runtimeProbeInProgress)
        }
        .padding(.horizontal, 20)
        .frame(height: 64)
    }

    private var separator: some View {
        Rectangle().fill(.white.opacity(highContrast ? 0.3 : 0.07))
            .frame(height: 1)
            .accessibilityHidden(true)
    }

    private var quickActions: some View {
        HStack(spacing: 8) {
            quickAction("Imagen", symbol: "photo") {
                guard let url = ImageFilePicker.chooseImage() else { return }
                Task { await model.startImageVoiceTurn(fileURL: url) }
            }
            .disabled(!interactive || !model.canStartVoiceTurn)
            quickAction("Pantalla", symbol: "macbook") {
                Task { await model.startScreenVoiceTurn() }
            }
            .disabled(!interactive || !model.canStartScreenTurn)
            quickAction("Abrir HUD", symbol: "rectangle.on.rectangle") {
                HUDPanelController.shared.show(model: model)
            }
            .disabled(!interactive)
        }
    }

    private func quickAction(_ title: String, symbol: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 7) {
                Image(systemName: symbol).font(.system(size: 16, weight: .medium))
                Text(title).font(.system(size: 11, weight: .medium))
            }
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active)))
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
        HStack {
            Label(model.voiceShortcutAvailable ? "\(JarvisVoiceShortcut.current.title) · Hablar" : "Atajo no disponible",
                  systemImage: "keyboard")
                .font(.system(size: 11))
                .foregroundStyle(.white.opacity(0.55))
            Spacer()
            Button("Salir") { NSApplication.shared.terminate(nil) }
                .buttonStyle(MenuBarSmallButtonStyle())
                .disabled(!interactive)
                .help("Salir de Jarvis")
        }
        .padding(.horizontal, 20)
        .frame(height: 48)
    }
}
