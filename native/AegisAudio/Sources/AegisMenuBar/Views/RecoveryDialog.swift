import SwiftUI

struct RecoveryDialog: View {
    let model: MenuBarModel
    let recovery: PendingRecovery
    var interactive = true
    @Environment(\.openWindow) private var openWindow
    @State private var checking = false
    @State private var checkResult: String?

    var body: some View {
        JarvisDialogShell(title: String(localized: "La tarea necesita atención", bundle: .module), symbol: "exclamationmark.bubble",
                          detail: String(localized: "Revisa lo ocurrido antes de continuar.", bundle: .module), height: 430) {
            Text(recovery.message).font(.system(size: 14))
                .fixedSize(horizontal: false, vertical: true)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading).voiceEnrollmentCard()
            if recovery.outcomeUnknown {
                Text(String(localized: "Comprueba el resultado en la aplicación o servicio antes de volver a solicitar la acción. Jarvis no la repetirá automáticamente.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(String(localized: "Puedes revisar las cuentas, los modelos y los permisos en Configuración.", bundle: .module))
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            if let checkResult {
                Text(checkResult).font(.system(size: 12, weight: .medium))
                    .fixedSize(horizontal: false, vertical: true)
            }
        } footer: {
            HStack {
                Button(String(localized: "Cerrar", bundle: .module), role: .cancel) { Task { await model.cancelInteraction() } }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button(checking ? "Comprobando…" : String(localized: "Comprobar conexión", bundle: .module)) {
                    checking = true
                    Task {
                        await model.refreshDaemon()
                        await model.refreshPrivacyCapabilities()
                        checkResult = model.daemonState == .online
                            ? (model.securityState == .intact
                               ? String(localized: "Servicio local conectado. Puedes revisar las cuentas y modelos en Configuración.", bundle: .module)
                               : String(localized: "El servicio está conectado; falta verificar su seguridad para continuar.", bundle: .module))
                            : String(localized: "El servicio local sigue sin conexión. Revisa Diagnóstico en Configuración.", bundle: .module)
                        checking = false
                    }
                }.disabled(checking)
                Button(String(localized: "Configuración", bundle: .module)) {
                    model.pendingRecovery = nil
                    RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
                }
            }.disabled(!interactive)
        }
    }
}
