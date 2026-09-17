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
                          detail: "Revisa la acción antes de autorizarla. La aprobación vale una sola vez.") {
            Text(approval.confirmation.summary).font(.system(size: 13, weight: .medium))
                .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading).voiceEnrollmentCard()
            if let preview = approval.confirmation.preview {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Detalles de la acción").font(.system(size: 13, weight: .semibold))
                    ForEach(Array(preview.fields.enumerated()), id: \.offset) { _, field in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(field.label).font(.system(size: 11, weight: .medium))
                                .foregroundStyle(.white.opacity(0.55))
                            Text(field.value).font(.system(size: 12)).textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if preview.fields.isEmpty { Text("Esta acción no tiene datos adicionales.") }
                }.padding(14).frame(maxWidth: .infinity, alignment: .leading).voiceEnrollmentCard()
            }
            Text(presentation.warning).font(.system(size: 12)).foregroundStyle(.white.opacity(0.65))
                .fixedSize(horizontal: false, vertical: true)
            if let recovery = model.pendingRecovery, recovery.code == "correction_not_applied" {
                Label(recovery.message, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12)).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Label("Puedes usar Touch ID o decir «Aprobado».", systemImage: "touchid")
                .font(.system(size: 12)).foregroundStyle(.orange)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                Text(context.date < approval.confirmation.expiresAt
                     ? "Disponible durante \(max(0, Int(approval.confirmation.expiresAt.timeIntervalSince(context.date)))) s"
                     : "La aprobación caducó")
                    .font(.system(size: 11)).foregroundStyle(.white.opacity(0.55)).monospacedDigit()
            }
        } footer: {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                HStack(spacing: 10) {
                    Button("Rechazar", role: .cancel) { Task { await model.denyPending(expected: approval) } }
                        .keyboardShortcut(.cancelAction)
                    Button("Corregir…") {
                        model.pauseApprovalForCorrection()
                        correction = ""
                        showCorrection = true
                    }
                    Spacer()
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
                Text("Corregir la solicitud").font(.headline)
                Text("Escribe la solicitud completa. Jarvis cancelará la acción anterior y evaluará esta petición de nuevo.")
                    .font(.callout).foregroundStyle(.secondary)
                TextEditor(text: $correction).scrollContentBackground(.hidden).frame(height: 140)
                    .padding(10).jarvisCard()
                    .accessibilityLabel("Solicitud corregida")
                HStack {
                    Button("Cancelar", role: .cancel) { showCorrection = false }
                        .disabled(model.approvalActionInProgress || model.interactionSubmissionInProgress)
                    Spacer()
                    Button("Evaluar nueva solicitud") {
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
