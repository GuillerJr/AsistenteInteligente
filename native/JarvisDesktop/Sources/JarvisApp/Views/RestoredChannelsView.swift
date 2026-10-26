import DesktopCore
import SwiftUI

struct RestoredChannelsView: View {
    @Bindable var channels: ChannelReconciliationStore
    let telegram: TelegramStore
    let navigation: SettingsOwnerNavigation
    let mayBegin: Bool
    var body: some View {
        Form {
            Section("Rutas conservadas de Telegram") {
                Text("Revisa cada ruta del perfil restaurado y decide si habilitarla o deshabilitarla localmente. Se conservan el chat, agente, conversación e historial; esta decisión no conecta el bot ni inicia la recepción.")
                Text("Después conecta explícitamente el mismo bot, completa un enlace privado nuevo para cada chat habilitado y activa el canal desde Telegram. La revisión local no verifica una cuenta externa ni concede permisos de herramientas.").font(.caption).foregroundStyle(.secondary)
                Button("Actualizar rutas y estado") { Task { await telegram.refreshFromUI(); await channels.refresh() } }.disabled(channels.isRequesting)
                if let snapshot = telegram.snapshot {
                    ForEach(snapshot.routes) { route in
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Chat \(route.chatID) · \(route.agentID)")
                            Text("Revisión \(route.revision) · \(route.enabled ? "habilitada" : "deshabilitada")").font(.caption)
                            HStack {
                                Button(route.enabled ? "Revisar deshabilitación" : "Revisar reconciliación") {
                                    Task { await channels.prepare(chatID: route.chatID, revision: route.revision, enabled: !route.enabled) }
                                }.disabled(!mayBegin || !channels.mayPrepare || snapshot.running)
                                Button("Inspeccionar ruta") { Task { await channels.inspect(chatID: route.chatID) } }.disabled(!channels.mayPrepare)
                            }
                        }
                    }
                    if snapshot.routes.isEmpty { Text("Este perfil no contiene rutas de Telegram.") }
                }
            }
            if let error = channels.errorMessage { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
            if let review = channels.review {
                Section("Revisión exacta de la ruta") {
                    Text("Bot histórico: \(review.botID) · Chat: \(review.chatID)")
                    Text("Agente: \(review.agentID) · Conversación: \(review.sessionID)").font(.caption).textSelection(.enabled)
                    Text("Carpeta: \(review.workspacePath)").textSelection(.enabled)
                    Text(review.enabledAfter ? "La ruta quedará habilitada localmente; el canal seguirá detenido." : "La ruta quedará deshabilitada. Se conservarán su conversación y los recibos anteriores.")
                    if channels.operation == nil {
                        HStack {
                            Button("Solicitar aprobación de la ruta") { Task { await channels.submit() } }.disabled(!mayBegin || !channels.mayPrepare || review.expiresAt <= Date())
                            Button("Descartar revisión") { channels.discardReview() }.disabled(!channels.mayPrepare)
                        }
                    }
                }
            }
            if let operation = channels.operation {
                Section("Resultado de reconciliación") {
                    Text(MaintenancePresentation.phase(operation.phase)).font(.headline)
                    Text("Operación: \(operation.id)").font(.caption.monospaced()).textSelection(.enabled)
                    if let notice = OwnerDecisionNotice(tab: "channels-restored", operation: operation, reviewID: channels.review?.id, expiresAt: channels.review?.expiresAt, unconfirmed: channels.responseUnconfirmed) { OwnerDecisionStatusView(notice: notice) }
                    if channels.canDecide, let review = channels.review, let approval = channels.approval {
                        OwnerDecisionValidityView(expiresAt: min(review.expiresAt, approval.expiresAt)) {
                        HStack {
                            Button("Autorizar reconciliación local") { Task { await channels.decide(allow: true) } }.buttonStyle(.borderedProminent)
                            Button("No autorizar") { Task { await channels.decide(allow: false) } }
                        }
                        }
                    }
                    if !operation.settled { Button("Cancelar operación") { Task { await channels.cancel() } }.disabled(channels.isRequesting) }
                    if operation.cleanupOwnerPending {
                        Text("La operación conserva recursos pendientes de cierre. Completa sólo esa limpieza; no se repetirá el cambio de ruta.").foregroundStyle(.orange)
                        Button("Reintentar sólo la limpieza propia") { Task { await channels.cleanup() } }.disabled(channels.isRequesting)
                    }
                    if operation.success { Text("Cambio local confirmado. No se conectó el bot ni se enviaron mensajes.") }
                    if let code = operation.errorCode { Text("Diagnóstico: \(code)").font(.caption).textSelection(.enabled) }
                    Button("Consultar estado de la operación") { Task { await channels.refresh(); await telegram.refreshFromUI() } }.disabled(channels.isRequesting)
                }.id(operation.id)
            }
            if channels.responseUnconfirmed { Section { Text("El resultado de la solicitud sigue sin confirmar. Se consultará su identidad exacta; no se reenviará la operación ni se renovará su permiso.").foregroundStyle(.orange) } }
            if let observation = channels.observation {
                Section("Observación local actual") {
                    Text("Chat \(observation.route.chatID) · Revisión \(observation.route.revision) · \(observation.route.enabled ? "habilitada" : "deshabilitada")")
                    Text("Esta lectura no acredita el ACK anterior, no cambia ningún recibo ni permite repetir la operación.").font(.caption)
                }
            }
        }.formStyle(.grouped).ownerDecisionScrolling(navigation, tab: "channels-restored")
            .task { await telegram.refreshFromUI(); await channels.refresh() }
            .onChange(of: channels.operation?.resourcesPending) { _, pending in
                if pending == false, channels.operation?.settled == true, !channels.responseUnconfirmed {
                    Task { await telegram.refreshFromUI() }
                }
            }
    }
}
