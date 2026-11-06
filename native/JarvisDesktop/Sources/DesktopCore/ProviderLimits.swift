import Foundation

/// One usage-limit window of the signed-in account. Every field may be unknown:
/// unknown stays nil and is never shown as zero.
public struct ProviderLimitWindow: Equatable, Sendable {
    public let usedPercent: Double?
    public let resetsAt: Date?
    public let windowMinutes: Int?

    init?(_ value: RPCValue) {
        if case .null = value { return nil }
        guard value.object != nil else { return nil }
        usedPercent = Self.number(value["used_percent"], 0...1000)
        resetsAt = value["resets_at"].integer.map { Date(timeIntervalSince1970: TimeInterval($0)) }
        windowMinutes = value["window_minutes"].integer
    }

    static func number(_ value: RPCValue, _ range: ClosedRange<Double>) -> Double? {
        if case .number(let number) = value, number.isFinite, range.contains(number) { return number }
        return nil
    }
}

/// The official account's limits for one limit id (A-23). Information only.
public struct ProviderLimit: Equatable, Identifiable, Sendable {
    public let id: String
    public let name: String?
    public let plan: String?
    public let reached: String?
    public let primary: ProviderLimitWindow?
    public let secondary: ProviderLimitWindow?
    public let creditBalance: Double?
    public let unlimitedCredits: Bool?

    public init?(_ value: RPCValue) {
        guard let id = value["limit_id"].string, !id.isEmpty, id.count <= 80,
              value["windows"].object != nil else { return nil }
        for key in ["name", "plan", "reached"] {
            switch value[key] {
            case .null: continue
            case .string(let text) where !text.isEmpty && text.count <= 80: continue
            default: return nil
            }
        }
        self.id = id
        name = value["name"].string
        plan = value["plan"].string
        reached = value["reached"].string
        primary = ProviderLimitWindow(value["windows"]["primary"])
        secondary = ProviderLimitWindow(value["windows"]["secondary"])
        creditBalance = ProviderLimitWindow.number(value["credits"]["balance"], 0...1e15)
        unlimitedCredits = value["credits"]["unlimited"].bool
    }

    /// Rejects the whole `provider.limits` reply when any row is malformed or duplicated.
    public static func snapshot(_ result: RPCValue) -> [ProviderLimit]? {
        guard let connected = result["connected"].bool, let rows = result["limits"].array,
              rows.count <= 8 else { return nil }
        let limits = rows.compactMap(ProviderLimit.init)
        guard limits.count == rows.count, Set(limits.map(\.id)).count == limits.count,
              connected || limits.isEmpty else { return nil }
        return limits
    }

    /// Spanish description of one window; unknown usage says so instead of 0 %.
    public static func describe(_ window: ProviderLimitWindow, now: Date = Date()) -> String {
        var parts: [String] = []
        if let minutes = window.windowMinutes {
            parts.append(minutes % 1440 == 0 ? "Ventana de \(minutes / 1440) d" :
                         minutes % 60 == 0 ? "Ventana de \(minutes / 60) h" : "Ventana de \(minutes) min")
        }
        parts.append(window.usedPercent.map { "uso \(Int($0.rounded())) %" } ?? "uso desconocido")
        if let reset = window.resetsAt, reset > now {
            parts.append("se renueva \(reset.formatted(date: .abbreviated, time: .shortened))")
        }
        return parts.joined(separator: ", ")
    }
}
