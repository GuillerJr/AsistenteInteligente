import AppKit
import Contacts
import EventKit
import SwiftUI

struct JarvisWelcomeView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    let restart: @MainActor () async throws -> Void
    var interactive = true
    @State private var step = 0
    @State private var computerUse = false
    @State private var loginEnabled = JarvisLoginItemController.shared.status == .enabled
    @State private var starting = false
    @State private var permissionMessage: String?
    @AppStorage(JarvisWelcomeState.completionKey) private var completedVersion = 0
    @Environment(\.dismissWindow) private var dismissWindow
    @Environment(\.openWindow) private var openWindow

    private var title: String {
        [String(localized: "Bienvenido a Jarvis", bundle: .module), String(localized: "Conecta con ChatGPT", bundle: .module), String(localized: "Elige los permisos", bundle: .module),
         String(localized: "Jarvis al iniciar sesión", bundle: .module), String(localized: "Tu primera conversación", bundle: .module)][step]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(String(localized: "Paso \(step + 1) de 5", bundle: .module)).font(.caption).foregroundStyle(JarvisTheme.inkSecondary)
            Text(title).font(JarvisTheme.voice(28)).accessibilityAddTraits(.isHeader)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) { content }
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            if let message = store.message {
                Label(message, systemImage: store.failed ? "exclamationmark.circle" : "info.circle")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
            }
            if let permissionMessage { Text(permissionMessage).font(.callout) }
            if store.busy || starting { ProgressView(String(localized: "Preparando Jarvis…", bundle: .module)).controlSize(.small) }
            Divider()
            HStack {
                Button(String(localized: "Omitir por ahora", bundle: .module)) { finish() }.keyboardShortcut(.cancelAction)
                Spacer()
                if step > 0 { Button(String(localized: "Atrás", bundle: .module)) { step -= 1 } }
                if step < 4 {
                    Button(String(localized: "Continuar", bundle: .module)) { step += 1 }.keyboardShortcut(.defaultAction)
                } else {
                    Button(String(localized: "Abrir chat", bundle: .module)) { finish(openChat: true) }
                }
            }
            .disabled(store.busy || starting)
        }
        .padding(30).frame(minWidth: 600, idealWidth: 650, minHeight: 540, idealHeight: 620)
        .background(JarvisTheme.canvas).foregroundStyle(JarvisTheme.ink)
        .buttonStyle(MenuBarSmallButtonStyle()).tint(JarvisTheme.clay)
        .disabled(!interactive)
        .task {
            guard interactive else { return }
            for _ in 0..<20 {
                await store.refresh()
                if store.snapshot != nil { return }
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
            }
        }
        .onChange(of: store.inference.status?.state) { _, _ in
            guard interactive else { return }
            Task { await store.refresh(); await model.refreshDaemon() }
        }
    }

    @ViewBuilder private var content: some View {
        switch step {
        case 0:
            Text(String(localized: "Habla o escribe para organizar tus ideas, consultar información y trabajar con tus apps.", bundle: .module))
            Text(String(localized: "Jarvis usa tu cuenta de ChatGPT mediante Codex y necesita conexión a internet. Tú eliges los permisos y apruebas las acciones que cambian algo.", bundle: .module))
            Text(String(localized: "Puedes saltarte cualquier paso y volver desde Ajustes › General › Abrir bienvenida.", bundle: .module))
        case 1:
            JarvisCodexSettingsView(store: store.inference, interactive: interactive)
        case 2:
            Text(String(localized: "Todos son opcionales. Puedes conversar por escrito sin concederlos. macOS te pedirá confirmar cada acceso.", bundle: .module))
            permission(String(localized: "Micrófono", bundle: .module), detail: String(localized: "Escuchar cuando decidas hablar.", bundle: .module)) {
                Task { await model.requestMicrophone() }
            }
            permission(String(localized: "Reconocimiento de voz", bundle: .module), detail: String(localized: "Convertir tu voz en texto.", bundle: .module)) {
                Task { await model.requestSpeechRecognition() }
            }
            permission(String(localized: "Calendario", bundle: .module), detail: String(localized: "Consultar la agenda para los avisos que actives.", bundle: .module)) {
                Task {
                    let granted = (try? await EKEventStore().requestFullAccessToEvents()) == true
                    permissionMessage = granted ? String(localized: "Calendario disponible.", bundle: .module) : String(localized: "Puedes seguir sin acceso al calendario.", bundle: .module)
                }
            }
            permission(String(localized: "Contactos", bundle: .module), detail: String(localized: "Encontrar los contactos para las acciones que solicites.", bundle: .module)) {
                Task {
                    let granted = (try? await CNContactStore().requestAccess(for: .contacts)) == true
                    permissionMessage = granted ? String(localized: "Contactos disponibles.", bundle: .module) : String(localized: "Puedes seguir sin acceso a contactos.", bundle: .module)
                }
            }
            permission(String(localized: "Automatización", bundle: .module), detail: String(localized: "macOS pedirá acceso a cada app al usarla por primera vez. Puedes revisar los permisos actuales.", bundle: .module)) {
                openPrivacy("Privacy_Automation")
            }
            Toggle(String(localized: "Quiero usar el control del Mac", bundle: .module), isOn: $computerUse)
            if computerUse {
                permission(String(localized: "Accesibilidad", bundle: .module), detail: String(localized: "Controlar botones y ventanas tras tu aprobación.", bundle: .module)) {
                    Task { await model.requestComputerControlAccess() }
                }
                permission(String(localized: "Grabación de pantalla", bundle: .module), detail: String(localized: "Ver la pantalla durante las acciones que solicites.", bundle: .module)) {
                    model.requestScreenCapture()
                }
            }
        case 3:
            Text(String(localized: "Puedes mantener a Jarvis disponible en la barra de menús al entrar en tu cuenta.", bundle: .module))
            Toggle(String(localized: "Abrir Jarvis al iniciar sesión", bundle: .module), isOn: Binding(get: { loginEnabled }, set: setLogin))
            Text(String(localized: "Si macOS pide autorización, confírmala en Ítems de inicio. También puedes cambiarlo después en Ajustes.", bundle: .module))
        default:
            Text(String(localized: "Prueba con este mensaje. Puedes seguir por escrito o usar el atajo de voz que aparece en Ajustes.", bundle: .module))
            Text(JarvisWelcomeState.example).padding(16).voiceEnrollmentCard()
            Button(String(localized: "Enviar mensaje de ejemplo", bundle: .module)) {
                starting = true
                Task {
                    defer { starting = false }
                    let issue = await JarvisWelcomeState.sendExample(model: model, store: store,
                        refresh: { await store.refresh(); await model.refreshDaemon() },
                        send: {
                            RequestedWindowFocus.open("chat") { openWindow(id: "chat") }
                            return await model.sendChatMessage(JarvisWelcomeState.example)
                        })
                    if let issue {
                        store.fail(issue == .submission ? model.chat.error ?? issue.message : issue.message)
                    } else {
                        finish(openChat: true)
                    }
                }
            }
            .disabled(starting || JarvisWelcomeState.exampleIssue(model: model, store: store) != nil)
            if let issue = JarvisWelcomeState.exampleIssue(model: model, store: store) {
                Text(issue.message).font(.callout).foregroundStyle(JarvisTheme.inkSecondary)
            }
        }
    }

    private func permission(_ title: String, detail: String, action: @escaping () -> Void) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).fontWeight(.medium)
                Text(detail).font(.caption).foregroundStyle(JarvisTheme.inkSecondary)
            }
            Spacer()
            Button(String(localized: "Revisar", bundle: .module), action: action).accessibilityLabel(String(localized: "Revisar permiso de \(title)", bundle: .module))
        }
    }

    private func setLogin(_ enabled: Bool) {
        do {
            let status = try JarvisLoginItemController.shared.setEnabled(enabled)
            loginEnabled = status == .enabled
            if status == .requiresApproval { JarvisLoginItemController.shared.openApprovalSettings() }
        } catch { store.fail(String(localized: "No se pudo cambiar el inicio automático. Revisa Ítems de inicio en macOS.", bundle: .module)) }
    }

    private func openPrivacy(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }

    private func finish(openChat: Bool = false) {
        completedVersion = JarvisWelcomeState.version
        dismissWindow(id: "welcome")
        if openChat { RequestedWindowFocus.open("chat") { openWindow(id: "chat") } }
    }
}
