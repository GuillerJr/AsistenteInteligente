import Foundation

public enum LocalVoiceTimerCommand: Equatable, Sendable {
    case start(durationSeconds: Int)
    case cancel
    case pause
    case resume
    case status

    private static let startPattern = try? NSRegularExpression(
        pattern: #"^(?:(?:pon|configura|inicia|set|start)(?: un| a)? )?(?:temporizador|timer) (?:de|por|for) ([0-9]{1,5}|[a-z -]{1,32}) (segundos?|seconds?|minutos?|minutes?|horas?|hours?)$"#
    )
    private static let spokenAmounts: [String: Int] = {
        var values = ["a": 1, "un": 1, "una": 1]
        for localeIdentifier in ["es_ES", "en_US"] {
            let formatter = NumberFormatter()
            formatter.locale = Locale(identifier: localeIdentifier)
            formatter.numberStyle = .spellOut
            for amount in 1 ... 60 {
                guard let word = formatter.string(from: NSNumber(value: amount)) else {
                    continue
                }
                values[normalizedAmount(word)] = amount
            }
        }
        return values
    }()
    private static let cancelCommands: Set<String> = [
        "cancela el temporizador",
        "cancela temporizador",
        "cancel timer",
        "deten el temporizador",
        "deten temporizador",
        "stop timer",
    ]
    private static let statusCommands: Set<String> = [
        "cuanto falta del temporizador",
        "cuanto tiempo queda del temporizador",
        "estado del temporizador",
        "how much time is left on the timer",
        "timer status",
    ]
    private static let pauseCommands: Set<String> = [
        "pausa el temporizador",
        "pausa temporizador",
        "pause timer",
    ]
    private static let resumeCommands: Set<String> = [
        "continua el temporizador",
        "reanuda el temporizador",
        "reanuda temporizador",
        "resume timer",
    ]

    public static func parse(_ transcript: String) -> Self? {
        let normalized = LocalVoiceCommandText.normalize(transcript)
        if cancelCommands.contains(normalized) {
            return .cancel
        }
        if statusCommands.contains(normalized) {
            return .status
        }
        if pauseCommands.contains(normalized) {
            return .pause
        }
        if resumeCommands.contains(normalized) {
            return .resume
        }
        guard
            let startPattern,
            let match = startPattern.firstMatch(
                in: normalized,
                range: NSRange(normalized.startIndex..., in: normalized)
            ),
            let amountRange = Range(match.range(at: 1), in: normalized),
            let unitRange = Range(match.range(at: 2), in: normalized),
            let amount = parsedAmount(String(normalized[amountRange])),
            amount > 0
        else {
            return nil
        }
        let unit = normalized[unitRange]
        let multiplier = unit.hasPrefix("h") ? 3_600 : (unit.hasPrefix("m") ? 60 : 1)
        let seconds = amount * multiplier
        guard seconds <= 86_400 else { return nil }
        return .start(durationSeconds: seconds)
    }

    private static func parsedAmount(_ value: String) -> Int? {
        Int(value) ?? spokenAmounts[normalizedAmount(value)]
    }

    private static func normalizedAmount(_ value: String) -> String {
        value
            .folding(
                options: [.caseInsensitive, .diacriticInsensitive],
                locale: Locale(identifier: "es")
            )
            .lowercased()
            .replacingOccurrences(of: "-", with: " ")
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
    }
}

@MainActor
public final class LocalVoiceTimerScheduler {
    private var timerTask: Task<Void, Never>?
    private var deadline: ContinuousClock.Instant?
    private var pausedRemainingSeconds: Int?
    private var completion: (@MainActor @Sendable () -> Void)?

    public var isActive: Bool { timerTask != nil || pausedRemainingSeconds != nil }
    public var isPaused: Bool { pausedRemainingSeconds != nil }
    public var remainingSeconds: Int? {
        if let pausedRemainingSeconds {
            return pausedRemainingSeconds
        }
        guard timerTask != nil, let deadline else { return nil }
        let remaining = ContinuousClock.now.duration(to: deadline)
        guard remaining > .zero else { return 1 }
        let components = remaining.components
        let roundedSeconds = components.seconds + (components.attoseconds > 0 ? 1 : 0)
        return max(1, min(86_400, Int(roundedSeconds)))
    }

    public init() {}

    @discardableResult
    public func start(
        durationSeconds: Int,
        completion: @escaping @MainActor @Sendable () -> Void
    ) -> Bool {
        guard (1 ... 86_400).contains(durationSeconds), !isActive else { return false }
        self.completion = completion
        schedule(durationSeconds: durationSeconds)
        return true
    }

    public func pause() -> Int? {
        guard timerTask != nil, let remainingSeconds else { return nil }
        timerTask?.cancel()
        timerTask = nil
        deadline = nil
        pausedRemainingSeconds = remainingSeconds
        return remainingSeconds
    }

    public func resume() -> Int? {
        guard let pausedRemainingSeconds, completion != nil else { return nil }
        self.pausedRemainingSeconds = nil
        schedule(durationSeconds: pausedRemainingSeconds)
        return pausedRemainingSeconds
    }

    private func schedule(durationSeconds: Int) {
        deadline = ContinuousClock.now.advanced(by: .seconds(durationSeconds))
        timerTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(durationSeconds))
            } catch {
                return
            }
            guard let self, !Task.isCancelled else { return }
            let completion = completion
            timerTask = nil
            deadline = nil
            self.completion = nil
            completion?()
        }
    }

    @discardableResult
    public func cancel() -> Bool {
        guard isActive else { return false }
        timerTask?.cancel()
        timerTask = nil
        deadline = nil
        pausedRemainingSeconds = nil
        completion = nil
        return true
    }

    deinit {
        timerTask?.cancel()
    }
}
