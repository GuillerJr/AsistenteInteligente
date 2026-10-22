import AegisAudioCore
import AppKit
import SwiftUI

struct WakeWordEnrollmentView: View {
    private static let activationCommand = "./script/activate_wake_word.sh"

    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow
    @State private var confirmingSampleDeletion = false

    var body: some View {
        VoiceEnrollmentPanel(
            title: String(localized: "Activación local por voz", bundle: .module),
            symbol: "waveform.badge.mic",
            detail: String(localized: "Enseña a Jarvis a reconocer su nombre. Pulsa una vez y espera a “Grabando” antes de hablar. Cada muestra dura 2 segundos y permanece en este Mac.", bundle: .module),
            width: 560,
            height: 600
        ) {
            Text(String(localized: "Muestras de activación", bundle: .module))
                .font(.system(size: 13, weight: .semibold))
            sampleRow(
                title: String(localized: "Di “Jarvis”", bundle: .module),
                count: model.wakeWordEnrollmentProgress.jarvisCount,
                label: .jarvis
            )

            sampleRow(
                title: String(localized: "Sonido ambiente", bundle: .module),
                count: model.wakeWordEnrollmentProgress.backgroundCount,
                label: .background
            )

            if model.wakeWordEnrollmentProgress.isReady {
                activationHandoff
            }
        } status: {
            status
        } footer: {
            HStack {
                Button(String(localized: "Cerrar", bundle: .module), role: .cancel) {
                    dismissWindow(id: "wake-word-enrollment")
                }
                .keyboardShortcut(.cancelAction)
                if hasSamples {
                    Button(String(localized: "Eliminar muestras…", bundle: .module), role: .destructive) {
                        confirmingSampleDeletion = true
                    }
                    .disabled(model.wakeWordEnrollmentState.isBusy)
                }
                Spacer()
                if model.microphonePermission != .authorized {
                    Button(String(localized: "Permitir micrófono", bundle: .module)) {
                        Task { await model.requestMicrophone() }
                    }
                }
            }
        }
        .task {
            await model.refreshWakeWordEnrollment()
        }
        .confirmationDialog(
            String(localized: "¿Eliminar todas las muestras locales?", bundle: .module),
            isPresented: $confirmingSampleDeletion
        ) {
            Button(String(localized: "Eliminar muestras", bundle: .module), role: .destructive) {
                Task { await model.clearWakeWordSamples() }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: {
            Text(String(localized: "Se borrarán las grabaciones de estas muestras. El reconocimiento ya entrenado se conservará.", bundle: .module))
        }
    }

    private var activationHandoff: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(String(localized: "Siguiente paso", bundle: .module))
                .font(.system(size: 13, weight: .semibold))
            Text(String(localized: "Ejecuta este comando desde la raíz del proyecto. Validará, entrenará e instalará el modelo local.", bundle: .module))
                .font(.caption)
                .foregroundStyle(JarvisTheme.inkSecondary)
            HStack {
                Text(Self.activationCommand)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                Spacer()
                Button(String(localized: "Copiar comando", bundle: .module), systemImage: "doc.on.doc") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(Self.activationCommand, forType: .string)
                }
            }
        }
        .padding(14)
        .voiceEnrollmentCard()
    }

    private func sampleRow(
        title: String,
        count: Int,
        label: WakeWordEnrollmentLabel
    ) -> some View {
        let recording: Bool
        if case let .recording(activeLabel) = model.wakeWordEnrollmentState {
            recording = activeLabel == label
        } else {
            recording = false
        }
        return VoiceEnrollmentSampleRow(
            title: title,
            subtitle: label == .jarvis ? String(localized: "Palabra de activación", bundle: .module) : String(localized: "Sonidos sin la palabra Jarvis", bundle: .module),
            symbol: label == .jarvis ? "waveform.badge.mic" : "waveform",
            guidance: WakeWordEnrollmentGuidance.instruction(label: label, acceptedCount: count),
            count: count, targetCount: WakeWordEnrollmentProgress.targetPerLabel,
            buttonTitle: buttonTitle(for: label), canRecord: model.canRecordWakeWordSample,
            isRecording: recording,
            record: { Task { await model.recordWakeWordSample(label) } }
        )
    }

    @ViewBuilder
    private var status: some View {
        switch model.wakeWordEnrollmentState {
        case .idle:
            Label(String(localized: "Listo para grabar una muestra", bundle: .module), systemImage: "checkmark.circle")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case .loading:
            Label(String(localized: "Validando almacenamiento local…", bundle: .module), systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case .clearing:
            Label(String(localized: "Eliminando muestras locales…", bundle: .module), systemImage: "trash")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case let .arming(label):
            Label(
                label == .jarvis ? String(localized: "Prepárate para decir “Jarvis”…", bundle: .module) : String(localized: "Preparando captura ambiente…", bundle: .module),
                systemImage: "timer"
            )
            .foregroundStyle(JarvisTheme.inkSecondary)
        case let .recording(label):
            Label(
                label == .jarvis ? String(localized: "Grabando “Jarvis”…", bundle: .module) : String(localized: "Grabando ambiente…", bundle: .module),
                systemImage: "record.circle.fill"
            )
            .foregroundStyle(JarvisTheme.failure)
        case .ready:
            Label(String(localized: "Muestras mínimas listas para entrenar", bundle: .module), systemImage: "checkmark.circle.fill")
                .foregroundStyle(HUDStyle.accent(for: .success))
        case let .failed(error):
            Label(enrollmentErrorTitle(error), systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(HUDStyle.accent(for: .warning))
        }
    }

    private func buttonTitle(for label: WakeWordEnrollmentLabel) -> String {
        if
            case let .recording(activeLabel) = model.wakeWordEnrollmentState,
            activeLabel == label
        {
            return "Grabando…"
        }
        if
            case let .arming(activeLabel) = model.wakeWordEnrollmentState,
            activeLabel == label
        {
            return "Preparando…"
        }
        return String(localized: "Grabar 2 s", bundle: .module)
    }

    private var hasSamples: Bool {
        model.wakeWordEnrollmentProgress.jarvisCount > 0
            || model.wakeWordEnrollmentProgress.backgroundCount > 0
    }

    private func enrollmentErrorTitle(_ error: WakeWordEnrollmentError) -> String {
        switch error {
        case .sampleTooQuiet:
            String(localized: "Muestra descartada: di “Jarvis” con voz clara", bundle: .module)
        case .sampleClipped:
            String(localized: "Muestra descartada: reduce el volumen o aléjate", bundle: .module)
        case .permissionRequired:
            String(localized: "El micrófono requiere permiso", bundle: .module)
        case .unsafeStorage:
            String(localized: "El almacenamiento local no es seguro", bundle: .module)
        case .capacityReached:
            String(localized: "Se alcanzó el límite de muestras", bundle: .module)
        case .invalidConfiguration, .invalidInputFormat, .recordingFailed:
            String(localized: "No fue posible guardar la muestra", bundle: .module)
        }
    }
}
