#if DEBUG
import AppKit
import AegisAudioCore
import SwiftUI

/// An isolated visual fixture: no IPC, prompts, audio or persisted test settings.
@MainActor
final class PresentationPreviewController {
    static let shared = PresentationPreviewController()
    private var window: NSWindow?

    func show(model: MenuBarModel, mode: NotchPreviewMode) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 700, height: 860),
                              styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        window.title = "Pruebas visuales de Jarvis"
        window.isReleasedWhenClosed = false
        window.contentView = NSHostingView(rootView: PresentationPreviewView(model: model, mode: mode))
        window.center()
        self.window = window
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
}

private struct PresentationPreviewView: View {
    let model: MenuBarModel
    @State var mode: NotchPreviewMode
    @State private var reduceMotion = true
    @State private var highContrast = false
    @State private var compact = false
    @State private var notch = NotchPresentationState.preview()

    var body: some View {
        ScrollView {
            VStack(spacing: 12) {
                Text("Datos ficticios · sin micrófono ni conexión al servicio")
                    .font(.headline)
                Picker("Estado", selection: $mode) {
                    ForEach(NotchPreviewMode.allCases.filter { $0 != .cycle }, id: \.self) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .onChange(of: mode) { _, value in model.applyNotchPreview(value) }
                HStack {
                    Toggle("Reducir movimiento", isOn: $reduceMotion)
                    Toggle("Alto contraste", isOn: $highContrast)
                    Toggle("Panel compacto", isOn: $compact)
                }
                Button("Abrir HUD independiente") { HUDPanelController.shared.show(model: model) }
                NotchPresenceView(model: model, presentation: notch, forceReduceMotion: reduceMotion)
                    .frame(width: 348, height: 94)
                    .background(.black.opacity(0.85))
                HUDView(model: model, close: {}, interactive: false,
                        forceReduceMotion: reduceMotion, forceHighContrast: highContrast)
                    .frame(width: compact ? 340 : AssistantPanelLayout.hudPreferredSize.width,
                           height: compact ? 430 : AssistantPanelLayout.hudPreferredSize.height)
            }
            .padding(20)
        }
        .preferredColorScheme(.dark)
    }
}

extension NotchPresentationState {
    static func preview() -> NotchPresentationState {
        let state = NotchPresentationState()
        state.notchWidth = 192
        state.notchHeight = 32
        state.wingWidth = 34
        return state
    }
}
#endif
