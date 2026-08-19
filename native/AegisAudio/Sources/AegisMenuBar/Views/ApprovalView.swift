import AppKit
import SwiftUI

struct ApprovalView: View {
    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let approval = model.pendingApproval {
                Label("Acción de red activa", systemImage: "exclamationmark.shield.fill")
                    .font(.headline)

                Text(approval.confirmation.summary)
                    .font(.body.monospaced())
                    .textSelection(.enabled)

                Text(
                    "Aegis iniciará conexiones TCP sin enviar payloads. "
                        + "El equipo remoto puede registrarlas."
                )
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
}
