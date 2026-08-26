import Foundation

public enum LocalVoiceTimerCommand: Equatable, Sendable {
    case start(durationSeconds: Int)
    case cancel

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

    public var isActive: Bool { timerTask != nil }

    public init() {}

    @discardableResult
    public func start(
        durationSeconds: Int,
        completion: @escaping @MainActor @Sendable () -> Void
    ) -> Bool {
        guard (1 ... 86_400).contains(durationSeconds), timerTask == nil else { return false }
        timerTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .seconds(durationSeconds))
            } catch {
                return
            }
            guard let self, !Task.isCancelled else { return }
            timerTask = nil
            completion()
        }
        return true
    }

    @discardableResult
    public func cancel() -> Bool {
        guard let timerTask else { return false }
        timerTask.cancel()
        self.timerTask = nil
        return true
    }

    deinit {
        timerTask?.cancel()
    }
}
