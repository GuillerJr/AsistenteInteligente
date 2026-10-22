import AegisAudioCore
import SwiftUI

struct ActionReviewView: View {
    let model: MenuBarModel
    let approval: PendingApproval
    var interactive = true
    @State private var showCorrection = false
    @State private var correction = ""

    private var presentation: ActionReviewPresentation {
        ActionReviewPresentation(toolName: approval.confirmation.toolName)
    }

    private var actionTitle: String {
        ActionApprovalLabel.title(toolName: approval.confirmation.toolName,
                                  action: approval.confirmation.preview?.fields.first(where: { $0.label == "Acción" })?.value)
    }

    var body: some View {
        JarvisDialogShell(title: presentation.title, symbol: presentation.icon,
                          detail: String(localized: "Revisa la acción antes de autorizarla. La aprobación vale una sola vez.", bundle: .module)) {
            Text(approval.confirmation.summary).font(.system(size: 13, weight: .medium))
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading).voiceEnrollmentCard()
            if let preview = approval.confirmation.preview {
                VStack(alignment: .leading, spacing: 14) {
                    Text(String(localized: "Detalles de la acción", bundle: .module)).font(.system(size: 13, weight: .semibold))
                    ForEach(Array(preview.fields.enumerated()), id: \.offset) { _, field in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(field.label).font(.system(size: 11, weight: .medium))
                                .foregroundStyle(JarvisTheme.inkMuted)
                            Text(field.value).font(.system(size: 12)).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if preview.fields.isEmpty { Text(String(localized: "Esta acción no tiene datos adicionales.", bundle: .module)) }
                }.padding(14).frame(maxWidth: .infinity, alignment: .leading).voiceEnrollmentCard()
            }
            Text(presentation.warning).font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if let recovery = model.pendingRecovery, recovery.code == "correction_not_applied" {
                Label(recovery.message, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Label(String(localized: "Puedes usar Touch ID o decir «Aprobado».", bundle: .module), systemImage: "touchid")
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.warning)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                Text(context.date < approval.confirmation.expiresAt
                     ? String(localized: "Disponible durante \(max(0, Int(approval.confirmation.expiresAt.timeIntervalSince(context.date)))) s", bundle: .module)
                     : String(localized: "La aprobación caducó", bundle: .module))
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted).monospacedDigit()
            }
        } footer: {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                HStack(spacing: 10) {
                    Button(String(localized: "Rechazar", bundle: .module), role: .cancel) { Task { await model.denyPending(expected: approval) } }
                        .keyboardShortcut(.cancelAction)
                    Button(String(localized: "Corregir…", bundle: .module)) {
                        model.pauseApprovalForCorrection()
                        correction = ""
                        showCorrection = true
                    }
                    Spacer()
                    if let bundle = approval.confirmation.appGrantBundle {
                        // Codex's per-app approval: keep working in this app without asking.
                        Menu(String(localized: "Permitir \(Self.appName(bundle))", bundle: .module)) {
                            Button(String(localized: "En esta conversación", bundle: .module)) {
                                Task { await model.approvePending(expected: approval, appScope: "conversation") }
                            }
                            Button(String(localized: "Siempre", bundle: .module)) {
                                Task { await model.approvePending(expected: approval, appScope: "always") }
                            }
                        }
                        .fixedSize()
                        .help(String(localized: "Jarvis podrá seguir usando esta app sin volver a preguntar. Puedes revocarlo en Ajustes › Memoria y privacidad.", bundle: .module))
                        .disabled(showCorrection || context.date >= approval.confirmation.expiresAt)
                    }
                    if approval.confirmation.standingGrantAvailable {
                        // Only read-only tools: writes and sends keep asking every time.
                        Button(String(localized: "Aprobar siempre", bundle: .module)) {
                            Task { await model.approvePending(expected: approval, always: true) }
                        }
                        .help(String(localized: "Solo lee datos. Puedes revocarlo en Ajustes › Permisos.", bundle: .module))
                        .disabled(showCorrection || context.date >= approval.confirmation.expiresAt)
                    }
                    Button(actionTitle) { Task { await model.approvePending(expected: approval) } }
                        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(showCorrection || context.date >= approval.confirmation.expiresAt)
                }.disabled(!interactive || model.approvalActionInProgress || model.interactionSubmissionInProgress)
            }
        }
        .sheet(isPresented: $showCorrection, onDismiss: {
            model.resumeApprovalAfterCorrection(approval)
        }) {
            VStack(alignment: .leading, spacing: 16) {
                Text(String(localized: "Corregir la solicitud", bundle: .module)).font(.headline)
                Text(String(localized: "Escribe la solicitud completa. Jarvis cancelará la acción anterior y evaluará esta petición de nuevo.", bundle: .module))
                    .font(.callout).foregroundStyle(JarvisTheme.inkSecondary)
                TextEditor(text: $correction).scrollContentBackground(.hidden).frame(height: 140)
                    .padding(10).jarvisCard()
                    .accessibilityLabel(String(localized: "Solicitud corregida", bundle: .module))
                HStack {
                    Button(String(localized: "Cancelar", bundle: .module), role: .cancel) { showCorrection = false }
                        .disabled(model.approvalActionInProgress || model.interactionSubmissionInProgress)
                    Spacer()
                    Button(String(localized: "Evaluar nueva solicitud", bundle: .module)) {
                        let text = correction
                        Task {
                            await model.correctPendingRequest(text, expectedApproval: approval)
                            showCorrection = false
                        }
                    }.disabled(correction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                               || correction.utf8.count > 4_096 || model.pendingApproval != approval
                               || model.approvalActionInProgress || model.interactionSubmissionInProgress)
                }
            }.padding(24).frame(width: 500).jarvisSheet()
        }
        .onChange(of: model.pendingApproval) { _, current in
            if current != approval { showCorrection = false }
        }
    }
}

extension ActionReviewView {
    /// "com.apple.Notes" → "Notas", as Finder names the installed app.
    static func appName(_ bundle: String) -> String {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else { return bundle }
        return FileManager.default.displayName(atPath: url.path).replacingOccurrences(of: ".app", with: "")
    }
}
