import DesktopCore
import SwiftUI

/// Discovers only the group that receives the owner's one-use command. It
/// never enumerates groups, reads their history, posts a message or infers IDs.
struct TelegramGroupDiscoveryView: View {
    @Bindable var store: ChatStore
    @State private var ownerChatID: Int?
    @State private var agentID = "jarvis"
    @State private var attachments = false
    @State private var review: TelegramGroupDiscoveryReview?

    private var privateRoutes: [TelegramRouteSnapshot] {
        store.telegram.snapshot?.routes.filter { $0.chatType == .privateChat && $0.enabled } ?? []
    }
    private var editable: Bool {
        store.canChangeSession && !store.telegram.isChanging
            && store.telegram.snapshot?.connected == true && store.telegram.snapshot?.running == false
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Añadir un grupo sin buscar su ID").font(.headline)
            Text("1. Enlaza primero tu chat privado con este bot. 2. Copia un código de un solo uso. 3. Envíalo tú en el grupo y confirma aquí el destino descubierto.")
                .font(.caption).foregroundStyle(.secondary)
            if let status = store.telegram.groupDiscovery { discoveryStatus(status) }
            if let notice = store.telegram.discoveryNotice {
                Text(notice).font(.caption).foregroundStyle(.orange)
            }
            if privateRoutes.isEmpty {
                Text("Necesitas una ruta privada habilitada. Usa «Enlazar tu chat desde Telegram» antes de añadir el grupo.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Picker("Tu chat privado", selection: $ownerChatID) {
                    Text("Elige el chat que acabas de enlazar").tag(Int?.none)
                    ForEach(privateRoutes) { route in Text("Chat privado \(String(route.chatID))").tag(Optional(route.chatID)) }
                }.disabled(!editable || store.telegram.pairingActive)
                Picker("Agente del grupo", selection: $agentID) {
                    ForEach(store.management.agents) { Text($0.name).tag($0.id) }
                }.disabled(!editable || store.telegram.pairingActive)
                Toggle("Admitir adjuntos; cada lectura requiere aprobación nativa", isOn: $attachments)
                    .disabled(!editable || store.telegram.pairingActive)
                Text("Jarvis comprobará una prueba privada fresca en memoria; una ruta guardada por sí sola no basta. Si te pide renovarla, vuelve al enlace privado y verifica ese mismo chat. Su conversación se conserva.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Revisar descubrimiento de grupo…") {
                    guard let ownerChatID else { return }
                    Task { review = await store.telegram.reviewGroupDiscovery(ownerChatID: ownerChatID,
                        agentID: agentID, allowAttachments: attachments) }
                }.disabled(!editable || store.telegram.pairingActive || ownerChatID == nil
                           || !privateRoutes.contains(where: { $0.chatID == ownerChatID })
                           || !store.management.agents.contains(where: { $0.id == agentID })
                           || (store.telegram.snapshot?.routes.count ?? 32) >= 32)
                if let error = store.telegram.errorMessage {
                    Text(error).font(.caption).foregroundStyle(.red).textSelection(.enabled)
                }
            }
            if store.telegram.snapshot?.running == true {
                Text("Detén el canal antes de descubrir un grupo.").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
        .sheet(item: $review) { TelegramGroupDiscoveryReviewView(store: store, review: $0) }
    }

    @ViewBuilder private func discoveryStatus(_ status: TelegramGroupDiscoveryStatus) -> some View {
        if status.state == "starting" {
            Text("Preparando el lector de descubrimiento…").font(.caption)
        } else if status.state == "listening" {
            if let review = store.telegram.discoveryReview {
                Text("Bot @\(review.username) · usa la cuenta de tu chat privado \(String(review.ownerChatID)).")
                    .font(.caption)
                if status.challengeID != store.telegram.copiedDiscoveryChallengeID {
                    Button("Revisar y copiar código para el grupo…") { Task { await store.telegram.copyGroupDiscoveryCommand() } }
                        .disabled(!editable)
                } else {
                    Text("En Telegram, pega y envía el código en el grupo que quieres añadir. Si usas temas, envíalo dentro del tema exacto. Vuelve aquí después; Jarvis sólo leerá esa prueba, sin publicar nada por ti.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Text("Falta la revisión local que inició esta prueba. Cancélala e inicia otra; no se recuperará ni copiará un código sin esa revisión.")
                    .font(.caption).foregroundStyle(.orange)
            }
        } else if status.state == "claimed" {
            Text("Grupo detectado: \(String(status.chatID ?? 0))").font(.headline)
            Text("Tipo: \(status.chatType?.label ?? "Grupo") · remitente \(String(status.senderID ?? 0))").font(.caption)
            Text(status.messageThreadID.map { "Sólo tema \($0)" } ?? "Sólo mensajes sin tema").font(.caption)
            Text("No se creó una ruta ni se activó el canal. Confirma que es el grupo y tema donde enviaste el código.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Revisar y añadir este grupo…") { Task { await store.telegram.confirmGroupDiscovery(status) } }
                .disabled(!editable || store.telegram.discoveryReview == nil)
        } else if status.state == "confirmed" {
            Text("Grupo añadido y verificado. Su canal se activa por separado.").font(.caption).foregroundStyle(.secondary)
        } else if ["expired", "cancelled", "failed"].contains(status.state) {
            Text(status.state == "expired" ? "El código caducó. Puedes revisar e iniciar otro descubrimiento." : "El descubrimiento terminó. Revisa el estado antes de iniciar otro.")
                .font(.caption).foregroundStyle(.secondary)
        }
        if status.active {
            if let expires = status.expiresAt { Text("Caduca: \(expires.formatted(date: .omitted, time: .standard))").font(.caption).foregroundStyle(.secondary) }
            Button("Cancelar descubrimiento…", role: .destructive) { Task { await store.telegram.cancelGroupDiscovery() } }
                .disabled(!store.telegram.mayReduceAuthority)
        }
    }
}

private struct TelegramGroupDiscoveryReviewView: View {
    @Bindable var store: ChatStore
    let review: TelegramGroupDiscoveryReview
    @Environment(\.dismiss) private var dismiss
    @State private var consumption = false
    @State private var exclusive = false
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Revisar descubrimiento de grupo").font(.title2.weight(.semibold))
            Text("Bot @\(review.username) · chat privado \(String(review.ownerChatID))")
            Text("Agente \(review.agentID) · versión \(review.agentRevision)")
            Text(review.allowAttachments ? "La nueva ruta admitirá adjuntos con lectura aprobada en Jarvis." : "La nueva ruta aceptará únicamente texto.")
                .font(.caption)
            Text("El código dura como máximo 180 segundos. Jarvis consumirá las actualizaciones del bot, también las ignoradas. No uses un bot conectado a otro programa. Sólo se aceptará el mensaje del mismo usuario que verificó el chat privado; bots y remitentes anónimos quedan excluidos.")
            Text("Copiarás el código tras otra revisión y lo enviarás tú en Telegram. El grupo y tema descubiertos aparecerán aquí para tu confirmación antes de crear la ruta. Telegram conservará el mensaje del código. No se envía a Codex ni activa el canal.")
                .foregroundStyle(.secondary)
            Toggle("Acepto leer y avanzar las actualizaciones, incluidas las ignoradas", isOn: $consumption)
            Toggle("Confirmo que este bot no tiene otro consumidor", isOn: $exclusive)
            DisclosureGroup("Revisión exacta") {
                Text(ManagementValidation.json(review.reviewValue)).font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
            }
            if let error = store.telegram.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer(); Button("Volver") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Iniciar descubrimiento…") {
                    Task { if await store.telegram.startGroupDiscovery(review, updateConsumption: consumption,
                        noOtherConsumer: exclusive) { dismiss() } }
                }.disabled(!consumption || !exclusive || !store.canChangeSession || store.telegram.isChanging || store.telegram.pairingActive)
            }
        }.padding(24).frame(width: 670)
    }
}
