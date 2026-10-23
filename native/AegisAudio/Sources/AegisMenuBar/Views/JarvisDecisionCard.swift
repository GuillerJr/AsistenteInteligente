import AegisAudioCore
import SwiftUI

/// Whatever Jarvis needs from the owner (an approval, a missing detail, a browser to
/// use, a notice that a task stopped), shown as a card where the request was made:
/// under the chat conversation, or under the notch for voice and Dot. No modal window.
struct JarvisDecisionCard: View {
    let model: MenuBarModel
    var style: JarvisApprovalCard.Style = .chat

    var body: some View {
        if let approval = model.pendingApproval {
            JarvisApprovalCard(model: model, approval: approval, style: style)
        } else if let input = model.pendingInput {
            JarvisInputCard(model: model, input: input, style: style)
        } else if let selection = model.pendingBrowserSelection {
            JarvisBrowserChoiceCard(model: model, selection: selection, style: style)
        } else if let recovery = model.pendingRecovery {
            JarvisRecoveryCard(model: model, recovery: recovery, style: style)
        }
    }
}

extension View {
    /// The shared card surface: raised in the chat, black like the notch under it.
    func jarvisDecisionSurface(_ style: JarvisApprovalCard.Style) -> some View {
        padding(14)
            .background(style == .chat ? JarvisTheme.raised : Color.black,
                        in: RoundedRectangle(cornerRadius: JarvisTheme.radiusCard + 2, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusCard + 2, style: .continuous)
                .stroke(JarvisTheme.hairlineStrong, lineWidth: 1))
    }
}

private struct JarvisDecisionHeader: View {
    let symbol: String
    let title: String
    var expiresAt: Date?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol).foregroundStyle(JarvisTheme.clay)
            Text(title).font(.system(size: 13, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
            Spacer(minLength: 8)
            if let expiresAt {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let left = Int(expiresAt.timeIntervalSince(context.date))
                    Text(left > 0 ? String(localized: "\(left) s", bundle: .module)
                                  : String(localized: "Caducó", bundle: .module))
                        .font(.system(size: 11)).monospacedDigit().foregroundStyle(JarvisTheme.inkMuted)
                }
            }
        }
    }
}

/// "Necesito un dato más": the question, one-click options and a short answer field.
private struct JarvisInputCard: View {
    let model: MenuBarModel
    let input: PendingInput
    let style: JarvisApprovalCard.Style
    @State private var answer = ""

    private var busy: Bool { model.interactionSubmissionInProgress }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            JarvisDecisionHeader(symbol: "questionmark.bubble",
                                 title: String(localized: "Necesito un dato más", bundle: .module),
                                 expiresAt: input.request.expiresAt)
            Text(input.request.question).font(.system(size: 13)).foregroundStyle(JarvisTheme.ink)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            if !input.request.options.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(input.request.options.enumerated()), id: \.offset) { index, option in
                        Button {
                            Task { await model.answerPendingInput(option, expectedID: input.id) }
                        } label: {
                            HStack(spacing: 8) {
                                Text(String(localized: "\(index + 1)", bundle: .module))
                                    .font(.system(size: 11, weight: .semibold)).monospacedDigit().frame(width: 14)
                                Text(option).font(.system(size: 13)).multilineTextAlignment(.leading)
                                Spacer(minLength: 0)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active)))
                        .accessibilityLabel(String(localized: "Opción \(index + 1): \(option)", bundle: .module))
                    }
                }
            }
            HStack(spacing: 8) {
                TextField(input.request.options.isEmpty ? String(localized: "Escribe tu respuesta…", bundle: .module)
                                                        : String(localized: "U otra respuesta…", bundle: .module),
                          text: $answer)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(send)
                    .accessibilityLabel(String(localized: "Respuesta para Jarvis", bundle: .module))
                Button(String(localized: "Continuar", bundle: .module), action: send)
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                              || answer.utf8.count > 4_096)
            }
            HStack {
                Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {
                    Task { await model.cancelInteraction() }
                }
                Spacer()
                Text(String(localized: "No escribas contraseñas ni claves aquí.", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
            }
        }
        .controlSize(.small)
        .disabled(busy || Date() >= input.request.expiresAt)
        .jarvisDecisionSurface(style)
        .task(id: input.id) {
            let interval = max(0, min(300, input.request.expiresAt.timeIntervalSinceNow))
            do { try await Task.sleep(for: .seconds(interval)) } catch { return }
            model.expireInput(input.id)
        }
    }

    private func send() {
        let text = answer
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, text.utf8.count <= 4_096 else { return }
        Task { await model.answerPendingInput(text, expectedID: input.id) }
    }
}

private struct JarvisBrowserChoiceCard: View {
    let model: MenuBarModel
    let selection: PendingBrowserSelection
    let style: JarvisApprovalCard.Style

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            JarvisDecisionHeader(symbol: "globe", title: String(localized: "Elige un navegador", bundle: .module))
            HStack(spacing: 8) {
                ForEach(selection.options, id: \.bundleIdentifier) { option in
                    Button(option.name) {
                        Task { await model.selectBrowser(bundleIdentifier: option.bundleIdentifier) }
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active)))
                }
                Spacer(minLength: 0)
                Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {
                    Task { await model.cancelInteraction() }
                }
            }
            Text(String(localized: "También puedes decir el nombre del navegador.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
        }
        .controlSize(.small)
        .jarvisDecisionSurface(style)
    }
}

private struct JarvisRecoveryCard: View {
    let model: MenuBarModel
    let recovery: PendingRecovery
    let style: JarvisApprovalCard.Style
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            JarvisDecisionHeader(symbol: "exclamationmark.bubble",
                                 title: String(localized: "La tarea necesita atención", bundle: .module))
            Text(recovery.message).font(.system(size: 13)).foregroundStyle(JarvisTheme.ink)
                .fixedSize(horizontal: false, vertical: true)
            if recovery.outcomeUnknown {
                Text(String(localized: "Comprueba el resultado en la aplicación o servicio antes de volver a solicitar la acción. Jarvis no la repetirá automáticamente.", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                if style == .chat {
                    // The notch panel is outside the app's scenes and cannot open windows.
                    Button(String(localized: "Configuración", bundle: .module)) {
                        model.pendingRecovery = nil
                        RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
                    }
                }
                Spacer()
                Button(String(localized: "Entendido", bundle: .module)) {
                    Task { await model.cancelInteraction() }
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
            }
        }
        .controlSize(.small)
        .jarvisDecisionSurface(style)
    }
}
