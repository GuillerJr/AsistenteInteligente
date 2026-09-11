import AegisAudioCore
import SwiftUI

struct HUDView: View {
    let model: MenuBarModel
    let close: () -> Void
    var interactive = true
    var forceReduceMotion = false
    var forceHighContrast = false

    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorSchemeContrast) private var contrast
    @Environment(\.openWindow) private var openWindow

    private var status: AssistantPresentation { model.presentationStatus }
    private var accentColor: Color { status.tone.color }
    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }
    private var highContrast: Bool { contrast == .increased || forceHighContrast }

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                Circle()
                    .stroke(accentColor.opacity(0.2), lineWidth: 1)
                    .padding(32)
                    .accessibilityHidden(true)

                NodeSphereView(
                    activity: status.kind == .working || status.kind == .processing ? model.hudActivity : [:],
                    voiceLevel: model.voiceActivityLevel,
                    listeningPulse: status.kind == .listening || status.kind == .followingUp,
                    interrupting: status.kind == .interrupting,
                    reduceMotion: reduceMotion || !status.isAnimated
                )
                .padding(.vertical, 60)
                .accessibilityHidden(true)
                .allowsHitTesting(false)

                VStack(spacing: 12) {
                    header
                    Spacer(minLength: 0)
                    ScrollView {
                        statusCard
                            .frame(minHeight: min(280, geometry.size.height * 0.55), alignment: .bottom)
                    }
                    .scrollBounceBehavior(.basedOnSize)
                    .frame(maxHeight: min(280, geometry.size.height * 0.55), alignment: .bottom)
                    .defaultScrollAnchor(.top)
                }
                .padding(16)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background {
            RoundedRectangle(cornerRadius: 26)
                .fill(.black.opacity(reduceTransparency || highContrast ? 1 : 0.92))
        }
        .overlay {
            RoundedRectangle(cornerRadius: 26)
                .strokeBorder(.white.opacity(highContrast ? 0.65 : 0.2), lineWidth: 1)
                .allowsHitTesting(false)
        }
        .preferredColorScheme(.dark)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: status)
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "circle.hexagongrid.fill")
                .foregroundStyle(accentColor)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text("JARVIS")
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .tracking(2)
                Text("Estado del asistente")
                    .font(.system(size: 11))
                    .foregroundStyle(.white.opacity(0.8))
            }
            Spacer(minLength: 4)
            Button(action: close) {
                Image(systemName: "xmark")
                    .font(.system(size: 13, weight: .semibold))
                    .frame(width: 32, height: 32)
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .background(.white.opacity(0.1), in: Circle())
            .keyboardShortcut(.cancelAction)
            .help("Cerrar HUD (Esc). No cancela el turno.")
            .accessibilityLabel("Cerrar HUD")
            .accessibilityHint("Cierra el panel sin cancelar la tarea en curso.")
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(.black.opacity(0.9), in: RoundedRectangle(cornerRadius: 16))
    }

    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 8) {
                Label(status.title, systemImage: status.symbol)
                    .font(.system(size: 18, weight: .semibold, design: .rounded))
                    .foregroundStyle(accentColor)
                    .fixedSize(horizontal: false, vertical: true)
                Text(status.detail)
                    .font(.system(size: 13))
                    .foregroundStyle(.white.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(status.accessibilityDescription)

            if status.kind == .browserSelection, let selection = model.pendingBrowserSelection {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 120))], spacing: 8) {
                    ForEach(selection.options, id: \.bundleIdentifier) { option in
                        Button(option.name) {
                            Task { await model.selectBrowser(bundleIdentifier: option.bundleIdentifier) }
                        }
                        .buttonStyle(.bordered)
                        .disabled(!interactive)
                        .controlSize(.regular)
                        .frame(maxWidth: .infinity)
                        .help("Continuar en \(option.name)")
                    }
                }
            }
            if status.kind == .approval, model.pendingApproval != nil {
                Button("Revisar aprobación") { openWindow(id: "approval") }
                    .buttonStyle(.bordered)
                    .disabled(!interactive)
            }
            if model.activeComputerUseJobID != nil {
                Button("Detener control del Mac", role: .destructive) {
                    Task { await model.cancelActiveComputerUse() }
                }
                .buttonStyle(.bordered)
                .disabled(!interactive)
                .help("Solicitar la cancelación de la tarea de control activa")
            }
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.black, in: RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(accentColor.opacity(highContrast ? 0.9 : 0.4), lineWidth: 1)
                .allowsHitTesting(false)
        }
    }
}
