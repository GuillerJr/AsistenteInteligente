import DesktopCore
import SwiftUI

@MainActor struct ResidentServiceView: View {
    let store: ChatStore
    let login: LoginItemController
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Form {
            Section("Servicio en tu sesión") {
                LabeledContent("Estado", value: serviceStatus)
                if let owner = store.serviceOwner {
                    LabeledContent("PID bajo custodia", value: String(owner.processIdentifier))
                }
                Text("Cerrar la ventana mantiene esta app y su servicio local en la barra de menús. «Salir de Jarvis» espera el cierre de las herramientas y del servicio; si no se confirma, conserva la supervisión.")
                Text("La recuperación del hijo Python es limitada y exige identidad y perfil exactos. No reenvía ejecuciones ni vuelve a activar herramientas o Telegram. Si falla la app completa, no se promete su relanzamiento automático.")
                    .font(.caption).foregroundStyle(.secondary)
                Text(store.statusText).font(.caption).foregroundStyle(.secondary)
                if let error = store.errorMessage { Text(error).font(.caption).foregroundStyle(.red) }
            }
            Section("Abrir al iniciar sesión") {
                LabeledContent("Estado de macOS", value: login.status.title)
                HStack {
                    Button("Activar arranque…") { change(enabled: true) }.disabled(!login.canEnable)
                    Button("Desactivar arranque…") { change(enabled: false) }.disabled(!login.canDisable)
                    Button("Ítems de inicio…") { login.openSettings() }
                        .disabled(login.isChanging)
                    Button("Consultar estado") { login.refresh() }.disabled(login.isChanging)
                }
                if !login.eligible {
                    Text("El arranque sólo se modifica desde un paquete autocontenido íntegro y su perfil principal. Está deshabilitado para desarrollo, cualificación, perfiles restaurados y cambios de servicio en curso.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("Esta opción abre Jarvis después de iniciar tu sesión de macOS. No es un daemon de sistema y no mantiene el servicio después de elegir Salir. Desactivarla conserva la app y el servicio que ya están abiertos.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("El próximo inicio usa el perfil principal de este paquete. No habilita capturas, herramientas, canales ni cuentas por sí mismo.")
                    .font(.caption).foregroundStyle(.secondary)
                if let notice = login.notice { Text(notice).font(.caption).accessibilityIdentifier("jarvis.login-item-notice") }
            }
        }
        .formStyle(.grouped)
        .onAppear { login.refresh() }
        .onChange(of: scenePhase) { _, phase in if phase == .active { login.refresh() } }
    }

    private var serviceStatus: String {
        if store.residentCleanupUnconfirmed { return "Cierre sin confirmar; se conserva la custodia" }
        if store.isShuttingDown { return "Cerrando y asentando los recursos" }
        if store.starting { return "Iniciando el servicio local" }
        if store.residentRecoveryBlocked { return "Recuperación detenida; requiere revisión" }
        if store.ready && store.residentServiceRunning { return "En ejecución" }
        if store.residentServiceRunning { return "Proceso presente; estado local sin confirmar" }
        return "Sin servicio confirmado"
    }

    private func change(enabled: Bool) {
        Task { @MainActor in
            await login.change(enabled: enabled) {
                OwnerManagementReview.confirm(
                    title: enabled ? "Abrir Jarvis al iniciar sesión" : "Desactivar el arranque de Jarvis",
                    action: enabled ? "Solicitar el arranque" : "Desactivar el arranque",
                    detail: enabled
                        ? "Registrar sólo esta app mediante ServiceManagement. macOS puede pedirte aprobación en Ítems de inicio. Al iniciar sesión se abrirán Jarvis y su servicio local con el perfil principal; no se habilitan cuentas, capturas ni herramientas. Salir detendrá el servicio."
                        : "Retirar sólo esta app de los Ítems de inicio. La app y el servicio actual siguen abiertos hasta que elijas Salir.",
                    isCurrent: { !store.isShuttingDown && !store.starting })
            }
        }
    }
}
