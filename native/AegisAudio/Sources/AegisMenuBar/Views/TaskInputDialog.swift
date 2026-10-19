import SwiftUI

struct TaskInputDialog: View {
    let model: MenuBarModel
    let input: PendingInput
    var interactive = true
    @State private var answer = ""
    @FocusState private var answerFocused: Bool

    var body: some View {
        JarvisDialogShell(title: "Necesito un dato más", symbol: "questionmark.bubble",
                          detail: "Completa la información para que Jarvis pueda continuar.", height: 440) {
            Text(input.request.question).font(.system(size: 15, weight: .medium))
                .fixedSize(horizontal: false, vertical: true)
            TextField("Escribe tu respuesta…", text: $answer, axis: .vertical)
                .lineLimit(3...6).focused($answerFocused).jarvisInput(focused: answerFocused, fontSize: 14)
                .accessibilityLabel("Respuesta para Jarvis")
                .disabled(!interactive)
            Text("La respuesta se evaluará como una nueva solicitud. Las acciones que requieran aprobación volverán a pedirla. No escribas contraseñas ni claves aquí.")
                .font(.system(size: 12)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                Text("Disponible durante \(max(0, Int(input.request.expiresAt.timeIntervalSince(context.date)))) s")
                    .font(.system(size: 11)).foregroundStyle(Color.primary.opacity(0.5)).monospacedDigit()
            }
        } footer: {
            HStack {
                Button("Cancelar", role: .cancel) { Task { await model.cancelInteraction() } }
                    .keyboardShortcut(.cancelAction)
                    .disabled(!interactive)
                Spacer()
                Button("Continuar") { Task { await model.answerPendingInput(answer, expectedID: input.id) } }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(!interactive || answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                               || answer.utf8.count > 4_096 || model.interactionSubmissionInProgress
                               || Date() >= input.request.expiresAt)
            }
        }
        .task(id: input.id) {
            answerFocused = interactive
            let interval = max(0, min(300, input.request.expiresAt.timeIntervalSinceNow))
            do { try await Task.sleep(for: .seconds(interval)) } catch { return }
            model.expireInput(input.id)
        }
    }
}
