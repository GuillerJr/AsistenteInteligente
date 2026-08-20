import AppKit
import SwiftUI

struct ApprovalView: View {
    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let approval = model.pendingApproval {
                Label(
                    title(for: approval.confirmation.toolName),
                    systemImage: icon(for: approval.confirmation.toolName)
                )
                    .font(.headline)

                Text(approval.confirmation.summary)
                    .font(.body.monospaced())
                    .textSelection(.enabled)

                Text(warning(for: approval.confirmation.toolName))
                .font(.callout)
                .foregroundStyle(.secondary)

                Text("Caduca \(approval.confirmation.expiresAt, style: .relative)")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                HStack {
                    Button("Denegar", role: .cancel) {
                        Task {
                            await model.denyPending()
                            if model.pendingApproval == nil {
                                dismissWindow(id: "approval")
                            }
                        }
                    }

                    Spacer()

                    Button("Aprobar una vez") {
                        Task {
                            await model.approvePending()
                            if model.pendingApproval == nil {
                                dismissWindow(id: "approval")
                            }
                        }
                    }
                    .keyboardShortcut(.defaultAction)
                }
                .disabled(model.approvalActionInProgress)
            } else {
                ContentUnavailableView(
                    "Sin aprobaciones pendientes",
                    systemImage: "checkmark.shield"
                )
            }
        }
        .padding(20)
        .frame(width: 440)
        .onAppear {
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    private func title(for toolName: String) -> String {
        toolName == "terminal_run_template" ? "Diagnóstico de terminal" : "Acción de red activa"
    }

    private func icon(for toolName: String) -> String {
        toolName == "terminal_run_template" ? "terminal.fill" : "exclamationmark.shield.fill"
    }

    private func warning(for toolName: String) -> String {
        if toolName == "terminal_run_template" {
            return "Jarvis ejecutará una plantilla fija de solo lectura, sin shell ni argumentos libres."
        }
        return "Jarvis iniciará conexiones TCP sin enviar payloads. El equipo remoto puede registrarlas."
    }
}
