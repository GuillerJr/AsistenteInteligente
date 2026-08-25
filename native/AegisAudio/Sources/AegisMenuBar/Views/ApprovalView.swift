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
        switch toolName {
        case "computer_use": "Control visual autónomo"
        case "terminal_run_template": "Diagnóstico de terminal"
        case "mail_send_message": "Envío de correo"
        case "calendar_create_event": "Creación de evento"
        case "browser_open_url": "Apertura del navegador"
        case "application_open": "Apertura de aplicación"
        default: "Acción de red activa"
        }
    }

    private func icon(for toolName: String) -> String {
        switch toolName {
        case "computer_use": "cursorarrow.motionlines"
        case "terminal_run_template": "terminal.fill"
        case "mail_send_message": "envelope.fill"
        case "calendar_create_event": "calendar.badge.plus"
        case "browser_open_url": "safari.fill"
        case "application_open": "app.fill"
        default: "exclamationmark.shield.fill"
        }
    }

    private func warning(for toolName: String) -> String {
        if toolName == "computer_use" {
            return "Jarvis enviará capturas acotadas al modelo NVIDIA y controlará solo la "
                + "aplicación indicada. Se detendrá antes de acciones sensibles."
        }
        if toolName == "terminal_run_template" {
            return "Jarvis ejecutará una plantilla fija de solo lectura, sin shell ni argumentos libres."
        }
        return "Jarvis iniciará conexiones TCP sin enviar payloads. El equipo remoto puede registrarlas."
    }
}
