import AppKit
import SwiftUI

struct JarvisTelegramSettingsView: View {
    @State private var store = JarvisTelegramStore()
    @State private var token = ""
    @State private var confirmation: TelegramConfirmation?
    @FocusState private var tokenFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            JarvisSettingsCard(title: String(localized: "Jarvis en tu móvil", bundle: .module),
                              detail: String(localized: "Conversa y aprueba acciones desde tu chat privado de Telegram. Tu Mac debe estar encendido, conectado y con Jarvis activo.", bundle: .module)) {
                HStack {
                    Label(store.snapshot?.statusTitle ?? "Cargando…", systemImage: "paperplane")
                        .foregroundStyle(store.snapshot?.error == nil ? JarvisInterfaceStyle.secondaryText : HUDStyle.accent(for: .warning))
                    Spacer()
                    if store.busy { ProgressView().controlSize(.small) }
                    Button(String(localized: "Actualizar", bundle: .module), systemImage: "arrow.clockwise") { Task { await store.refresh() } }
                }
                if let username = store.snapshot?.bot_username {
                    Text(String(localized: "Bot: @\(username)", bundle: .module)).textSelection(.enabled)
                }
                if store.snapshot?.error == "telegram_journal_invalid" {
                    Text(String(localized: "El canal se detuvo para evitar repetir operaciones. Puedes conservar el archivo dañado y restablecer únicamente su cola de transporte.", bundle: .module))
                        .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                    Button(String(localized: "Restablecer recuperación de Telegram…", bundle: .module)) { confirmation = .resetJournal }
                        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .warning)))
                }
                if let name = store.snapshot?.owner_name, let id = store.snapshot?.owner_id {
                    Text(String(localized: "Cuenta autorizada: \(name) · ID \(String(id))", bundle: .module)).textSelection(.enabled)
                    Toggle(String(localized: "Acceso desde Telegram", bundle: .module), isOn: Binding(
                        get: { store.snapshot?.enabled == true },
                        set: { value in
                            if value { confirmation = .enable }
                            else { Task { await store.perform("telegram.set_enabled", payload: ["enabled": false, "confirm": true]) } }
                        }
                    )).toggleStyle(JarvisToggleControlStyle())
                    HStack {
                        Button(String(localized: "Vincular otra cuenta…", bundle: .module)) { confirmation = .pair }
                        Button(String(localized: "Desvincular…", bundle: .module), role: .destructive) { confirmation = .unlink }
                    }
                }
            }
            JarvisSettingsCard(title: store.snapshot?.configured == true ? String(localized: "Cambiar bot", bundle: .module) : String(localized: "Conectar tu bot", bundle: .module),
                              detail: String(localized: "Copia el token de tu bot desde @BotFather. Se guardará en el llavero de macOS; no lo compartas en conversaciones.", bundle: .module)) {
                SecureField(store.snapshot?.configured == true ? String(localized: "Token guardado en el llavero (oculto)", bundle: .module) : String(localized: "Token de Telegram", bundle: .module), text: $token)
                    .focused($tokenFocused).jarvisInput(focused: tokenFocused)
                    .accessibilityLabel(String(localized: "Token del bot de Telegram", bundle: .module))
                    .onSubmit { if validToken { confirmation = .configure } }
                HStack {
                    Button(String(localized: "Abrir BotFather", bundle: .module), systemImage: "arrow.up.right") { open("https://t.me/BotFather") }
                    Spacer()
                    Button(String(localized: "Guardar y verificar", bundle: .module)) { confirmation = .configure }
                        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                        .disabled(!validToken)
                }
            }
            if store.snapshot?.configured == true {
                JarvisSettingsCard(title: String(localized: "Vincular tu cuenta", bundle: .module),
                                  detail: String(localized: "Abre el bot con el enlace de vinculación, pulsa Iniciar y vuelve aquí para autorizar tu cuenta. El enlace caduca en cinco minutos.", bundle: .module)) {
                    if let candidate = store.snapshot?.candidate {
                        Label(String(localized: "\(candidate.name) · ID \(String(candidate.id))", bundle: .module), systemImage: "person.crop.circle")
                        Text(String(localized: "Comprueba que este ID corresponde a tu cuenta de Telegram antes de autorizar.", bundle: .module))
                            .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                        Button(String(localized: "Autorizar esta cuenta…", bundle: .module)) { confirmation = .authorize(candidate) }
                            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    } else if let url = store.snapshot?.pair_url {
                        Button(String(localized: "Abrir bot y vincular", bundle: .module), systemImage: "arrow.up.right") { open(url) }
                        Text(String(localized: "Esperando a que pulses Iniciar en Telegram…", bundle: .module)).foregroundStyle(JarvisInterfaceStyle.secondaryText)
                    } else {
                        Button(store.snapshot?.owner_id == nil ? String(localized: "Vincular móvil", bundle: .module) : String(localized: "Volver a vincular…", bundle: .module)) { confirmation = .pair }
                    }
                }
            }
            JarvisSettingsCard(title: String(localized: "Cómo usarlo", bundle: .module),
                              detail: String(localized: "Envía texto, notas de voz, imágenes o documentos. Puedes responder a un mensaje concreto y usar sus botones. Jarvis comparte el contexto de tus chats del Mac.", bundle: .module)) {
                Text(String(localized: "/nuevo · empezar otra conversación\n/cancelar · cancelar la tarea actual\n/estado · comprobar la conexión\n/ayuda · ver los comandos", bundle: .module))
                    .lineSpacing(5).textSelection(.enabled)
                Text(String(localized: "Notas de voz: hasta 2 minutos. Archivos: hasta 10 MB; PDF con texto o imágenes. Los botones permiten cancelar, resumir, escuchar y trabajar con correos. Las acciones conservan sus permisos y aprobaciones; las que necesitan presencia local o voz verificada pueden requerir volver al Mac.", bundle: .module))
                    .foregroundStyle(JarvisInterfaceStyle.secondaryText)
            }
            if let message = store.message {
                Label(message, systemImage: store.failed ? "exclamationmark.circle" : "checkmark.circle")
                    .foregroundStyle(HUDStyle.accent(for: store.failed ? .warning : .success))
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .disabled(store.busy)
        .task {
            while !Task.isCancelled {
                await store.refresh()
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
            }
        }
        .onDisappear { token = "" }
        .alert(confirmation?.title ?? String(localized: "Telegram", bundle: .module), isPresented: Binding(
            get: { confirmation != nil }, set: { if !$0 { confirmation = nil } }
        ), presenting: confirmation) { action in
            Button(action.actionTitle, role: action.isDestructive ? .destructive : nil) {
                let enteredToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
                if case .configure = action { token = "" }
                Task {
                    switch action {
                    case .configure: await store.perform("telegram.configure", payload: ["token": enteredToken, "confirm": true])
                    case .pair: await store.perform("telegram.pair.begin")
                    case .authorize(let candidate): await store.perform("telegram.pair.approve", payload: ["candidate_id": candidate.id, "confirm": true])
                    case .enable: await store.perform("telegram.set_enabled", payload: ["enabled": true, "confirm": true])
                    case .unlink: await store.perform("telegram.unlink", payload: ["confirm": true])
                    case .resetJournal: await store.perform("telegram.journal.reset", payload: ["confirm": true])
                    }
                }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) { }
        } message: { action in Text(action.detail) }
    }

    private var validToken: Bool {
        token.trimmingCharacters(in: .whitespacesAndNewlines)
            .range(of: "^[0-9]{5,20}:[A-Za-z0-9_-]{30,100}$", options: .regularExpression) != nil
    }

    private func open(_ text: String) {
        guard let url = URL(string: text), url.scheme == "https", url.host == "t.me" else { return }
        NSWorkspace.shared.open(url)
    }
}

private enum TelegramConfirmation {
    case configure, pair, authorize(JarvisTelegramCandidate), enable, unlink, resetJournal
    var isDestructive: Bool {
        switch self {
        case .unlink, .resetJournal: true
        default: false
        }
    }
    var title: String {
        switch self {
        case .configure: String(localized: "¿Conectar este bot a Jarvis?", bundle: .module)
        case .pair: String(localized: "¿Iniciar la vinculación?", bundle: .module)
        case .authorize: String(localized: "¿Autorizar esta cuenta de Telegram?", bundle: .module)
        case .enable: String(localized: "¿Activar el acceso desde Telegram?", bundle: .module)
        case .unlink: String(localized: "¿Desvincular tu cuenta?", bundle: .module)
        case .resetJournal: String(localized: "¿Restablecer la recuperación de Telegram?", bundle: .module)
        }
    }
    var actionTitle: String {
        switch self {
        case .configure: String(localized: "Conectar bot", bundle: .module)
        case .pair: String(localized: "Vincular", bundle: .module)
        case .authorize: String(localized: "Autorizar cuenta", bundle: .module)
        case .enable: String(localized: "Activar", bundle: .module)
        case .unlink: String(localized: "Desvincular", bundle: .module)
        case .resetJournal: String(localized: "Restablecer", bundle: .module)
        }
    }
    var detail: String {
        switch self {
        case .configure: String(localized: "Jarvis verificará el bot y guardará su token en el llavero. Al cambiarlo se cancelará la tarea de Telegram y se retirará la vinculación anterior. Usa un bot exclusivo para Jarvis.", bundle: .module)
        case .pair: String(localized: "Se pausará el acceso actual y se cancelará su tarea. El nuevo enlace permitirá solicitar acceso; solo se concederá cuando autorices la cuenta en este Mac.", bundle: .module)
        case .authorize(let candidate): String(localized: "Autorizarás a \(candidate.name), ID \(String(candidate.id)), para enviar instrucciones y aprobar acciones. Las instrucciones, notas de voz, imágenes, documentos, contexto y resultados solicitados se procesarán con NVIDIA; las respuestas se enviarán por Telegram. El acceso funcionará también con la pantalla bloqueada mientras Jarvis siga activo.", bundle: .module)
        case .enable: String(localized: "Tu cuenta vinculada podrá enviar instrucciones, recibir resultados y aprobar acciones desde Telegram, incluso con la pantalla del Mac bloqueada.", bundle: .module)
        case .unlink: String(localized: "Se cancelará la tarea de Telegram y se retirará el acceso de tu cuenta. El token permanece en el llavero y tus conversaciones en el historial.", bundle: .module)
        case .resetJournal: String(localized: "Se conservará un respaldo privado del archivo dañado. Se descartarán las entradas y respuestas pendientes de Telegram para evitar repetir acciones. El acceso quedará desactivado hasta que lo actives nuevamente. Tu token, cuenta autorizada y conversaciones se conservarán.", bundle: .module)
        }
    }
}
