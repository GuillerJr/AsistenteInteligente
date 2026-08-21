import AegisAudioCore
import AppKit
import SwiftUI

struct WakeWordEnrollmentView: View {
    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label("Activación local por voz", systemImage: "waveform.badge.mic")
                .font(.title2.weight(.semibold))

            Text("Cada botón graba un clip local de 2 segundos. Nada se envía por red ni se graba sin una acción explícita.")
                .foregroundStyle(.secondary)

            sampleRow(
                title: "Di “Jarvis”",
                detail: "Una pronunciación natural por muestra",
                count: model.wakeWordEnrollmentProgress.jarvisCount,
                label: .jarvis
            )

            sampleRow(
                title: "Sonido ambiente",
                detail: "Silencio, conversación o ruido habitual",
                count: model.wakeWordEnrollmentProgress.backgroundCount,
                label: .background
            )

            status

            HStack {
                Button("Cerrar", role: .cancel) {
                    dismissWindow(id: "wake-word-enrollment")
                }
                Spacer()
                if model.microphonePermission != .authorized {
                    Button("Permitir micrófono") {
                        Task { await model.requestMicrophone() }
                    }
                }
            }
        }
        .padding(22)
        .frame(width: 480)
        .task {
            await model.refreshWakeWordEnrollment()
        }
        .onAppear {
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    private func sampleRow(
        title: String,
        detail: String,
        count: Int,
        label: WakeWordEnrollmentLabel
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.headline)
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(buttonTitle(for: label)) {
                    Task { await model.recordWakeWordSample(label) }
                }
                .disabled(!model.canRecordWakeWordSample)
            }
            ProgressView(
                value: Double(min(count, WakeWordEnrollmentProgress.targetPerLabel)),
                total: Double(WakeWordEnrollmentProgress.targetPerLabel)
            )
            Text("\(count) de \(WakeWordEnrollmentProgress.targetPerLabel) mínimas")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .padding(14)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var status: some View {
        switch model.wakeWordEnrollmentState {
        case .idle:
            Label("Dataset en preparación", systemImage: "circle.dotted")
                .foregroundStyle(.secondary)
        case .loading:
            Label("Validando almacenamiento local…", systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(.secondary)
        case let .recording(label):
            Label(
                label == .jarvis ? "Grabando “Jarvis”…" : "Grabando ambiente…",
                systemImage: "record.circle.fill"
            )
            .foregroundStyle(.red)
        case .ready:
            Label("Dataset mínimo listo para entrenamiento local", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed:
            Label("No fue posible guardar la muestra de forma segura", systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
        }
    }

    private func buttonTitle(for label: WakeWordEnrollmentLabel) -> String {
        if
            case let .recording(activeLabel) = model.wakeWordEnrollmentState,
            activeLabel == label
        {
            return "Grabando…"
        }
        return "Grabar 2 s"
    }
}
