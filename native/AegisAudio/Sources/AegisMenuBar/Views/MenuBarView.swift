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
                    quickActions
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
        .background(HUDSurface(opaque: reduceTransparency || highContrast))
        .clipShape(RoundedRectangle(cornerRadius: HUDStyle.cornerRadius, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: HUDStyle.cornerRadius, style: .continuous)
                .strokeBorder(.white.opacity(highContrast ? 0.55 : 0.16), lineWidth: 1)
                .allowsHitTesting(false)
        }
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
            MenuBarIconButton(symbol: "person.crop.circle", title: "Configurar identidad de voz") {
                openWindow(id: "speaker-enrollment")
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
            Label(model.voiceShortcutAvailable ? "⌃⇧ Espacio · Hablar" : "Atajo no disponible",
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
