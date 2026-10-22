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
        if !model.mascotSessionAvailable { return String(localized: "En pausa", bundle: .module) }
        if model.interactionID != nil { return String(localized: "Necesito tu atención", bundle: .module) }
        if [.idle, .ambient, .completed].contains(model.presentationStatus.kind) {
            if let notice = model.mascotNotice, notice.count <= 48 { return notice }
            if model.mascotNotice != nil { return String(localized: "Hay un aviso para ti", bundle: .module) }
            if model.pendingAttentionCount > 0 {
                return model.pendingAttentionCount == 1 ? "1 pendiente" : String(localized: "\(model.pendingAttentionCount) pendientes", bundle: .module)
            }
            return presentation.interaction.isHovered ? String(localized: "Estoy contigo", bundle: .module) : String(localized: "Jarvis", bundle: .module)
        }
        return model.presentationStatus.compactTitle
    }

    private var hint: String {
        if !model.mascotSessionAvailable { return String(localized: "Desbloquea tu Mac", bundle: .module) }
        if model.interactionID != nil { return String(localized: "Doble clic para revisar", bundle: .module) }
        if model.voiceState == .listening || model.voiceState == .followingUp {
            return String(localized: "Doble clic para dejar de escuchar", bundle: .module)
        }
        if !model.canActivateMascotVoice { return String(localized: "Revisa el estado en el menubar", bundle: .module) }
        return String(localized: "Doble clic para hablar", bundle: .module)
    }

    private var voiceActionTitle: String {
        if model.interactionID != nil { return String(localized: "Revisar decisión", bundle: .module) }
        if model.voiceState == .listening || model.voiceState == .followingUp { return String(localized: "Cancelar escucha", bundle: .module) }
        if model.voiceState == .speaking || model.voiceState == .processing { return String(localized: "Interrumpir", bundle: .module) }
        return String(localized: "Hablar con Jarvis", bundle: .module)
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
                .overlay(alignment: .topTrailing) {
                    // What waits for the owner stays visible after Dot's notice fades.
                    if model.pendingAttentionCount > 0 && model.mascotSessionAvailable {
                        Text(model.pendingAttentionCount > 9 ? "9+" : "\(model.pendingAttentionCount)")
                            .font(.system(size: 8, weight: .bold, design: .rounded))
                            .foregroundStyle(.black)
                            .padding(.horizontal, 3).frame(minWidth: 12, minHeight: 12)
                            .background(Color(red: 1, green: 0.76, blue: 0.32), in: Capsule())
                            .offset(x: 3, y: -2)
                            .accessibilityLabel(String(localized: "\(model.pendingAttentionCount) pendientes", bundle: .module))
                    }
                }
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
        .accessibilityLabel(String(localized: "Mascota de Jarvis", bundle: .module))
        .accessibilityValue(model.mascotSessionAvailable
                            ? model.presentationStatus.accessibilityDescription : String(localized: "Jarvis en pausa", bundle: .module))
        .accessibilityAction(named: Text(voiceActionTitle), activateVoice)
        .help(String(localized: "Pulsa o acaricia para interactuar. Doble clic para hablar, interrumpir o dejar de escuchar.", bundle: .module))
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
