import DesktopCore
import SwiftUI

struct TelegramPairingView: View {
    @Bindable var store: ChatStore
    @State private var agentID = "jarvis"
    @State private var review: TelegramPairingReview?

    private var editable: Bool { store.canChangeSession && !store.telegram.isChanging }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Enlazar tu chat desde Telegram").font(.headline)
            Text("Abre tu bot en la app Telegram y revisa aquí el chat propuesto. El enlace privado no se muestra ni se copia.")
                .font(.caption).foregroundStyle(.secondary)
            if let status = store.telegram.pairing {
                pairingStatus(status)
            }
            Picker("Agente del nuevo enlace", selection: $agentID) {
                ForEach(store.management.agents) { Text($0.name).tag($0.id) }
            }.disabled(!editable || store.telegram.pairing?.active == true)
            Button("Revisar e iniciar enlace…") {
                Task { review = await store.telegram.pairingReview(agentID: agentID) }
            }.disabled(!editable || store.telegram.snapshot?.connected != true || store.telegram.snapshot?.running == true ||
                       store.telegram.pairing?.active == true || (store.telegram.snapshot?.routes.count ?? 32) >= 32 ||
                       !store.management.agents.contains(where: { $0.id == agentID }))
            if store.telegram.snapshot?.connected != true {
                Text("Conecta primero el bot.").font(.caption).foregroundStyle(.secondary)
            } else if store.telegram.snapshot?.running == true {
                Text("Detén el canal antes de enlazar otro chat.").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
        .sheet(item: $review) { value in TelegramPairingReviewView(store: store, review: value) }
    }

    @ViewBuilder private func pairingStatus(_ status: TelegramPairingStatus) -> some View {
        if status.state == "listening" {
            Text("Esperando el inicio desde tu chat privado.")
            if status.challengeID != store.telegram.openingChallengeID {
                Button("Abrir bot en Telegram") { Task { await store.telegram.openPairing() } }
                    .disabled(!editable)
                    .accessibilityLabel("Abrir el bot en la app Telegram")
                    .help("Abre directamente la app Telegram instalada. No usa el navegador ni copia el enlace privado.")
            } else {
                Text("En Telegram, pulsa «Iniciar» si aparece. Después vuelve a Jarvis para revisar el chat propuesto.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        } else if status.state == "claimed", let chat = status.chatID {
            Text("Chat privado propuesto: \(String(chat))").font(.headline)
            Text("Bot: @\(status.username ?? "") · agente \(status.agentID ?? "") · versión \(status.agentRevision ?? 0)")
                .font(.caption).foregroundStyle(.secondary)
            Text("Aún no se creó una ruta ni se envió una respuesta. Revisa esta propuesta antes de confirmarla.")
                .font(.caption).foregroundStyle(.secondary)
            Button("Revisar y crear ruta…") { Task { await store.telegram.confirmPairing(status) } }.disabled(!editable)
        } else if status.state == "starting" {
            Text("Preparando el lector privado…").font(.caption).foregroundStyle(.secondary)
        } else if status.state == "confirmed" {
            Text(store.telegram.snapshot?.running == true
                 ? "Ruta creada. El canal se activó por separado."
                 : "Ruta creada. El canal sigue detenido hasta que lo actives por separado.")
                .font(.caption).foregroundStyle(.secondary)
        } else if status.state == "expired" {
            Text("El intento de enlace caducó. Puedes iniciar otro.").font(.caption).foregroundStyle(.secondary)
        } else if status.state == "cancelled" {
            Text("Intento de enlace cancelado.").font(.caption).foregroundStyle(.secondary)
        } else if status.state == "failed" {
            Text("El intento terminó con un error. Actualiza su estado antes de iniciar otro.").font(.caption).foregroundStyle(.red)
        }
        if status.active {
            if let expires = status.expiresAt { Text("Caduca: \(expires.formatted(date: .omitted, time: .standard))").font(.caption).foregroundStyle(.secondary) }
            Button("Cancelar enlace…", role: .destructive) { Task { await store.telegram.cancelPairing() } }.disabled(!editable)
        }
    }
}

private struct TelegramPairingReviewView: View {
    @Bindable var store: ChatStore
    let review: TelegramPairingReview
    @Environment(\.dismiss) private var dismiss
    @State private var updateConsumption = false
    @State private var noOtherConsumer = false

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            Text("Revisar enlace privado de Telegram").font(.title2.weight(.semibold))
            Text("Bot: @\(review.username) · ID \(String(review.botID))")
            Text("Agente: \(store.management.agents.first(where: { $0.id == review.agentID })?.name ?? review.agentID) · versión \(review.agentRevision)")
            Text("Carpeta: \(store.access.workspacePath ?? "Espacio privado de Jarvis")").font(.caption).textSelection(.enabled)
            Text("El intento dura como máximo 180 segundos. Se leerán las actualizaciones del bot y se avanzará su cursor, también para mensajes ignorados. No uses un bot que atienda otro programa.")
            Text("Telegram conserva el mensaje de inicio con su código privado. Jarvis no muestra ese código, no lo copia y no lo envía a Codex. Tendrás que abrir Telegram y quizá pulsar «Iniciar». Después habrá otra revisión para crear la ruta; no se activa el canal automáticamente.")
                .foregroundStyle(.secondary)
            Toggle("Acepto leer y avanzar las actualizaciones del bot, incluidas las ignoradas", isOn: $updateConsumption)
            Toggle("Confirmo que este bot no tiene otro consumidor", isOn: $noOtherConsumer)
            DisclosureGroup("Identidad exacta de la revisión") {
                Text(ManagementValidation.json(review.reviewValue))
                    .font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
            }
            if let error = store.telegram.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Volver") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Iniciar enlace…") {
                    Task {
                        if await store.telegram.startPairing(review, updateConsumption: updateConsumption, noOtherConsumer: noOtherConsumer) { dismiss() }
                    }
                }.disabled(!updateConsumption || !noOtherConsumer || !store.canChangeSession || store.telegram.isChanging)
            }
        }.padding(24).frame(width: 640)
    }
}
