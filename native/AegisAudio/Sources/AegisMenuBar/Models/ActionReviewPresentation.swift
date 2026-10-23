import Foundation

struct ActionReviewPresentation {
    let toolName: String
    let title: String
    let icon: String
    let warning: String

    init(toolName: String) {
        self.toolName = toolName
        switch toolName {
        case "computer_use":
            title = String(localized: "Control visual autónomo", bundle: .module)
            icon = "cursorarrow.motionlines"
            warning = String(localized: "Jarvis enviará capturas acotadas a Codex (OpenAI) y controlará solo la ", bundle: .module)
                + String(localized: "aplicación indicada. Se detendrá antes de acciones sensibles.", bundle: .module)
        case "terminal_run_template":
            title = String(localized: "Diagnóstico de terminal", bundle: .module)
            icon = "terminal.fill"
            warning = String(localized: "Jarvis ejecutará una plantilla fija de solo lectura, sin shell ni argumentos libres.", bundle: .module)
        case "network_discover_hosts":
            title = String(localized: "Exploración de red", bundle: .module)
            icon = "network"
            warning = String(localized: "Jarvis iniciará conexiones TCP sin enviar payloads. El equipo remoto puede registrarlas.", bundle: .module)
        case "mail_send_message":
            title = String(localized: "Envío de correo", bundle: .module)
            icon = "envelope.fill"
            warning = String(localized: "Jarvis enviará el mensaje indicado mediante Apple Mail a los destinatarios del resumen.", bundle: .module)
        case "calendar_create_event":
            title = String(localized: "Creación de evento", bundle: .module)
            icon = "calendar.badge.plus"
            warning = String(localized: "Jarvis creará el evento indicado en Apple Calendar con las fechas mostradas.", bundle: .module)
        case "reminder_create", "reminder_complete":
            title = String(localized: "Cambio en Recordatorios", bundle: .module)
            icon = "checklist"
            warning = String(localized: "Jarvis aplicará únicamente el cambio de Recordatorios descrito en el resumen.", bundle: .module)
        case "contact_create":
            title = String(localized: "Creación de contacto", bundle: .module)
            icon = "person.crop.circle.badge.plus"
            warning = String(localized: "Jarvis añadirá a Apple Contacts solo el nombre y los canales mostrados.", bundle: .module)
        case "system_audio_set":
            title = String(localized: "Cambio de audio", bundle: .module)
            icon = "speaker.wave.2.fill"
            warning = String(localized: "Jarvis cambiará únicamente el volumen o el silencio indicados para este Mac.", bundle: .module)
        case "media_control":
            title = String(localized: "Control multimedia", bundle: .module)
            icon = "playpause.fill"
            warning = String(localized: "Jarvis enviará la orden mostrada a Música o Spotify, sin leer tu biblioteca.", bundle: .module)
        case "spotlight_open":
            title = String(localized: "Apertura desde Spotlight", bundle: .module)
            icon = "magnifyingglass.circle.fill"
            warning = String(localized: "Jarvis abrirá el único resultado exacto de Spotlight indicado en el resumen.", bundle: .module)
        case "browser_open_url":
            title = String(localized: "Apertura del navegador", bundle: .module)
            icon = "safari.fill"
            warning = String(localized: "Jarvis abrirá la dirección pública indicada en tu navegador predeterminado.", bundle: .module)
        case "browser_search":
            title = String(localized: "Búsqueda visible en Internet", bundle: .module)
            icon = "text.magnifyingglass"
            warning = String(localized: "La consulta visible se enviará a DuckDuckGo y se abrirá en el navegador indicado.", bundle: .module)
        case "browser_play_media":
            title = String(localized: "Reproducción en Chrome", bundle: .module)
            icon = "play.rectangle.fill"
            warning = String(localized: "Jarvis buscará en YouTube y reproducirá el resultado solicitado mediante el DOM local de Chrome.", bundle: .module)
        case "application_open":
            title = String(localized: "Apertura de aplicación", bundle: .module)
            icon = "app.fill"
            warning = String(localized: "Jarvis abrirá únicamente la aplicación identificada en el resumen.", bundle: .module)
        case "shortcut_run":
            title = String(localized: "Ejecución de atajo", bundle: .module)
            icon = "square.stack.3d.up.fill"
            warning = String(localized: "Jarvis ejecutará el atajo de macOS indicado; revisa sus acciones antes de aprobar.", bundle: .module)
        default:
            title = String(localized: "Acción protegida", bundle: .module)
            icon = "exclamationmark.shield.fill"
            warning = String(localized: "Jarvis ejecutará únicamente la acción descrita. Puede usar un servicio externo; revisa el resumen antes de aprobar.", bundle: .module)
        }
    }
}
