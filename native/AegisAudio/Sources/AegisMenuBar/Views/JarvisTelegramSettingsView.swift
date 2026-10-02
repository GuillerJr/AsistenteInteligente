import AppKit
import SwiftUI

struct JarvisTelegramSettingsView: View {
    @State private var store = JarvisTelegramStore()
    @State private var token = ""
    @State private var confirmation: TelegramConfirmation?
    @FocusState private var tokenFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            JarvisSettingsCard(title: "Jarvis en tu móvil",
                              detail: "Conversa y aprueba acciones desde tu chat privado de Telegram. Tu Mac debe estar encendido, conectado y con Jarvis activo.") {
                HStack {
                    Label(store.snapshot?.statusTitle ?? "Cargando…", systemImage: "paperplane")
                        .foregroundStyle(store.snapshot?.error == nil ? JarvisInterfaceStyle.secondaryText : HUDStyle.accent(for: .warning))
                    Spacer()
                    if store.busy { ProgressView().controlSize(.small) }
                    Button("Actualizar", systemImage: "arrow.clockwise") { Task { await store.refresh() } }
                }
                if let username = store.snapshot?.bot_username {
                    Text("Bot: @\(username)").textSelection(.enabled)
                }
                if let name = store.snapshot?.owner_name, let id = store.snapshot?.owner_id {
                    Text("Cuenta autorizada: \(name) · ID \(String(id))").textSelection(.enabled)
                    Toggle("Acceso desde Telegram", isOn: Binding(
                        get: { store.snapshot?.enabled == true },
                        set: { value in
                            if value { confirmation = .enable }
                            else { Task { await store.perform("telegram.set_enabled", payload: ["enabled": false, "confirm": true]) } }
                        }
                    )).toggleStyle(JarvisToggleControlStyle())
                    HStack {
                        Button("Vincular otra cuenta…") { confirmation = .pair }
                        Button("Desvincular…", role: .destructive) { confirmation = .unlink }
                    }
                }
            }
            JarvisSettingsCard(title: store.snapshot?.configured == true ? "Cambiar bot" : "Conectar tu bot",
                              detail: "Copia el token de tu bot desde @BotFather. Se guardará en el llavero de macOS; no lo compartas en conversaciones.") {
                SecureField(store.snapshot?.configured == true ? "Token guardado en el llavero (oculto)" : "Token de Telegram", text: $token)
                    .focused($tokenFocused).jarvisInput(focused: tokenFocused)
                    .accessibilityLabel("Token del bot de Telegram")
                    .onSubmit { if validToken { confirmation = .configure } }
                HStack {
                    Button("Abrir BotFather", systemImage: "arrow.up.right") { open("https://t.me/BotFather") }
                    Spacer()
                    Button("Guardar y verificar") { confirmation = .configure }
                        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                        .disabled(!validToken)
                }
            }
            if store.snapshot?.configured == true {
                JarvisSettingsCard(title: "Vincular tu cuenta",
                                  detail: "Abre el bot con el enlace de vinculación, pulsa Iniciar y vuelve aquí para autorizar tu cuenta. El enlace caduca en cinco minutos.") {
                    if let candidate = store.snapshot?.candidate {
                        Label("\(candidate.name) · ID \(String(candidate.id))", systemImage: "person.crop.circle")
                        Text("Comprueba que este ID corresponde a tu cuenta de Telegram antes de autorizar.")
                            .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                        Button("Autorizar esta cuenta…") { confirmation = .authorize(candidate) }
                            .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    } else if let url = store.snapshot?.pair_url {
                        Button("Abrir bot y vincular", systemImage: "arrow.up.right") { open(url) }
                        Text("Esperando a que pulses Iniciar en Telegram…").foregroundStyle(JarvisInterfaceStyle.secondaryText)
                    } else {
                        Button(store.snapshot?.owner_id == nil ? "Vincular móvil" : "Volver a vincular…") { confirmation = .pair }
                    }
                }
            }
            JarvisSettingsCard(title: "Cómo usarlo",
                              detail: "Escribe tus instrucciones en el chat privado del bot. Jarvis mantiene el contexto en una conversación propia, visible también en el historial del Mac.") {
                Text("/nuevo · empezar otra conversación\n/cancelar · cancelar la tarea actual\n/estado · comprobar la conexión\n/ayuda · ver los comandos")
                    .lineSpacing(5).textSelection(.enabled)
                Text("Esta primera versión recibe texto. Las acciones conservan sus permisos y aprobaciones; las que necesitan presencia local o voz verificada pueden requerir volver al Mac.")
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
        .alert(confirmation?.title ?? "Telegram", isPresented: Binding(
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
                    }
                }
            }
            Button("Cancelar", role: .cancel) { }
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
    case configure, pair, authorize(JarvisTelegramCandidate), enable, unlink
    var isDestructive: Bool { if case .unlink = self { true } else { false } }
    var title: String {
        switch self {
        case .configure: "¿Conectar este bot a Jarvis?"
        case .pair: "¿Iniciar la vinculación?"
        case .authorize: "¿Autorizar esta cuenta de Telegram?"
        case .enable: "¿Activar el acceso desde Telegram?"
        case .unlink: "¿Desvincular tu cuenta?"
        }
    }
    var actionTitle: String {
        switch self {
        case .configure: "Conectar bot"
        case .pair: "Vincular"
        case .authorize: "Autorizar cuenta"
        case .enable: "Activar"
        case .unlink: "Desvincular"
        }
    }
    var detail: String {
        switch self {
        case .configure: "Jarvis verificará el bot y guardará su token en el llavero. Al cambiarlo se cancelará la tarea de Telegram y se retirará la vinculación anterior. Usa un bot exclusivo para Jarvis."
        case .pair: "Se pausará el acceso actual y se cancelará su tarea. El nuevo enlace permitirá solicitar acceso; solo se concederá cuando autorices la cuenta en este Mac."
        case .authorize(let candidate): "Autorizarás a \(candidate.name), ID \(String(candidate.id)), para enviar instrucciones y aprobar acciones. Las instrucciones, el contexto y los resultados solicitados se procesarán con NVIDIA; las respuestas se enviarán por Telegram. El acceso funcionará también con la pantalla bloqueada mientras Jarvis siga activo."
        case .enable: "Tu cuenta vinculada podrá enviar instrucciones, recibir resultados y aprobar acciones desde Telegram, incluso con la pantalla del Mac bloqueada."
        case .unlink: "Se cancelará la tarea de Telegram y se retirará el acceso de tu cuenta. El token permanece en el llavero y tus conversaciones en el historial."
        }
    }
}
