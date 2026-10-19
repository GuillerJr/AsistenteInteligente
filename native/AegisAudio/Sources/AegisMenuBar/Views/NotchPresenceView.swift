import AegisAudioCore
import SwiftUI

/// The notch is a living presence. Conversation and files stay in Chat.
struct NotchPresenceView: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    var interactive = true
    var togglePinned: () -> Void = {}
    var activateVoice: () -> Void = {}
    var dismiss: () -> Void = {}
    var forceReduceMotion = false
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion

    var body: some View {
        let progress = CGFloat(presentation.expansionProgress)
        let outline = NotchOutline(topInset: 12 * progress, bottomRadius: 12 + 10 * progress)
        let reveal = min(max((presentation.expansionProgress - 0.75) / 0.20, 0), 1)
        let opacity = reveal * reveal * (3 - 2 * reveal)
        GeometryReader { geometry in
            ZStack(alignment: .top) {
                MascotMotionStage(model: model, presentation: presentation,
                                  size: geometry.size, reduceMotion: systemReduceMotion || forceReduceMotion,
                                  interactive: interactive, voiceActionTitle: voiceActionTitle,
                                  touch: touch, activateVoice: activateVoice)

                VStack(spacing: 5) {
                    Text(status).font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundStyle(model.presentationStatus.tone.color)
                    Text(hint).font(.system(size: 9)).foregroundStyle(.white.opacity(0.55))
                }
                .lineLimit(1)
                .frame(width: max(0, geometry.size.width - 24), height: 36)
                .position(x: geometry.size.width / 2, y: geometry.size.height - 26)
                .opacity(opacity).allowsHitTesting(false).accessibilityHidden(opacity < 0.95)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(.black, in: outline).clipShape(outline).contentShape(outline)
        .clipped().ignoresSafeArea().preferredColorScheme(.dark)
        .onExitCommand(perform: dismiss)
        .task { synchronizeBehavior() }
        .onChange(of: model.presentationStatus.kind) { _, _ in synchronizeBehavior() }
        .onChange(of: model.interactionID) { _, _ in synchronizeBehavior() }
        .onChange(of: model.mascotNotice) { _, _ in synchronizeBehavior() }
        .onChange(of: model.mascotSessionAvailable) { _, available in
            synchronizeBehavior()
            if !available { dismiss() }
        }
    }

    private var status: String {
        if !model.mascotSessionAvailable { return "En pausa" }
        if model.interactionID != nil { return "Necesito tu atención" }
        if [.idle, .ambient, .completed].contains(model.presentationStatus.kind) {
            if model.mascotNotice != nil { return "Hay un aviso para ti" }
            return presentation.interaction.isHovered ? "Estoy contigo" : "Jarvis"
        }
        return model.presentationStatus.compactTitle
    }

    private var hint: String {
        if !model.mascotSessionAvailable { return "Desbloquea tu Mac" }
        if model.interactionID != nil { return "Doble clic para revisar" }
        if model.voiceState == .listening || model.voiceState == .followingUp {
            return "Doble clic para dejar de escuchar"
        }
        if !model.canActivateMascotVoice { return "Revisa el estado en el menubar" }
        return "Doble clic para hablar"
    }

    private var voiceActionTitle: String {
        if model.interactionID != nil { return "Revisar decisión" }
        if model.voiceState == .listening || model.voiceState == .followingUp { return "Cancelar escucha" }
        if model.voiceState == .speaking || model.voiceState == .processing { return "Interrumpir" }
        return "Hablar con Jarvis"
    }

    private func touch() {
        guard interactive, model.mascotSessionAvailable else { return }
        presentation.behavior.touch(at: Date().timeIntervalSinceReferenceDate)
        presentation.autonomy.touch()
        togglePinned()
    }

    private func synchronizeBehavior() {
        presentation.behavior.update(kind: model.interactionID == nil ? model.presentationStatus.kind : .approval,
                                     sessionAvailable: model.mascotSessionAvailable,
                                     hasNotice: model.mascotNotice != nil,
                                     at: Date().timeIntervalSinceReferenceDate)
    }
}

/// Only this subtree observes 60 Hz position; labels and the surrounding window do not.
private struct MascotMotionStage: View {
    let model: MenuBarModel
    let presentation: NotchPresentationState
    let size: CGSize
    let reduceMotion: Bool
    let interactive: Bool
    let voiceActionTitle: String
    let touch: () -> Void
    let activateVoice: () -> Void

    var body: some View {
        let room = MascotRoom(width: size.width, height: size.height,
                              expansion: presentation.expansionProgress)
        let center = room.center(for: presentation.autonomy.position)
        Button(action: touch) {
            NotchMascotArtwork(model: model, behavior: presentation.behavior,
                               reduceMotion: reduceMotion)
                .frame(width: 112, height: 112)
                .scaleEffect(room.bodySize / 112)
                .frame(width: room.bodySize, height: room.bodySize)
                .rotationEffect(.radians(reduceMotion ? 0 : presentation.autonomy.tilt))
                .contentShape(Circle())
                .simultaneousGesture(TapGesture(count: 2).onEnded {
                    if interactive { activateVoice() }
                })
                .simultaneousGesture(DragGesture(minimumDistance: 4).onChanged { _ in
                    guard interactive else { return }
                    presentation.behavior.touch(at: Date().timeIntervalSinceReferenceDate)
                    presentation.autonomy.touch()
                })
        }
        .buttonStyle(.plain).disabled(!interactive || !model.mascotSessionAvailable)
        .accessibilityLabel("Mascota de Jarvis")
        .accessibilityValue(model.mascotSessionAvailable
                            ? model.presentationStatus.accessibilityDescription : "Jarvis en pausa")
        .accessibilityAction(named: Text(voiceActionTitle), activateVoice)
        .help("Pulsa o acaricia para interactuar. Doble clic para hablar, interrumpir o dejar de escuchar.")
        .position(center)
        .onAppear { synchronize(room) }
        .onChange(of: room) { _, room in synchronize(room) }
        .onChange(of: reduceMotion) { _, _ in synchronize(room) }
    }

    private func synchronize(_ room: MascotRoom) {
        presentation.autonomy.updateRoom(size: size, expansion: room.expansion)
        presentation.autonomy.setReducedMotion(reduceMotion)
    }
}

/// Only the artwork consumes microphone levels and the animation clock.
private struct NotchMascotArtwork: View {
    let model: MenuBarModel
    let behavior: MascotBehaviorState
    let reduceMotion: Bool
    var body: some View {
        JarvisNotchMascot(behavior: behavior, energy: Double(model.voiceActivityLevel),
                         forceReduceMotion: reduceMotion)
    }
}
