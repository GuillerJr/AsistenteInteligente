import CryptoKit
import Foundation

public enum NativeMediaIssue: String, Error, LocalizedError, Sendable {
    case invalidArguments = "media_native_arguments_invalid"
    case authorityUnavailable = "media_native_authority_unavailable"
    case sourceChanged = "media_native_source_changed"
    case unsupported = "media_native_format_unsupported"
    case limit = "media_native_limit"
    case encryptedPDF = "media_native_pdf_encrypted"
    case decoderFailed = "media_native_decode_failed"
    case localeUnavailable = "media_native_locale_unavailable"
    case transcriberUnavailable = "media_native_transcriber_unavailable"
    case busy = "media_native_busy"
    case alreadySubmitted = "media_native_already_submitted"
    case cancelled = "media_native_cancelled"
    case deadline = "media_native_deadline"

    public var code: String { rawValue }
    public var errorDescription: String? {
        switch self {
        case .invalidArguments: "La operación de medios no coincide con la revisión autorizada."
        case .authorityUnavailable: "Falta la autoridad del host para observar este adjunto."
        case .sourceChanged: "Los bytes del adjunto cambiaron; revisa de nuevo la operación."
        case .unsupported: "El analizador local no admite ese formato de medio."
        case .limit: "El documento o medio supera los límites locales admitidos."
        case .encryptedPDF: "El PDF está cifrado; no se intentó abrirlo con contraseñas."
        case .decoderFailed: "El analizador local no pudo confirmar el contenido del adjunto."
        case .localeUnavailable: "El idioma solicitado no está disponible en el reconocimiento local."
        case .transcriberUnavailable: "No está conectada la transcripción local de archivos."
        case .busy: "Otra observación de medios conserva el worker hasta terminar su cierre."
        case .alreadySubmitted: "Esa operación ya se presentó y no se repetirá automáticamente."
        case .cancelled: "La observación se canceló y el worker terminó su cierre."
        case .deadline: "La observación alcanzó su plazo; se conservó la propiedad hasta cerrar."
        }
    }
}

struct NativeMediaRequest: Sendable {
    let operationID: String
    let attachmentID: String
    let attachmentSHA256: String
    let sourceSHA256: String
    let byteLength: Int
    let name: String
    let mime: String
    let kind: String
    let action: String
    let pageStart: Int
    let pageCount: Int
    let locale: String

    init(_ arguments: [String: RPCValue]) throws {
        let keys: Set<String> = ["operation_id", "attachment_id", "attachment_sha256", "source_sha256",
                                 "payload_sha256", "byte_length", "name", "mime", "kind", "action",
                                 "page_start", "page_count", "locale"]
        guard Set(arguments.keys) == keys,
              let operation = arguments["operation_id"]?.string, ManagementValidation.identifier(operation),
              let attachment = arguments["attachment_id"]?.string, ManagementValidation.identifier(attachment),
              let attachmentDigest = arguments["attachment_sha256"]?.string, ManagementValidation.hash(attachmentDigest),
              let sourceDigest = arguments["source_sha256"]?.string, ManagementValidation.hash(sourceDigest),
              arguments["payload_sha256"]?.string == sourceDigest,
              let length = arguments["byte_length"]?.integer, (1...16 * 1024 * 1024).contains(length),
              let name = arguments["name"]?.string, !name.isEmpty, name.utf8.count <= 255,
              name != ".", name != "..", !name.contains("/"), !name.contains("\\"),
              !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              let mime = arguments["mime"]?.string,
              let kind = arguments["kind"]?.string,
              let action = arguments["action"]?.string, ["extract", "transcribe"].contains(action),
              action != "transcribe" || ["audio", "video"].contains(kind),
              let start = arguments["page_start"]?.integer, (1...64).contains(start),
              let count = arguments["page_count"]?.integer, (1...8).contains(count), start + count - 1 <= 64,
              let requestedLocale = arguments["locale"]?.string,
              let locale = LocalSpeechPolicy.canonicalLocale(requestedLocale)
        else { throw NativeMediaIssue.invalidArguments }
        let accepted: [String: Set<String>] = [
            "pdf": ["application/pdf"], "document": ["application/rtf"],
            "image": ["image/png", "image/jpeg", "image/gif", "image/webp", "image/tiff", "image/heic", "image/avif"],
            "audio": ["audio/wav", "audio/mpeg", "audio/mp4", "audio/flac"],
            "video": ["video/mp4", "video/quicktime", "video/webm"],
        ]
        guard accepted[kind]?.contains(mime) == true else { throw NativeMediaIssue.unsupported }
        operationID = operation; attachmentID = attachment; attachmentSHA256 = attachmentDigest
        sourceSHA256 = sourceDigest; byteLength = length; self.name = name; self.mime = mime
        self.kind = kind; self.action = action; pageStart = start; pageCount = count; self.locale = locale
    }

    func validate(_ data: Data) throws {
        guard data.count == byteLength, data.count <= 16 * 1024 * 1024 else { throw NativeMediaIssue.sourceChanged }
        let actual = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard actual == sourceSHA256 else { throw NativeMediaIssue.sourceChanged }
    }
}

/// Cancellation owns each live framework resource until its worker returns.
/// Signalling this latch is not a cleanup receipt; await the enclosing task too.
final class NativeMediaCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var reason: NativeMediaIssue?
    private var handlers: [UUID: () -> Void] = [:]
    private let expiresAt: TimeInterval

    init(seconds: TimeInterval) { expiresAt = ProcessInfo.processInfo.systemUptime + seconds }

    func register(_ close: @escaping () -> Void) -> UUID {
        let id = UUID()
        lock.lock(); let cancelled = reason != nil
        if !cancelled { handlers[id] = close }
        lock.unlock()
        if cancelled { close() }
        return id
    }
    func remove(_ id: UUID) { lock.lock(); handlers.removeValue(forKey: id); lock.unlock() }
    func cancel(_ issue: NativeMediaIssue = .cancelled) {
        lock.lock()
        if reason == nil { reason = issue }
        let closing = Array(handlers.values)
        lock.unlock()
        for close in closing { close() }
    }
    func check() throws {
        if ProcessInfo.processInfo.systemUptime >= expiresAt { cancel(.deadline) }
        lock.lock(); let issue = reason; lock.unlock()
        if let issue { throw issue }
        if Task.isCancelled { throw NativeMediaIssue.cancelled }
    }
}

struct NativeMediaObservation: Sendable {
    var segments: [(locator: String, text: String)] = []
    var metadata: [String: RPCValue] = [:]
    var truncated = false
    var frame: RPCValue?
    private var textBytes = 0

    mutating func clearSegments() { segments.removeAll(); textBytes = 0 }

    mutating func add(_ locator: String, _ value: String) throws {
        guard segments.count < 64, locator.utf8.count <= 64 else { throw NativeMediaIssue.limit }
        var text = value.replacingOccurrences(of: "\0", with: "")
        let available = max(0, 48 * 1024 - textBytes)
        if text.utf8.count > available {
            var bytes = Data(text.utf8.prefix(available))
            while !bytes.isEmpty && String(data: bytes, encoding: .utf8) == nil { bytes.removeLast() }
            text = String(data: bytes, encoding: .utf8) ?? ""; truncated = true
        }
        textBytes += text.utf8.count; segments.append((locator, text))
    }

    func value(_ request: NativeMediaRequest) throws -> RPCValue {
        var result: [String: RPCValue] = [
            "schema": .string("jarvis.media.v1"), "succeeded": .bool(true), "verified": .bool(true),
            "effect_status": .string("known"), "source_sha256": .string(request.sourceSHA256),
            "kind": .string(request.kind), "segments": .array(segments.map {
                .object(["locator": .string($0.locator), "text": .string($0.text)])
            }), "metadata": .object(metadata), "truncated": .bool(truncated), "on_device": .bool(true),
        ]
        if let frame { result["frame"] = frame }
        if try JSONEncoder().encode(RPCValue.object(result)).count > 131072 {
            // A RAM-only preview is optional. Keep the cited text within the
            // signed broker envelope rather than losing its cleanup receipt.
            result.removeValue(forKey: "frame")
        }
        guard try JSONEncoder().encode(RPCValue.object(result)).count <= 131072 else {
            throw NativeMediaIssue.limit
        }
        return .object(result)
    }
}

/// Only already-reviewed, signed host operations can supply encoded media bytes.
/// Heavy decoding stays off MainActor; cancellation joins that same task before ACK.
@MainActor
public final class NativeMediaExecutor {
    public var beforeEffect: (@MainActor (String) async throws -> Void)?
    public var loadPayload: (@MainActor (String) async throws -> Data)?
    public var transcribe: (@MainActor (Data, String, String, String, String) async throws -> LocalAudioTranscript)?
    private var submitted: Set<String> = []
    private var activeOperation: String?
    private var activeTask: Task<RPCValue, Error>?
    private var cancellation: NativeMediaCancellation?
    private var epoch = 0
    private var processingSeconds: TimeInterval = 180

    public init() {}
    init(processingSeconds: TimeInterval) throws {
        guard processingSeconds.isFinite, processingSeconds > 0, processingSeconds <= 180 else {
            throw NativeMediaIssue.invalidArguments
        }
        self.processingSeconds = processingSeconds
    }

    public func execute(_ command: String, arguments: [String: RPCValue]) async throws -> RPCValue {
        guard command == "media.inspect" else { throw NativeMediaIssue.invalidArguments }
        let request = try NativeMediaRequest(arguments)
        guard activeTask == nil else { throw NativeMediaIssue.busy }
        guard !submitted.contains(request.operationID), submitted.count < 512 else {
            throw NativeMediaIssue.alreadySubmitted
        }
        guard beforeEffect != nil, loadPayload != nil else { throw NativeMediaIssue.authorityUnavailable }
        submitted.insert(request.operationID); activeOperation = request.operationID
        let version = epoch, flag = NativeMediaCancellation(seconds: processingSeconds)
        cancellation = flag
        let task = Task { @MainActor [weak self] in
            guard let self else { throw NativeMediaIssue.cancelled }
            return try await self.perform(request, flag: flag, version: version)
        }
        activeTask = task
        let timeout = Task { @MainActor [processingSeconds] in
            do { try await Task.sleep(for: .seconds(processingSeconds)) }
            catch { return }
            flag.cancel(.deadline); task.cancel()
        }
        defer {
            timeout.cancel()
            if activeOperation == request.operationID {
                activeTask = nil; activeOperation = nil; cancellation = nil
            }
        }
        return try await withTaskCancellationHandler {
            let result = try await task.value
            try flag.check()
            guard version == epoch else { throw NativeMediaIssue.cancelled }
            return result
        } onCancel: { flag.cancel(); task.cancel() }
    }

    public func cancel(operationID: String) async {
        guard activeOperation == operationID, let task = activeTask else { return }
        cancellation?.cancel(); task.cancel()
        _ = await task.result
    }

    public func reset() async {
        epoch += 1
        if let task = activeTask { cancellation?.cancel(); task.cancel(); _ = await task.result }
        // Consumed IDs remain consumed after reset; no uncertain operation is replayed.
    }

    private func checked(_ request: NativeMediaRequest, flag: NativeMediaCancellation, version: Int) async throws {
        try flag.check()
        guard version == epoch, activeOperation == request.operationID, let beforeEffect else {
            throw NativeMediaIssue.cancelled
        }
        try await beforeEffect(request.operationID)
        try flag.check()
        guard version == epoch, activeOperation == request.operationID else { throw NativeMediaIssue.cancelled }
    }

    private func perform(_ request: NativeMediaRequest, flag: NativeMediaCancellation, version: Int) async throws -> RPCValue {
        try await checked(request, flag: flag, version: version)
        guard let loadPayload else { throw NativeMediaIssue.authorityUnavailable }
        let payload = try await loadPayload(request.operationID)
        try await checked(request, flag: flag, version: version)
        try request.validate(payload)
        let worker = Task.detached(priority: .userInitiated) {
            try await NativeMediaDecoder.inspect(request, data: payload, cancellation: flag)
        }
        var observation = try await withTaskCancellationHandler {
            try await worker.value
        } onCancel: { flag.cancel(); worker.cancel() }
        try await checked(request, flag: flag, version: version)
        if request.action == "transcribe" {
            guard let transcribe else { throw NativeMediaIssue.transcriberUnavailable }
            let transcript = try await transcribe(payload, request.name, request.mime,
                                                  request.sourceSHA256, request.locale)
            try await checked(request, flag: flag, version: version)
            guard transcript.onDevice, !transcript.audioSentToProvider,
                  transcript.sourceSHA256 == request.sourceSHA256,
                  LocalSpeechPolicy.canonicalLocale(transcript.localeIdentifier) == request.locale else {
                throw NativeMediaIssue.sourceChanged
            }
            observation.clearSegments()
            try observation.add("time:0-" + String(format: "%.3f", locale: Locale(identifier: "en_US_POSIX"), transcript.durationSeconds), transcript.text)
            observation.metadata["duration_seconds"] = .number(transcript.durationSeconds)
            observation.metadata["locale"] = .string(transcript.localeIdentifier)
            observation.metadata["transcript_source_sha256"] = .string(transcript.sourceSHA256)
        }
        try await checked(request, flag: flag, version: version)
        return try observation.value(request)
    }
}
