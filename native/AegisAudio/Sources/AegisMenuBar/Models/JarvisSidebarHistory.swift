import AegisAudioCore
import Foundation

/// A presentation of the pages already loaded by the history store. It never
/// filters rows, changes their identities, or requests another page itself.
enum JarvisSidebarHistory {
    enum Period: String, CaseIterable, Identifiable {
        case today, yesterday, week, earlier, results
        var id: String { rawValue }
        var title: String {
            switch self {
            case .today: String(localized: "Hoy", bundle: .module)
            case .yesterday: String(localized: "Ayer", bundle: .module)
            case .week: String(localized: "Últimos 7 días", bundle: .module)
            case .earlier: String(localized: "Anteriores", bundle: .module)
            case .results: String(localized: "Resultados", bundle: .module)
            }
        }
    }

    struct Group: Identifiable {
        let period: Period
        let entries: [ChatConversationSummary]
        var id: Period { period }
    }

    static func groups(_ entries: [ChatConversationSummary], query: String,
                       archived: Bool, trash: Bool, now: Date = Date(),
                       calendar: Calendar = .current) -> [Group] {
        guard !entries.isEmpty else { return [] }
        // Search rank and the explicit archive/trash modes stay in server order.
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || archived || trash {
            return [Group(period: .results, entries: entries)]
        }
        let today = calendar.startOfDay(for: now)
        let yesterday = calendar.date(byAdding: .day, value: -1, to: today) ?? today
        let week = calendar.date(byAdding: .day, value: -6, to: today) ?? yesterday
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        var grouped: [Period: [ChatConversationSummary]] = [:]
        for entry in entries {
            let date = fractional.date(from: entry.updatedAt) ?? plain.date(from: entry.updatedAt)
            let period: Period
            if let date {
                if date >= today { period = .today }
                else if date >= yesterday { period = .yesterday }
                else if date >= week { period = .week }
                else { period = .earlier }
            } else {
                // A malformed timestamp must not make a conversation disappear.
                period = .earlier
            }
            grouped[period, default: []].append(entry)
        }
        return Period.allCases.compactMap { period in
            grouped[period].map { Group(period: period, entries: $0) }
        }
    }

    static func isSelected(_ entry: ChatConversationSummary, conversationID: UUID?,
                           showingConversation: Bool, trash: Bool) -> Bool {
        !trash && showingConversation && entry.id == conversationID
    }
}
