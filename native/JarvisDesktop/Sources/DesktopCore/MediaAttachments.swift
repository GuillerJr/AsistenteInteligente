import CryptoKit
import Foundation
import Observation

public struct MediaAttachment: Identifiable, Equatable, Sendable {
    public let id: String
    public let scope: SessionScope
    public let workspaceID: String
    public let name: String
    public let mime: String
    public let kind: String
    public let bytes: Int
    public let sha256: String
    public let revoked: Bool
    public init(_ value: RPCValue) throws {
        guard let row = value.object,
              Set(row.keys) == ["format", "attachment_id", "session_id", "workspace_id", "temporary", "name", "mime", "kind", "byte_length", "sha256", "source", "revoked"],
              value["format"] == .string("jarvis-attachment/1"),
              let id = value["attachment_id"].string, ManagementValidation.identifier(id),
              let session = value["session_id"].string, ManagementValidation.identifier(session),
              let temporary = value["temporary"].bool,
              let workspace = value["workspace_id"].string, ManagementValidation.hash(workspace),
              let name = value["name"].string, MediaFilePolicy.safeName(name),
              let mime = value["mime"].string, ManagementValidation.text(mime, limit: 255),
              let kind = value["kind"].string, ["document", "pdf", "image", "audio", "video"].contains(kind),
              let bytes = value["byte_length"].integer, (1...16 * 1024 * 1024).contains(bytes),
              let sha = value["sha256"].string, ManagementValidation.hash(sha),
              let source = value["source"].string, ManagementValidation.text(source, limit: 64),
              let revoked = value["revoked"].bool else { throw RPCFailure.invalidResponse }
        self.id = id; scope = SessionScope(sessionID: session, temporary: temporary)
        workspaceID = workspace; self.name = name; self.mime = mime; self.kind = kind
        self.bytes = bytes; sha256 = sha; self.revoked = revoked
    }
    public var review: RPCValue { .object(["attachment_id": .string(id), "name": .string(name),
        "byte_length": .number(Double(bytes)), "sha256": .string(sha256), "mime": .string(mime)]) }
}

public enum MediaFilePolicy {
    private static let privateNames: Set<String> = ["auth.json", "history", "history.json", "chat_history.json", "conversations.json", ".ssh", ".codex", ".aws", ".gnupg", ".git", ".env", ".jarvis-attachments", "credentials.json", "cookies", "keychains", "id_rsa", "id_ed25519", "transport.token", ".zsh_history", ".bash_history", ".git-credentials", ".npmrc", ".netrc"]
    public static func safeName(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 255 && ![".", ".."].contains(value)
        && !value.contains("/") && !value.contains("\\")
        && !value.unicodeScalars.contains(where: { $0.value < 32 })
    }
    public static func selectablePath(_ url: URL) -> Bool {
        url.isFileURL && url.path.hasPrefix("/") && safeName(url.lastPathComponent)
        && !url.standardizedFileURL.pathComponents.contains { component in
            let name = component.lowercased()
            return privateNames.contains(name) || name.hasPrefix(".env.")
        }
    }
    public static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

public struct MediaPreview: Identifiable {
    public var id: String { attachment.id }
    public let attachment: MediaAttachment
    public let data: Data
    public let playbackMIME: String?
    public let playbackSHA: String?
    public init(attachment: MediaAttachment, data: Data, playbackMIME: String? = nil, playbackSHA: String? = nil) {
        self.attachment = attachment; self.data = data
        self.playbackMIME = playbackMIME; self.playbackSHA = playbackSHA
    }
}

/// Original and playable bytes have distinct identities after local decoding.
/// This value contains metadata only and never disguises WAV as the Ogg source.
public struct MediaPreviewMetadata: Equatable, Sendable {
    public let sourceMIME: String
    public let sourceBytes: Int
    public let sourceSHA256: String
    public let playbackMIME: String
    public let playbackBytes: Int
    public let playbackSHA256: String
    public let isDerived: Bool
    public init(_ preview: MediaPreview) throws {
        let mime = preview.playbackMIME ?? preview.attachment.mime
        let digest = preview.playbackSHA ?? preview.attachment.sha256
        guard !preview.attachment.revoked, !preview.data.isEmpty, preview.data.count <= 16 * 1024 * 1024,
              ManagementValidation.hash(digest), MediaFilePolicy.digest(preview.data) == digest,
              (preview.playbackMIME == nil) == (preview.playbackSHA == nil) else { throw RPCFailure.invalidResponse }
        let derived = mime != preview.attachment.mime || digest != preview.attachment.sha256
        if derived {
            guard preview.attachment.kind == "audio", preview.attachment.mime == "audio/ogg", mime == "audio/wav",
                  digest != preview.attachment.sha256 else { throw RPCFailure.invalidResponse }
        } else { guard preview.data.count == preview.attachment.bytes else { throw RPCFailure.invalidResponse } }
        sourceMIME = preview.attachment.mime; sourceBytes = preview.attachment.bytes; sourceSHA256 = preview.attachment.sha256
        playbackMIME = mime; playbackBytes = preview.data.count; playbackSHA256 = digest; isDerived = derived
    }
}

@MainActor @Observable public final class MediaAttachmentsStore {
    public typealias Confirmation = @MainActor (String, String, String, () -> Bool) -> Bool
    public private(set) var attachments: [MediaAttachment] = []
    public private(set) var selectedIDs: [String] = []
    public private(set) var scope: SessionScope?
    public private(set) var busy = false
    public private(set) var errorMessage: String?
    public var preview: MediaPreview?
    @ObservationIgnored private var request: MaintenanceClient.Request?
    @ObservationIgnored private var transport: LocalRPCClient?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var contextTicket = 0
    @ObservationIgnored private var contextRevision: String?
    @ObservationIgnored private var contextReady = false
    @ObservationIgnored private var uploadID: String?
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private let isActive: () -> Bool
    @ObservationIgnored private let confirmReview: Confirmation?

    public init(isActive: @escaping () -> Bool, confirmReview: Confirmation? = nil) {
        self.isActive = isActive; self.confirmReview = confirmReview
    }
    init(request: @escaping MaintenanceClient.Request, isActive: @escaping () -> Bool,
         confirmReview: Confirmation? = nil) {
        self.request = request; self.isActive = isActive; self.confirmReview = confirmReview
    }
    public func configure(_ client: LocalRPCClient) {
        guard !busy else { return }
        transport = client; request = { try await client.call($0, params: $1) }
    }
    public func setScope(_ value: SessionScope?) async {
        contextTicket += 1; contextRevision = nil; contextReady = false
        await replaceScope(value)
    }
    /// Session identity alone does not bind workspace or agent configuration.
    /// Retire the old RAM preview and wait for its owner before admitting a new
    /// revision, even when it selects the same conversation again.
    public func setContext(scope value: SessionScope?, revision: String) async {
        if contextReady, contextRevision == revision, scope == value { await refresh(); return }
        contextTicket += 1
        let ticket = contextTicket
        contextRevision = revision; contextReady = false
        await replaceScope(nil)
        guard ticket == contextTicket else { return }
        await replaceScope(value)
        guard ticket == contextTicket else { return }
        contextReady = true
    }
    private func replaceScope(_ value: SessionScope?) async {
        if scope != value {
            generation += 1; task?.cancel()
            let epoch = generation
            preview = nil; selectedIDs = []; attachments = []
            if let id = uploadID, let request { _ = try? await request("media.upload.cancel", ["upload_id": .string(id)]) }
            await task?.value
            guard epoch == generation else { return }
            scope = value
        }
        await refresh()
    }
    public func refresh() async {
        guard let scope, let request, isActive(), !busy else { return }
        let epoch = generation
        do {
            let reply = try await request("media.list", scope.params)
            let rows = try ManagementValidation.rows(reply["attachments"], limit: 32, parse: MediaAttachment.init)
            guard rows.allSatisfy({ $0.scope == scope }), Set(rows.map(\.id)).count == rows.count,
                  reply["workspace_files_included_in_profile_backup"].bool == false,
                  reply["temporary_storage"] == .string("memory_only") else { throw RPCFailure.invalidResponse }
            guard epoch == generation, self.scope == scope, isActive() else { return }
            selectedIDs = selectedIDs.filter { id in
                rows.contains { row in row.id == id && !row.revoked && attachments.contains(row) }
            }
            if let preview, !rows.contains(where: { $0 == preview.attachment && !$0.revoked }) { self.preview = nil }
            attachments = rows
            errorMessage = nil
        } catch { if epoch == generation { errorMessage = error.localizedDescription } }
    }
    public func toggle(_ attachment: MediaAttachment) {
        guard !busy, !attachment.revoked, attachment.scope == scope else { return }
        if selectedIDs.contains(attachment.id) { selectedIDs.removeAll { $0 == attachment.id } }
        else if selectedIDs.count < 8 { selectedIDs.append(attachment.id) }
    }
    public func didSubmit(_ identities: [String], scope submitted: SessionScope) {
        guard scope == submitted else { return }
        selectedIDs.removeAll { identities.contains($0) }
    }
    public func importData(_ data: Data, name: String) async {
        await own { await self.performImport(data, name: name) }
    }
    private func own(_ operation: @escaping @MainActor () async -> Void) async {
        guard task == nil, !busy else { return }
        let worker = Task { @MainActor in await operation() }
        task = worker
        await withTaskCancellationHandler { await worker.value } onCancel: { worker.cancel() }
        task = nil
    }
    private func performImport(_ data: Data, name: String) async {
        guard let scope, let request, isActive(), !busy, MediaFilePolicy.safeName(name),
              !data.isEmpty, data.count <= 16 * 1024 * 1024 else { return }
        let epoch = generation, id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let hash = MediaFilePolicy.digest(data)
        let current = { self.isActive() && self.generation == epoch && self.scope == scope }
        guard let confirmReview, confirmReview("Adjuntar este archivo", "Importar este adjunto",
            "Archivo: \(name)\nTamaño: \(data.count) bytes\nSHA-256: \(hash)\n\nSe conservará en esta conversación \(scope.temporary ? "sólo en memoria" : "como archivo del espacio de trabajo"). No se incluye en la copia de perfil que excluye workspace. Su contenido sólo llegará a Codex después de aprobar su análisis. No se ejecutarán macros ni enlaces.", current), current(), !Task.isCancelled else { return }
        busy = true; uploadID = id
        defer { busy = false; uploadID = nil }
        do {
            var begin = scope.params; begin["upload_id"] = .string(id); begin["name"] = .string(name)
            begin["bytes"] = .number(Double(data.count)); begin["sha256"] = .string(hash)
            let receipt = try await request("media.upload.begin", begin)
            guard receipt["upload_id"] == .string(id), receipt["status"] == .string("receiving"),
                  receipt["received"].integer == 0 else { throw RPCFailure.invalidResponse }
            var offset = 0
            while offset < data.count {
                guard current(), !Task.isCancelled else { throw CancellationError() }
                let end = min(offset + 131072, data.count)
                let reply = try await request("media.upload.chunk", ["upload_id": .string(id),
                    "offset": .number(Double(offset)), "data_base64": .string(data.subdata(in: offset..<end).base64EncodedString())])
                guard reply["status"] == .string("receiving"), reply["received"].integer == end else { throw RPCFailure.invalidResponse }
                offset = end
            }
            guard current(), !Task.isCancelled else { throw CancellationError() }
            let reply = try await request("media.upload.commit", ["upload_id": .string(id)])
            let result = try MediaAttachment(reply["attachment"])
            guard reply["status"] == .string("complete"), result.id == id,
                  result.sha256 == hash, result.bytes == data.count, result.name == name,
                  !result.revoked, result.scope == scope else { throw RPCFailure.invalidResponse }
            guard current() else { return }
            attachments.append(result); if selectedIDs.count < 8 { selectedIDs.append(result.id) }
            errorMessage = nil
        } catch {
            // No effect is resent after a lost reply. A status read can identify
            // a published attachment; an unknown result remains explicit.
            let status = try? await request("media.upload.status", ["upload_id": .string(id)])
            if current(), status?["status"] == .string("complete"),
               let value = status?["attachment"], let result = try? MediaAttachment(value),
               result.scope == scope, result.sha256 == hash, result.id == id,
               result.bytes == data.count, result.name == name, !result.revoked {
                attachments.removeAll { $0.id == id }; attachments.append(result)
                if !selectedIDs.contains(id), selectedIDs.count < 8 { selectedIDs.append(id) }
                errorMessage = nil
            } else {
                _ = try? await request("media.upload.cancel", ["upload_id": .string(id)])
                if current() { errorMessage = "La importación no se confirmó. Actualiza los adjuntos antes de intentarlo de nuevo." }
            }
        }
    }
    public func openPreview(_ attachment: MediaAttachment) async {
        await own { await self.performPreview(attachment) }
    }
    private func performPreview(_ attachment: MediaAttachment) async {
        guard let scope, attachment.scope == scope, !attachment.revoked, let request, !busy else { return }
        let epoch = generation; busy = true; preview = nil; defer { busy = false }
        do {
            var data = Data(); var complete = false
            let playback = ["audio", "video"].contains(attachment.kind)
            var payloadLength = playback ? nil : Optional(attachment.bytes)
            var payloadSHA = playback ? nil : Optional(attachment.sha256)
            var payloadMIME = playback ? nil : Optional(attachment.mime)
            while !complete {
                try Task.checkCancellation()
                guard isActive(), epoch == generation, self.scope == scope else { throw RPCFailure.disconnected }
                var params = scope.params; params["attachment_id"] = .string(attachment.id)
                params["sha256"] = .string(attachment.sha256); params["offset"] = .number(Double(data.count))
                let reply = try await request(playback ? "media.playback.read" : "media.read", params)
                if playback {
                    guard let bytes = reply["byte_length"].integer, (1...16 * 1024 * 1024).contains(bytes),
                          let digest = reply["payload_sha256"].string, ManagementValidation.hash(digest),
                          let mime = reply["payload_mime"].string,
                          mime == attachment.mime || (attachment.mime == "audio/ogg" && mime == "audio/wav"),
                          payloadLength == nil || payloadLength == bytes,
                          payloadSHA == nil || payloadSHA == digest,
                          payloadMIME == nil || payloadMIME == mime else { throw RPCFailure.invalidResponse }
                    payloadLength = bytes; payloadSHA = digest; payloadMIME = mime
                }
                guard try MediaAttachment(reply["attachment"]) == attachment,
                      reply["offset"].integer == data.count,
                      let text = reply["data_base64"].string, text.utf8.count <= 174764,
                      let block = Data(base64Encoded: text), !block.isEmpty, block.count <= 131072,
                      let length = payloadLength, data.count + block.count <= length,
                      reply["next_offset"].integer == data.count + block.count,
                      let done = reply["complete"].bool,
                      done == (data.count + block.count == length) else { throw RPCFailure.invalidResponse }
                data.append(block); complete = done
            }
            guard data.count == payloadLength, MediaFilePolicy.digest(data) == payloadSHA,
                  epoch == generation, self.scope == scope, isActive() else { throw RPCFailure.invalidResponse }
            preview = MediaPreview(attachment: attachment, data: data,
                playbackMIME: playback ? payloadMIME : nil, playbackSHA: playback ? payloadSHA : nil); errorMessage = nil
        } catch { if epoch == generation { errorMessage = error.localizedDescription } }
    }
    public func revoke(_ attachment: MediaAttachment) async {
        await own { await self.performRevoke(attachment) }
    }
    private func performRevoke(_ attachment: MediaAttachment) async {
        guard let scope, attachment.scope == scope, let request, !busy, !attachment.revoked else { return }
        let epoch = generation
        let current = { self.isActive() && self.generation == epoch && self.scope == scope }
        guard let confirmReview, confirmReview("Retirar adjunto", "Retirar de esta conversación",
            "\(attachment.name)\nSHA-256: \(attachment.sha256)\n\nImpedirá nuevas lecturas. Conserva los bytes originales y no borra respuestas anteriores ni contenido ya enviado al proveedor.", current),
            current(), !Task.isCancelled else { return }
        busy = true
        do {
            var params = scope.params; params["attachment_id"] = .string(attachment.id); params["sha256"] = .string(attachment.sha256)
            let reply = try await request("media.revoke", params)
            let result = try MediaAttachment(reply["attachment"])
            guard result.revoked, result.id == attachment.id, result.scope == scope,
                  result.workspaceID == attachment.workspaceID, result.kind == attachment.kind,
                  result.review == attachment.review, current() else { throw RPCFailure.invalidResponse }
            attachments.removeAll { $0.id == result.id }; attachments.append(result)
            selectedIDs.removeAll { $0 == result.id }; preview = nil; errorMessage = nil
        } catch { if epoch == generation { errorMessage = error.localizedDescription } }
        busy = false
    }
    public func close() async {
        contextTicket += 1; contextRevision = nil; contextReady = false
        generation += 1; task?.cancel(); preview = nil; selectedIDs = []; attachments = []
        if let id = uploadID, let request { _ = try? await request("media.upload.cancel", ["upload_id": .string(id)]) }
        await task?.value
        await transport?.close(); request = nil; scope = nil
    }
}
