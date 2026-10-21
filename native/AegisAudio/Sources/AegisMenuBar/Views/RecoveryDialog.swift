import SwiftUI

struct RecoveryDialog: View {
    let model: MenuBarModel
    let recovery: PendingRecovery
    var interactive = true
    @Environment(\.openWindow) private var openWindow
    @State private var checking = false
    @State private var checkResult: String?

    var body: some View {
        JarvisDialogShell(title: "La tarea necesita atención", symbol: "exclamationmark.bubble",
                          detail: "Revisa lo ocurrido antes de continuar.", height: 430) {
            Text(recovery.message).font(.system(size: 14))
                .fixedSize(horizontal: false, vertical: true)
                .padding(14).frame(maxWidth: .infinity, alignment: .leading).voiceEnrollmentCard()
            if recovery.outcomeUnknown {
                Text("Comprueba el resultado en la aplicación o servicio antes de volver a solicitar la acción. Jarvis no la repetirá automáticamente.")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Puedes revisar las cuentas, los modelos y los permisos en Configuración.")
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
            if let checkResult {
                Text(checkResult).font(.system(size: 12, weight: .medium))
                    .fixedSize(horizontal: false, vertical: true)
            }
        } footer: {
            HStack {
                Button("Cerrar", role: .cancel) { Task { await model.cancelInteraction() } }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button(checking ? "Comprobando…" : "Comprobar conexión") {
                    checking = true
                    Task {
                        await model.refreshDaemon()
                        await model.refreshPrivacyCapabilities()
                        checkResult = model.daemonState == .online
                            ? (model.securityState == .intact
                               ? "Servicio local conectado. Puedes revisar las cuentas y modelos en Configuración."
                               : "El servicio está conectado; falta verificar su seguridad para continuar.")
                            : "El servicio local sigue sin conexión. Revisa Diagnóstico en Configuración."
                        checking = false
                    }
                }.disabled(checking)
                Button("Configuración") {
                    model.pendingRecovery = nil
                    RequestedWindowFocus.open("settings") { openWindow(id: "settings") }
                }
            }.disabled(!interactive)
        }
    }
}
