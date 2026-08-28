import AppKit
import SwiftUI

struct ApprovalView: View {
    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let approval = model.pendingApproval {
                let presentation = ApprovalPresentation(toolName: approval.confirmation.toolName)
                Label(
                    presentation.title,
                    systemImage: presentation.icon
                )
                    .font(.headline)

                Text(approval.confirmation.summary)
                    .font(.body.monospaced())
                    .textSelection(.enabled)
                    .lineLimit(4)
                    .fixedSize(horizontal: false, vertical: true)

                Text(presentation.warning)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(4)
                    .fixedSize(horizontal: false, vertical: true)

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
                    .keyboardShortcut(.cancelAction)

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
        .onChange(of: model.pendingApproval) { previous, current in
            guard previous != nil, current == nil else { return }
            dismissWindow(id: "approval")
        }
    }
}

private struct ApprovalPresentation {
    let title: String
    let icon: String
    let warning: String

    init(toolName: String) {
        switch toolName {
        case "computer_use":
            title = "Control visual autónomo"
            icon = "cursorarrow.motionlines"
            warning = "Jarvis enviará capturas acotadas al modelo NVIDIA y controlará solo la "
                + "aplicación indicada. Se detendrá antes de acciones sensibles."
        case "terminal_run_template":
            title = "Diagnóstico de terminal"
            icon = "terminal.fill"
            warning = "Jarvis ejecutará una plantilla fija de solo lectura, sin shell ni argumentos libres."
        case "network_discover_hosts":
            title = "Exploración de red"
            icon = "network"
            warning = "Jarvis iniciará conexiones TCP sin enviar payloads. El equipo remoto puede registrarlas."
        case "mail_send_message":
            title = "Envío de correo"
            icon = "envelope.fill"
            warning = "Jarvis enviará el mensaje indicado mediante Apple Mail a los destinatarios del resumen."
        case "calendar_create_event":
            title = "Creación de evento"
            icon = "calendar.badge.plus"
            warning = "Jarvis creará el evento indicado en Apple Calendar con las fechas mostradas."
        case "reminder_create", "reminder_complete":
            title = "Cambio en Recordatorios"
            icon = "checklist"
            warning = "Jarvis aplicará únicamente el cambio de Recordatorios descrito en el resumen."
        case "contact_create":
            title = "Creación de contacto"
            icon = "person.crop.circle.badge.plus"
            warning = "Jarvis añadirá a Apple Contacts solo el nombre y los canales mostrados."
        case "system_audio_set":
            title = "Cambio de audio"
            icon = "speaker.wave.2.fill"
            warning = "Jarvis cambiará únicamente el volumen o el silencio indicados para este Mac."
        case "media_control":
            title = "Control multimedia"
            icon = "playpause.fill"
            warning = "Jarvis enviará la orden mostrada a Música o Spotify, sin leer tu biblioteca."
        case "spotlight_open":
            title = "Apertura desde Spotlight"
            icon = "magnifyingglass.circle.fill"
            warning = "Jarvis abrirá el único resultado exacto de Spotlight indicado en el resumen."
        case "browser_open_url":
            title = "Apertura del navegador"
            icon = "safari.fill"
            warning = "Jarvis abrirá la dirección pública indicada en tu navegador predeterminado."
        case "browser_search":
            title = "Búsqueda visible en Internet"
            icon = "text.magnifyingglass"
            warning = "La consulta visible se enviará a DuckDuckGo y se abrirá en el navegador indicado."
        case "application_open":
            title = "Apertura de aplicación"
            icon = "app.fill"
            warning = "Jarvis abrirá únicamente la aplicación identificada en el resumen."
        case "shortcut_run":
            title = "Ejecución de atajo"
            icon = "square.stack.3d.up.fill"
            warning = "Jarvis ejecutará el atajo de macOS indicado; revisa sus acciones antes de aprobar."
        default:
            title = "Acción protegida"
            icon = "exclamationmark.shield.fill"
            warning = "Jarvis ejecutará únicamente la acción descrita. Puede usar un servicio externo; revisa el resumen antes de aprobar."
        }
    }
}
