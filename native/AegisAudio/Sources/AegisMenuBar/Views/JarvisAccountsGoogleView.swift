import SwiftUI

struct JarvisAccountsGoogleView: View {
    let store: JarvisSettingsStore
    let server: JarvisIntegrationSettings
    @Environment(\.dismiss) private var dismiss
    @State private var email: String
    @FocusState private var emailFocused: Bool

    init(store: JarvisSettingsStore, server: JarvisIntegrationSettings) {
        self.store = store
        self.server = server
        // Seed this presentation's draft once; subsequent refreshes must not replace typed input.
        _email = State(initialValue: server.accounts.first ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Label(String(localized: "Cuenta de Google", bundle: .module), systemImage: "person.crop.circle")
                .font(.system(size: 20, weight: .semibold))
            Text(String(localized: "Escribe la cuenta que quieres usar. Google solicitará autorización en tu navegador; Jarvis verificará que el correo coincida antes de guardar el cambio.", bundle: .module))
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            TextField(String(localized: "Correo de Google", bundle: .module), text: $email)
                .focused($emailFocused).jarvisInput(focused: emailFocused)
                .accessibilityLabel(String(localized: "Correo de la cuenta de Google", bundle: .module))
            Text(String(localized: "La conexión actual se pausará durante la autorización. Después podrás aplicar los cambios para reiniciar el servicio.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
            HStack {
                Button(String(localized: "Cancelar", bundle: .module)) { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(String(localized: "Autorizar en Google", bundle: .module)) {
                    let account = email.trimmingCharacters(in: .whitespacesAndNewlines)
                    dismiss()
                    Task {
                        await store.perform("settings.google.connect", payload: [
                            "server_id": server.id, "email": account, "confirm": true,
                        ], success: String(localized: "Cuenta conectada. Aplica los cambios para reiniciar el servicio.", bundle: .module))
                    }
                }
                .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                .disabled(!validEmail || store.busy)
            }
        }
        .padding(24).frame(width: 480).jarvisSheet()
        .onAppear { emailFocused = true }
    }

    private var validEmail: Bool {
        email.trimmingCharacters(in: .whitespacesAndNewlines)
            .range(of: "^[A-Za-z0-9][A-Za-z0-9._+%-]*@[A-Za-z0-9.-]+\\.[A-Za-z]{2,63}$", options: .regularExpression) != nil
    }
}
