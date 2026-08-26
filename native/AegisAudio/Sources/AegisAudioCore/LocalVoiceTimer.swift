import Foundation

public enum LocalVoiceTimerCommand: Equatable, Sendable {
    case start(durationSeconds: Int)
    case cancel
    case status

    private static let startPattern = try? NSRegularExpression(
        pattern: #"^(?:(?:pon|configura|inicia|set|start)(?: un| a)? )?(?:temporizador|timer) (?:de|por|for) ([0-9]{1,5}) (segundos?|seconds?|minutos?|minutes?|horas?|hours?)$"#
    )
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

    public static func parse(_ transcript: String) -> Self? {
        var normalized = transcript
            .folding(
                options: [.caseInsensitive, .diacriticInsensitive],
                locale: Locale(identifier: "es")
            )
            .lowercased()
            .replacingOccurrences(of: ",", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        if normalized.hasPrefix("jarvis ") {
            normalized.removeFirst("jarvis ".count)
        }
        if cancelCommands.contains(normalized) {
            return .cancel
        }
        if statusCommands.contains(normalized) {
            return .status
        }
        guard
            let startPattern,
            let match = startPattern.firstMatch(
                in: normalized,
                range: NSRange(normalized.startIndex..., in: normalized)
            ),
            let amountRange = Range(match.range(at: 1), in: normalized),
            let unitRange = Range(match.range(at: 2), in: normalized),
            let amount = Int(normalized[amountRange]),
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
}

@MainActor
public final class LocalVoiceTimerScheduler {
    private var timerTask: Task<Void, Never>?
    private var deadline: ContinuousClock.Instant?

    public var isActive: Bool { timerTask != nil }
    public var remainingSeconds: Int? {
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
        guard (1 ... 86_400).contains(durationSeconds), timerTask == nil else { return false }
        deadline = ContinuousClock.now.advanced(by: .seconds(durationSeconds))
        timerTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(durationSeconds))
            } catch {
                return
            }
            guard let self, !Task.isCancelled else { return }
            timerTask = nil
            deadline = nil
            completion()
        }
        return true
    }

    @discardableResult
    public func cancel() -> Bool {
        guard let timerTask else { return false }
        timerTask.cancel()
        self.timerTask = nil
        deadline = nil
        return true
    }

    deinit {
        timerTask?.cancel()
    }
}
