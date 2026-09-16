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
            title: "Activación local por voz",
            symbol: "waveform.badge.mic",
            detail: "Enseña a Jarvis a reconocer su nombre. Pulsa una vez y espera a “Grabando” antes de hablar. Cada muestra dura 2 segundos y permanece en este Mac.",
            width: 560,
            height: 600
        ) {
            Text("Muestras de activación")
                .font(.system(size: 13, weight: .semibold))
            sampleRow(
                title: "Di “Jarvis”",
                count: model.wakeWordEnrollmentProgress.jarvisCount,
                label: .jarvis
            )

            sampleRow(
                title: "Sonido ambiente",
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
                Button("Cerrar", role: .cancel) {
                    dismissWindow(id: "wake-word-enrollment")
                }
                .keyboardShortcut(.cancelAction)
                if hasSamples {
                    Button("Eliminar muestras…", role: .destructive) {
                        confirmingSampleDeletion = true
                    }
                    .disabled(model.wakeWordEnrollmentState.isBusy)
                }
                Spacer()
                if model.microphonePermission != .authorized {
                    Button("Permitir micrófono") {
                        Task { await model.requestMicrophone() }
                    }
                }
            }
        }
        .task {
            await model.refreshWakeWordEnrollment()
        }
        .confirmationDialog(
            "¿Eliminar todas las muestras locales?",
            isPresented: $confirmingSampleDeletion
        ) {
            Button("Eliminar muestras", role: .destructive) {
                Task { await model.clearWakeWordSamples() }
            }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Se borrarán los CAF de enrolamiento. Un modelo ya entrenado no se eliminará.")
        }
    }

    private var activationHandoff: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Siguiente paso")
                .font(.system(size: 13, weight: .semibold))
            Text("Ejecuta este comando desde la raíz del proyecto. Validará, entrenará e instalará el modelo local.")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.65))
            HStack {
                Text(Self.activationCommand)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                Spacer()
                Button("Copiar comando", systemImage: "doc.on.doc") {
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
            subtitle: label == .jarvis ? "Palabra de activación" : "Sonidos sin la palabra Jarvis",
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
            Label("Listo para grabar una muestra", systemImage: "checkmark.circle")
                .foregroundStyle(.white.opacity(0.65))
        case .loading:
            Label("Validando almacenamiento local…", systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(.white.opacity(0.65))
        case .clearing:
            Label("Eliminando muestras locales…", systemImage: "trash")
                .foregroundStyle(.white.opacity(0.65))
        case let .arming(label):
            Label(
                label == .jarvis ? "Prepárate para decir “Jarvis”…" : "Preparando captura ambiente…",
                systemImage: "timer"
            )
            .foregroundStyle(.white.opacity(0.65))
        case let .recording(label):
            Label(
                label == .jarvis ? "Grabando “Jarvis”…" : "Grabando ambiente…",
                systemImage: "record.circle.fill"
            )
            .foregroundStyle(.red)
        case .ready:
            Label("Dataset mínimo listo para entrenamiento local", systemImage: "checkmark.circle.fill")
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
        return "Grabar 2 s"
    }

    private var hasSamples: Bool {
        model.wakeWordEnrollmentProgress.jarvisCount > 0
            || model.wakeWordEnrollmentProgress.backgroundCount > 0
    }

    private func enrollmentErrorTitle(_ error: WakeWordEnrollmentError) -> String {
        switch error {
        case .sampleTooQuiet:
            "Muestra descartada: di “Jarvis” con voz clara"
        case .sampleClipped:
            "Muestra descartada: reduce el volumen o aléjate"
        case .permissionRequired:
            "El micrófono requiere permiso"
        case .unsafeStorage:
            "El almacenamiento local no es seguro"
        case .capacityReached:
            "Se alcanzó el límite de muestras"
        case .invalidConfiguration, .invalidInputFormat, .recordingFailed:
            "No fue posible guardar la muestra"
        }
    }
}
