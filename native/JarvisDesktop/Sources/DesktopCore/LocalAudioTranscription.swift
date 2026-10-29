import CryptoKit
import Foundation

public enum LocalFileSpeechIssue: Error, Equatable, Sendable {
    case invalidSource, sourceChanged, authorizationRequired, localeUnavailable
    case unsupportedPlatform, durationLimit, textLimit, deadline, recognitionFailed
    public var code: String {
        switch self {
        case .invalidSource: "local_audio_invalid_source"
        case .sourceChanged: "local_audio_source_changed"
        case .authorizationRequired: "local_audio_speech_permission_required"
        case .localeUnavailable: "local_audio_locale_unavailable"
        case .unsupportedPlatform: "local_audio_platform_unavailable"
        case .durationLimit: "local_audio_duration_limit"
        case .textLimit: "local_audio_text_limit"
        case .deadline: "local_audio_deadline"
        case .recognitionFailed: "local_audio_recognition_failed"
        }
    }
    public var message: String {
        switch self {
        case .invalidSource: "El archivo de audio no cumple el formato o límite admitido."
        case .sourceChanged: "Los bytes del audio no coinciden con la revisión autorizada."
        case .authorizationRequired: "Autoriza el reconocimiento de voz de Jarvis para transcribir este archivo en el Mac. No necesita el micrófono."
        case .localeUnavailable: "El modelo local de ese idioma no está instalado. No se descargó otro modelo ni se envió audio a un proveedor."
        case .unsupportedPlatform: "La transcripción de archivos en memoria necesita macOS 26 o posterior."
        case .durationLimit: "La transcripción local admite audio de hasta dos minutos."
        case .textLimit: "La transcripción supera el límite de texto; no se publicó un resultado truncado."
        case .deadline: "La transcripción local alcanzó su plazo y se canceló."
        case .recognitionFailed: "El reconocimiento local no pudo confirmar una transcripción del archivo."
        }
    }
}

public enum LocalAudioTranscriptionPolicy {
    public static let maximumSourceBytes = 16 * 1024 * 1024
    public static let maximumDuration: TimeInterval = 120
    public static let maximumTextBytes = 16000
    public static let maximumProcessingSeconds: TimeInterval = 180
    public static let supportedMIMETypes = Set(["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac",
                                               "video/mp4", "video/quicktime", "video/webm"])

    public static func validateSource(data: Data, name: String, mimeType: String, expectedSHA256: String) throws {
        guard !data.isEmpty, data.count <= maximumSourceBytes, supportedMIMETypes.contains(mimeType),
              !name.isEmpty, name.utf8.count <= 512, !name.contains("/"), !name.contains("\\"),
              !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              ManagementValidation.hash(expectedSHA256) else { throw LocalFileSpeechIssue.invalidSource }
        let actual = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard actual == expectedSHA256 else { throw LocalFileSpeechIssue.sourceChanged }
    }
    public static func validateDuration(_ value: TimeInterval) throws {
        guard value.isFinite, value > 0, value <= maximumDuration else { throw LocalFileSpeechIssue.durationLimit }
    }
    public static func validateText(_ value: String) throws {
        guard value.utf8.count <= maximumTextBytes else { throw LocalFileSpeechIssue.textLimit }
        guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, !value.contains("\0") else { throw LocalFileSpeechIssue.recognitionFailed }
    }
}

public struct LocalAudioTranscript: Equatable, Sendable {
    public let text: String
    public let localeIdentifier: String
    public let durationSeconds: TimeInterval
    public let sourceSHA256: String
    public let onDevice = true
    public let audioSentToProvider = false
    public init(text: String, localeIdentifier: String, durationSeconds: TimeInterval, sourceSHA256: String) throws {
        try LocalAudioTranscriptionPolicy.validateText(text)
        try LocalAudioTranscriptionPolicy.validateDuration(durationSeconds)
        guard LocalSpeechPolicy.canonicalLocale(localeIdentifier) != nil, ManagementValidation.hash(sourceSHA256) else {
            throw LocalFileSpeechIssue.invalidSource
        }
        self.text = text; self.localeIdentifier = localeIdentifier
        self.durationSeconds = durationSeconds; self.sourceSHA256 = sourceSHA256
    }
}
