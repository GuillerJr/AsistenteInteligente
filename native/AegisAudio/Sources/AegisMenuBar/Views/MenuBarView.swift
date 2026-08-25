import AegisAudioCore
import AppKit
import SwiftUI

struct MenuBarView: View {
    let model: MenuBarModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 12) {
            header

            if model.pendingApproval != nil {
                approvalAction
            }

            primaryAction
            sensorGrid
            commandGrid
            wakeWordCard
            footer
        }
        .padding(14)
        .frame(width: 348)
        .background(panelBackground)
        .preferredColorScheme(.dark)
        .task {
            await model.refreshPrivacyCapabilities()
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didBecomeKeyNotification)) {
            _ in
            Task {
                await model.refreshPrivacyCapabilities()
            }
        }
    }

    private var header: some View {
        HStack(spacing: 11) {
            ZStack {
                Circle()
                    .stroke(accentColor.opacity(0.2), lineWidth: 5)
                Circle()
                    .trim(from: 0.08, to: 0.78)
                    .stroke(accentColor, style: StrokeStyle(lineWidth: 1.6, lineCap: .round))
                    .rotationEffect(.degrees(-38))
                Image(systemName: model.voiceState.symbol)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(accentColor)
            }
            .frame(width: 38, height: 38)
            .shadow(color: accentColor.opacity(0.2), radius: 7)

            VStack(alignment: .leading, spacing: 2) {
                Text("JARVIS")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .tracking(2.1)
                    .foregroundStyle(.white.opacity(0.94))
                Text(systemSummary.uppercased())
                    .font(.system(size: 8, weight: .semibold, design: .monospaced))
                    .tracking(0.65)
                    .foregroundStyle(accentColor.opacity(0.9))
                    .lineLimit(1)
                    .minimumScaleFactor(0.82)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            headerButton(symbol: "person.2.fill", help: "Configurar identidad de voz") {
                openWindow(id: "speaker-enrollment")
            }

            headerButton(
                symbol: "arrow.clockwise",
                help: "Actualizar estado",
                disabled: model.runtimeProbeInProgress
            ) {
                Task {
                    await model.refreshDaemon()
                    await model.refreshPrivacyCapabilities()
                }
            }

            headerButton(symbol: "power", help: "Salir de Jarvis") {
                NSApplication.shared.terminate(nil)
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var approvalAction: some View {
        Button {
            openWindow(id: "approval")
        } label: {
            HStack(spacing: 10) {
                Image(systemName: "hand.raised.fill")
                    .font(.system(size: 16, weight: .semibold))
                VStack(alignment: .leading, spacing: 1) {
                    Text("ACCIÓN PENDIENTE")
                        .font(.system(size: 10, weight: .bold, design: .rounded))
                        .tracking(0.7)
                    Text("Requiere confirmación explícita")
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.white.opacity(0.5))
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 10, weight: .bold))
            }
        }
        .buttonStyle(MenuPanelButtonStyle(color: .orange, emphasized: true))
    }

    private var primaryAction: some View {
        Button(action: performPrimaryAction) {
            HStack(spacing: 12) {
                Image(systemName: primarySymbol)
                    .font(.system(size: 25, weight: .medium))
                    .contentTransition(.symbolEffect(.replace))
                VStack(alignment: .leading, spacing: 2) {
                    Text(primaryTitle)
                        .font(.system(size: 11, weight: .bold, design: .rounded))
                        .tracking(0.8)
                    Text(primarySubtitle)
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.white.opacity(0.52))
                }
                Spacer()
                NeuralBars(level: CGFloat(model.voiceActivityLevel), active: model.voiceState.isBusy)
            }
            .frame(height: 43)
        }
        .buttonStyle(MenuPanelButtonStyle(color: primaryColor, emphasized: true))
        .disabled(!primaryAvailable)
        .opacity(primaryAvailable ? 1 : 0.45)
    }

    private var sensorGrid: some View {
        HStack(spacing: 8) {
            SensorTile(
                title: "MIC",
                symbol: "mic.fill",
                state: permissionTitle(model.microphonePermission),
                color: microphoneColor,
                action: microphoneAction
            )
            SensorTile(
                title: "SPEECH",
                symbol: "quote.bubble.fill",
                state: permissionTitle(model.speechPermission),
                color: speechColor,
                action: speechAction
            )
            SensorTile(
                title: "PANTALLA",
                symbol: "rectangle.inset.filled",
                state: model.screenCaptureAuthorized ? "LISTO" : "PERMITIR",
                color: model.screenCaptureAuthorized ? .green : .orange,
                action: model.screenCaptureAuthorized ? nil : { model.requestScreenCapture() }
            )
            SensorTile(
                title: "CONTROL",
                symbol: "cursorarrow.motionlines",
                state: computerControlState,
                color: computerControlColor,
                action: model.computerControlCapability == .ready
                    ? nil
                    : { Task { await model.requestComputerControlAccess() } }
            )
            .help(computerControlHelp)
        }
    }

    private var commandGrid: some View {
        HStack(spacing: 8) {
            CommandTile(title: "IMAGEN", symbol: "photo") {
                guard let url = ImageFilePicker.chooseImage() else { return }
                Task { await model.startImageVoiceTurn(fileURL: url) }
            }
            .disabled(!model.canStartVoiceTurn)

            CommandTile(title: "PANTALLA", symbol: "macbook") {
                Task { await model.startScreenVoiceTurn() }
            }
            .disabled(!model.canStartScreenTurn)

            CommandTile(title: "HUD 3D", symbol: "circle.hexagongrid.fill") {
                HUDPanelController.shared.show(model: model)
            }
        }
    }

    private var wakeWordCard: some View {
        HStack(spacing: 10) {
            Image(systemName: wakeWordListeningSymbol)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(wakeWordColor)
                .frame(width: 26, height: 26)
                .background(wakeWordColor.opacity(0.1), in: Circle())

            VStack(alignment: .leading, spacing: 2) {
                Text("ACTIVACIÓN «JARVIS»")
                    .font(.system(size: 9, weight: .bold, design: .rounded))
                    .tracking(0.65)
                    .foregroundStyle(.white.opacity(0.78))
                Text(wakeWordSummary)
                    .font(.system(size: 9, weight: .medium))
                    .foregroundStyle(.white.opacity(0.46))
                    .lineLimit(1)
            }

            Spacer(minLength: 6)

            Button(wakeWordActionTitle, action: performWakeWordAction)
                .buttonStyle(MenuCompactButtonStyle(color: wakeWordColor))
                .disabled(model.wakeWordListeningState == .starting)

            if model.wakeWordCapability == .ready {
                Button {
                    openWindow(id: "wake-word-enrollment")
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .buttonStyle(MenuIconButtonStyle(color: .cyan))
                .help("Configurar activación")
            }
        }
        .padding(10)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(.white.opacity(0.07), lineWidth: 1)
        }
    }

    private var footer: some View {
        HStack(spacing: 7) {
            Label(
                model.voiceShortcutAvailable ? "⌃⇧ ESPACIO" : "ATAJO NO DISPONIBLE",
                systemImage: "keyboard"
            )
            Spacer()
            Circle().frame(width: 3, height: 3)
            Text("LOCAL")
            Circle().frame(width: 3, height: 3)
            Text("ARM64")
        }
        .font(.system(size: 7, weight: .semibold, design: .monospaced))
        .tracking(0.65)
        .foregroundStyle(.white.opacity(0.28))
        .padding(.horizontal, 2)
    }

    private var panelBackground: some View {
        ZStack {
            Rectangle().fill(.ultraThinMaterial)
            LinearGradient(
                colors: [
                    Color(red: 0.018, green: 0.027, blue: 0.047).opacity(0.96),
                    .black.opacity(0.94),
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            RadialGradient(
                colors: [accentColor.opacity(0.08), .clear],
                center: .topLeading,
                startRadius: 0,
                endRadius: 220
            )
        }
    }

    @ViewBuilder
    private func headerButton(
        symbol: String,
        help: String,
        disabled: Bool = false,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            if disabled {
                ProgressView().controlSize(.mini)
            } else {
                Image(systemName: symbol)
            }
        }
        .buttonStyle(MenuIconButtonStyle(color: accentColor))
        .disabled(disabled)
        .help(help)
    }

    private var accentColor: Color {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return .red
        }
        if model.daemonState == .offline || model.securityState == .unavailable {
            return .orange
        }
        if model.providerState == .missing || model.providerState == .unavailable {
            return .orange
        }
        if model.voiceState == .awaitingApproval { return .orange }
        if model.voiceState == .processing || model.voiceState == .submitting { return .purple }
        return .cyan
    }

    private var systemSummary: String {
        if model.securityState == .compromised || model.daemonState == .securityFailure {
            return "Seguridad comprometida"
        }
        if model.daemonState != .online { return "Daemon \(model.daemonState.title)" }
        if model.providerState != .configured {
            return "NVIDIA \(model.providerState.title)"
        }
        return "\(model.voiceState.title) · auditoría \(model.securityState.title)"
    }

    private var voicePermissionPending: Bool {
        model.microphonePermission == .notDetermined || model.speechPermission == .notDetermined
    }

    private var voicePermissionBlocked: Bool {
        [.denied, .restricted].contains(model.microphonePermission)
            || [.denied, .restricted].contains(model.speechPermission)
    }

    private var primaryAvailable: Bool {
        model.pendingApproval != nil
            || model.activeComputerUseJobID != nil
            || model.canStartVoiceTurn
            || voicePermissionPending
            || voicePermissionBlocked
    }

    private var primaryTitle: String {
        if model.pendingApproval != nil { return "REVISAR ACCIÓN" }
        if model.activeComputerUseJobID != nil { return "DETENER CONTROL" }
        if model.canStartVoiceTurn { return "INICIAR VOZ" }
        if voicePermissionBlocked { return "AJUSTAR VOZ" }
        if voicePermissionPending { return "CONFIGURAR VOZ" }
        if model.providerState == .missing { return "NVIDIA NO CONFIGURADA" }
        if model.providerState == .unavailable { return "NVIDIA NO VERIFICABLE" }
        return model.voiceState.isBusy ? "JARVIS OCUPADO" : "VOZ NO DISPONIBLE"
    }

    private var primarySubtitle: String {
        if model.pendingApproval != nil { return "Confirmación de un solo uso" }
        if model.activeComputerUseJobID != nil { return "Cancelación inmediata del job activo" }
        if model.canStartVoiceTurn, let speaker = model.lastSpeakerID {
            return "Voz identificada: \(speaker)"
        }
        if model.canStartVoiceTurn, model.speakerIdentityCapability == .ready {
            return "Audio local · identidad activa"
        }
        if model.canStartVoiceTurn { return "Audio local · identidad pendiente" }
        if voicePermissionBlocked { return "Abrir privacidad de macOS" }
        if voicePermissionPending { return "Micrófono y reconocimiento" }
        if model.providerState == .missing { return "Añade la API key en Keychain" }
        if model.providerState == .unavailable { return "Revisa Keychain y el daemon" }
        return model.voiceState.isBusy ? "Procesando solicitud" : "Revisar daemon y seguridad"
    }

    private var primarySymbol: String {
        if model.pendingApproval != nil { return "hand.raised.fill" }
        if model.activeComputerUseJobID != nil { return "stop.circle.fill" }
        if model.canStartVoiceTurn { return "waveform.circle.fill" }
        if voicePermissionBlocked { return "gearshape.fill" }
        if voicePermissionPending { return "mic.badge.plus" }
        if model.providerState == .missing { return "key.fill" }
        if model.providerState == .unavailable { return "questionmark.circle.fill" }
        return "waveform.slash"
    }

    private var primaryColor: Color {
        if model.pendingApproval != nil { return .orange }
        if model.activeComputerUseJobID != nil { return .red }
        return .cyan
    }

    private func performPrimaryAction() {
        if model.pendingApproval != nil {
            openWindow(id: "approval")
        } else if model.activeComputerUseJobID != nil {
            Task { await model.cancelActiveComputerUse() }
        } else if model.canStartVoiceTurn {
            Task { await model.startVoiceTurn() }
        } else {
            configureVoice()
        }
    }

    private func configureVoice() {
        switch model.microphonePermission {
        case .denied, .restricted:
            model.openMicrophoneSettings()
            return
        case .authorized, .notDetermined, .unknown:
            break
        }
        switch model.speechPermission {
        case .denied, .restricted:
            model.openSpeechSettings()
            return
        case .authorized, .notDetermined, .unknown:
            break
        }
        Task { await model.requestUndeterminedPermissions() }
    }

    private var microphoneAction: (() -> Void)? {
        switch model.microphonePermission {
        case .notDetermined:
            return { Task { await model.requestMicrophone() } }
        case .denied, .restricted:
            return { model.openMicrophoneSettings() }
        case .authorized, .unknown:
            return nil
        }
    }

    private var speechAction: (() -> Void)? {
        switch model.speechPermission {
        case .notDetermined:
            return { Task { await model.requestSpeechRecognition() } }
        case .denied, .restricted:
            return { model.openSpeechSettings() }
        case .authorized, .unknown:
            return nil
        }
    }

    private var microphoneColor: Color {
        permissionColor(model.microphonePermission)
    }

    private var speechColor: Color {
        switch model.speechPermission {
        case .authorized: .green
        case .notDetermined, .unknown: .orange
        case .denied, .restricted: .red
        }
    }

    private var computerControlState: String {
        switch model.computerControlCapability {
        case .ready: "LISTO"
        case .screenCaptureMissing: "PANTALLA"
        case .accessibilityMissing: "ACCESIB."
        case .permissionsMissing: "PERMITIR"
        case .helperUnavailable: "NO DISP."
        }
    }

    private var computerControlHelp: String {
        switch model.computerControlCapability {
        case .ready:
            "JarvisComputerHelper puede observar y controlar aplicaciones compatibles"
        case .screenCaptureMissing:
            "Falta permitir la pantalla a JarvisComputerHelper en Privacidad y seguridad"
        case .accessibilityMissing:
            "Activa Jarvis y JarvisComputerHelper en Control de dispositivos; Jarvis se reinicia al salir de Ajustes"
        case .permissionsMissing:
            "Jarvis solicitará primero pantalla y después control, sin superponer paneles de macOS"
        case .helperUnavailable:
            "El componente local JarvisComputerHelper no está disponible"
        }
    }

    private var computerControlColor: Color {
        switch model.computerControlCapability {
        case .ready: .green
        case .screenCaptureMissing, .accessibilityMissing, .permissionsMissing: .orange
        case .helperUnavailable: .red
        }
    }

    private func permissionColor(_ permission: MicrophonePermission) -> Color {
        switch permission {
        case .authorized: .green
        case .notDetermined, .unknown: .orange
        case .denied, .restricted: .red
        }
    }

    private func permissionTitle(_ permission: MicrophonePermission) -> String {
        switch permission {
        case .authorized: "LISTO"
        case .denied, .restricted: "AJUSTES"
        case .notDetermined: "PERMITIR"
        case .unknown: "REVISAR"
        }
    }

    private func permissionTitle(_ permission: SpeechRecognitionPermission) -> String {
        switch permission {
        case .authorized: "LISTO"
        case .denied, .restricted: "AJUSTES"
        case .notDetermined: "PERMITIR"
        case .unknown: "REVISAR"
        }
    }

    private var wakeWordColor: Color {
        return switch model.wakeWordListeningState {
        case .listening: .green
        case .starting, .recovering: .cyan
        case .failed: .red
        case .paused: .orange
        case .unavailable, .off: .white.opacity(0.55)
        }
    }

    private var wakeWordSummary: String {
        guard model.wakeWordCapability == .ready else {
            return model.wakeWordCapability == .invalid ? "Modelo inválido" : "Configuración pendiente"
        }
        return switch model.wakeWordListeningState {
        case .unavailable: "No disponible"
        case .off: "Escucha desactivada"
        case .starting: "Iniciando escucha"
        case .recovering: "Recuperando escucha"
        case .listening: "Escuchando en segundo plano"
        case .paused: model.wakeWordPauseReason?.title ?? "En pausa"
        case .failed: "La escucha falló"
        }
    }

    private var wakeWordActionTitle: String {
        guard model.wakeWordCapability == .ready else { return "PREPARAR" }
        if model.wakeWordListeningState == .failed, model.wakeWordOptedIn { return "REINTENTAR" }
        return model.wakeWordOptedIn ? "DESACTIVAR" : "ACTIVAR"
    }

    private func performWakeWordAction() {
        guard model.wakeWordCapability == .ready else {
            openWindow(id: "wake-word-enrollment")
            return
        }
        let enabled = model.wakeWordListeningState == .failed && model.wakeWordOptedIn
            ? true
            : !model.wakeWordOptedIn
        Task { await model.setWakeWordListeningEnabled(enabled) }
    }

    private var wakeWordListeningSymbol: String {
        switch model.wakeWordListeningState {
        case .listening: "ear.badge.waveform"
        case .starting, .recovering: "hourglass.circle"
        case .paused: "pause.circle"
        case .unavailable, .off: "ear"
        case .failed: "exclamationmark.triangle"
        }
    }
}
private struct SensorTile: View {
    let title: String
    let symbol: String
    let state: String
    let color: Color
    let action: (() -> Void)?

    var body: some View {
        Group {
            if let action {
                Button(action: action) { content }
                    .buttonStyle(.plain)
                    .help("Configurar \(title.lowercased())")
            } else {
                content
            }
        }
        .frame(maxWidth: .infinity)
    }

    private var content: some View {
        VStack(spacing: 5) {
            Image(systemName: symbol)
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(color)
            Text(title)
                .font(.system(size: 8, weight: .bold, design: .rounded))
                .tracking(0.6)
                .foregroundStyle(.white.opacity(0.72))
            Text(state)
                .font(.system(size: 7, weight: .bold, design: .monospaced))
                .foregroundStyle(color.opacity(0.9))
        }
        .frame(maxWidth: .infinity, minHeight: 55)
        .background(color.opacity(action == nil ? 0.045 : 0.08), in: RoundedRectangle(cornerRadius: 11))
        .overlay {
            RoundedRectangle(cornerRadius: 11)
                .stroke(color.opacity(action == nil ? 0.13 : 0.3), lineWidth: 1)
        }
        .contentShape(RoundedRectangle(cornerRadius: 11))
    }
}

private struct CommandTile: View {
    let title: String
    let symbol: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 5) {
                Image(systemName: symbol)
                    .font(.system(size: 17, weight: .medium))
                Text(title)
                    .font(.system(size: 8, weight: .bold, design: .rounded))
                    .tracking(0.65)
            }
            .frame(maxWidth: .infinity, minHeight: 48)
        }
        .buttonStyle(MenuPanelButtonStyle(color: .purple, emphasized: false))
    }
}

private struct NeuralBars: View {
    let level: CGFloat
    let active: Bool

    var body: some View {
        HStack(alignment: .center, spacing: 2) {
            ForEach(0..<5, id: \.self) { index in
                Capsule()
                    .fill(Color.cyan.opacity(active ? 0.78 : 0.34))
                    .frame(width: 2, height: height(for: index))
            }
        }
        .frame(width: 22, height: 24)
        .animation(.smooth(duration: 0.14), value: level)
        .accessibilityHidden(true)
    }

    private func height(for index: Int) -> CGFloat {
        let shape: [CGFloat] = [0.45, 0.75, 1, 0.68, 0.4]
        return 5 + (shape[index] * max(active ? 0.35 : 0.08, level) * 17)
    }
}

private struct MenuPanelButtonStyle: ButtonStyle {
    let color: Color
    let emphasized: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.white.opacity(configuration.isPressed ? 0.68 : 0.92))
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .background {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(color.opacity(configuration.isPressed ? 0.16 : emphasized ? 0.11 : 0.055))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .stroke(color.opacity(emphasized ? 0.4 : 0.18), lineWidth: 1)
                    }
            }
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

private struct MenuCompactButtonStyle: ButtonStyle {
    let color: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 8, weight: .bold, design: .rounded))
            .tracking(0.5)
            .foregroundStyle(color)
            .padding(.horizontal, 8)
            .frame(height: 24)
            .background(color.opacity(configuration.isPressed ? 0.16 : 0.08), in: Capsule())
            .overlay { Capsule().stroke(color.opacity(0.25), lineWidth: 1) }
    }
}

private struct MenuIconButtonStyle: ButtonStyle {
    let color: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(configuration.isPressed ? color.opacity(0.55) : color.opacity(0.82))
            .frame(width: 27, height: 27)
            .background(.white.opacity(configuration.isPressed ? 0.025 : 0.055), in: Circle())
            .overlay { Circle().stroke(.white.opacity(0.07), lineWidth: 1) }
            .scaleEffect(configuration.isPressed ? 0.95 : 1)
    }
}
