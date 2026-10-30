import SwiftUI

struct ComposerView: View {
    @Bindable var store: ChatStore
    @FocusState private var focused: Bool

    private var phaseLabel: String {
        switch store.runPhase {
        case "queued": return "En cola"
        case "running": return "Codex está respondiendo…"
        case "waiting_approval": return "Esperando tu aprobación"
        case "cancelling": return "Cancelación solicitada…"
        case "cleaning": return "Terminando la limpieza…"
        case "disconnected": return "Estado pendiente: conexión interrumpida"
        case "checking": return "Consultando el estado de la ejecución…"
        case "completed": return "Completado"
        case "failed": return "No se pudo completar"
        case "cancelled": return "Cancelado y asentado"
        case "interrupted": return "Interrumpido"
        default: return ""
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !phaseLabel.isEmpty {
                HStack {
                    if store.activeRunID != nil && store.runPhase != "disconnected" {
                        ProgressView().controlSize(.small)
                    }
                    Text(phaseLabel).font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if store.runPhase == "disconnected" {
                        Button("Consultar estado") { store.resumeStatus() }
                    }
                }
            }
            AttachmentComposerView(store: store)
            VStack(spacing: 10) {
                TextField("Escribe un mensaje…", text: $store.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(2...7).focused($focused)
                    .onSubmit { if store.canSend { Task { await store.send() } } }
                    .disabled(!store.ready || store.activeRunID != nil || store.talk.isActive)
                HStack {
                    Text(store.selected?.temporary == true ? "Chat temporal" : "Codex · ChatGPT")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    if store.talk.isActive {
                        Button("Detener conversación", systemImage: "stop.circle") { store.stopVoice() }
                    } else {
                        Button("Conversar por voz", systemImage: "waveform") { store.startVoiceConversation() }
                            .disabled(!store.canUseVoice || !store.draft.isEmpty || !store.attachments.selectedIDs.isEmpty)
                    }
                    LocalVoiceControlsView(voice: store.voice, canUseVoice: store.canUseVoice,
                                           start: store.startDictation, cancel: store.stopVoice)
                    if store.activeRunID != nil {
                        Button("Cancelar", systemImage: "stop.fill") { Task { await store.cancel() } }
                            .disabled(["cancelling", "cleaning"].contains(store.runPhase))
                    } else {
                        Button("Enviar", systemImage: "arrow.up") { Task { await store.send() } }
                            .buttonStyle(.borderedProminent).disabled(!store.canSend)
                            .keyboardShortcut(.return, modifiers: [.command])
                    }
                }
            }
            .padding(16).background(.quaternary, in: .rect(cornerRadius: 16))
            Text(store.access.toolsEnabled ? "Herramientas disponibles · Las operaciones que cambian datos requieren tu aprobación"
                 : "Herramientas de la base pendientes de construcción")
                .font(.caption2).foregroundStyle(.secondary)
            if store.voice.phase != .idle || store.voice.issue != nil {
                Text(store.voice.statusText).font(.caption).foregroundStyle(store.voice.issue == nil ? Color.secondary : Color.red)
                if !store.voice.partialText.isEmpty {
                    Text(store.voice.partialText).font(.caption).foregroundStyle(.secondary).privacySensitive()
                }
            }
            if store.talk.isActive || store.talk.stopReason != nil {
                Text(store.talk.statusText).font(.caption).foregroundStyle(.secondary)
                if store.talk.partialForReview != nil && !store.talk.isActive {
                    Button("Pasar texto parcial al borrador") { store.preserveSpokenPartial() }
                }
                if store.talk.phase == .stopping {
                    Button("Comprobar cierre de voz") { store.talk.retryCleanup() }
                }
            }
            if let notice = store.dictationNotice { Text(notice).font(.caption).foregroundStyle(.secondary) }
            if let receipt = store.selectedVoiceCaptureReceipt, let projection = receipt.wakeProjection {
                DisclosureGroup("Revisar activación local") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Coincidencias en el texto reconocido: \(projection.matchCount). Se usa la primera; las menciones posteriores se conservan como dictado.")
                        Text("Caracteres anteriores a la primera coincidencia: \(projection.triggerCharacterOffset). Caracteres retirados antes del sufijo: \(projection.discardedPrefixCharacters).")
                        Text("Último sufijo proyectado: \(projection.suffixUTF8Bytes) bytes UTF-8.")
                        Text("SHA-256 del sufijo: \(projection.suffixSHA256)").font(.caption2.monospaced()).textSelection(.enabled)
                        if receipt.outcome != .transcript {
                            Text("La captura terminó sin añadir texto; estos datos corresponden a la última hipótesis proyectada.")
                        }
                        Text("Estos metadatos permanecen en memoria. No contienen audio ni el texto anterior a la activación.")
                    }.font(.caption).foregroundStyle(.secondary)
                }.font(.caption)
            }
        }
        .onAppear { focused = true }
    }
}
