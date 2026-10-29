import DesktopCore
import SwiftUI

struct TelegramGroupPairingView: View {
    @Bindable var store: ChatStore
    @State private var review: TelegramGroupPairingReview?
    private var editable: Bool { store.canChangeSession && !store.telegram.isChanging && store.telegram.snapshot?.running == false }
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Verificar grupos y temas").font(.headline)
            Text("Añade primero una ruta de grupo con sus remitentes autorizados. La prueba no publica mensajes automáticamente: copiarás un código y lo pegarás tú en el destino exacto.").font(.caption).foregroundStyle(.secondary)
            if let status = store.telegram.groupPairing {
                if status.active {
                    Text("Grupo \(String(status.targetChatID ?? 0)) · \(status.chatType?.label ?? "Grupo")").font(.headline)
                    if let topic = status.messageThreadID { Text("Tema \(topic)").font(.caption) }
                    if status.state == "listening" {
                        if store.telegram.copiedGroupChallengeID != status.base.challengeID {
                            Button("Revisar y copiar código…") { Task { await store.telegram.copyGroupPairingCommand() } }.disabled(!editable)
                        } else { Text("Pega el código en ese grupo y tema desde un remitente autorizado. Vuelve aquí para confirmar la prueba.").font(.caption) }
                    } else if status.state == "claimed" {
                        Text("Código recibido del remitente \(String(status.senderID ?? 0)).").font(.caption)
                        Button("Revisar y verificar ruta…") { Task { await store.telegram.confirmGroupPairing(status) } }.disabled(!editable)
                    } else { Text("Preparando lector…").font(.caption) }
                    if let expires = status.base.expiresAt { Text("Caduca: \(expires.formatted(date: .omitted, time: .standard))").font(.caption) }
                    Button("Cancelar prueba…", role: .destructive) { Task { await store.telegram.cancelGroupPairing() } }.disabled(!store.telegram.mayReduceAuthority)
                } else if status.state != "idle" {
                    Text(status.state == "confirmed" ? "Grupo verificado; el canal se activa por separado." : "La prueba terminó. Revisa el estado antes de iniciar otra.").font(.caption).foregroundStyle(.secondary)
                }
            }
            ForEach(store.telegram.snapshot?.routes.filter { $0.chatType != .privateChat && $0.enabled } ?? []) { route in
                Button("Revisar prueba del grupo \(String(route.chatID))…") { Task { review = await store.telegram.groupPairingReview(route: route) } }
                    .disabled(!editable || store.telegram.snapshot?.connected != true || store.telegram.pairingActive)
            }
        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
        .sheet(item: $review) { TelegramGroupReviewView(store: store, review: $0) }
    }
}

private struct TelegramGroupReviewView: View {
    @Bindable var store: ChatStore
    let review: TelegramGroupPairingReview
    @Environment(\.dismiss) private var dismiss
    @State private var consumption = false
    @State private var exclusive = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Revisar prueba de grupo").font(.title2.weight(.semibold))
            Text("Bot @\(review.base.username) · grupo \(String(review.chatID)) · ruta v\(review.routeRevision)")
            Text("Remitentes: " + review.policy.allowedSenderIDs.map(String.init).joined(separator: ", "))
            Text(review.policy.messageThreadID.map { "Sólo tema \($0)" } ?? "Sólo mensajes sin tema")
            Text("Durante 180 segundos Jarvis consumirá las actualizaciones del bot, incluidas las ignoradas. El código de un solo uso se copiará únicamente tras otra revisión; debes publicarlo tú. Telegram conservará ese mensaje. Después confirmarás aquí el grupo y remitente. No se activa la ruta automáticamente.").foregroundStyle(.secondary)
            Toggle("Acepto leer y avanzar las actualizaciones, incluidas las ignoradas", isOn: $consumption)
            Toggle("Confirmo que este bot no tiene otro consumidor", isOn: $exclusive)
            DisclosureGroup("Revisión exacta") { Text(ManagementValidation.json(review.reviewValue)).font(.system(.caption2, design: .monospaced)).textSelection(.enabled) }
            if let error = store.telegram.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer(); Button("Volver") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Iniciar prueba…") { Task { if await store.telegram.startGroupPairing(review, updateConsumption: consumption, noOtherConsumer: exclusive) { dismiss() } } }
                    .disabled(!consumption || !exclusive || !store.canChangeSession || store.telegram.isChanging || store.telegram.pairingActive)
            }
        }.padding(24).frame(width: 650)
    }
}
