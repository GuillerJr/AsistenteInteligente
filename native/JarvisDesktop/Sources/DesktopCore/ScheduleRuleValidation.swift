import Foundation

/// Wire validation shared by the editor and the response decoder. Scheduling
/// remains authoritative in the service; this never computes or executes a slot.
public enum ScheduleRuleValidation {
    public static func valid(_ value: RPCValue) -> Bool {
        guard let fields = value.object,
              Set(fields.keys).isSubset(of: ["kind", "timezone", "at", "interval_seconds", "local_time", "cron_expression", "active_start", "active_end"]),
              let kind = value["kind"].string,
              let zone = value["timezone"].string, zone.utf8.count <= 128,
              TimeZone(identifier: zone) != nil else { return false }
        func absent(_ keys: [String]) -> Bool { keys.allSatisfy { value[$0] == .null } }
        let validAt = value["at"] == .null || ManagementValidation.timestamp(value["at"]) != nil
        switch kind {
        case "once":
            return ManagementValidation.timestamp(value["at"]) != nil && absent(["interval_seconds", "local_time", "cron_expression", "active_start", "active_end"])
        case "interval", "heartbeat":
            guard let interval = value["interval_seconds"].integer,
                  (1...31_536_000).contains(interval), validAt,
                  absent(["local_time", "cron_expression"]) else { return false }
            if kind == "interval" { return absent(["active_start", "active_end"]) }
            if absent(["active_start", "active_end"]) { return true }
            guard let start = value["active_start"].string, let end = value["active_end"].string else { return false }
            return wallTime(start) && wallTime(end) && start != end
        case "daily":
            return value["local_time"].string.map(wallTime) == true && absent(["at", "interval_seconds", "cron_expression", "active_start", "active_end"])
        case "cron":
            return value["cron_expression"].string.map(cron) == true && absent(["at", "interval_seconds", "local_time", "active_start", "active_end"])
        case "event":
            return absent(["at", "interval_seconds", "local_time", "cron_expression", "active_start", "active_end"])
        default: return false
        }
    }

    public static func wallTime(_ value: String) -> Bool {
        value.range(of: "^([01][0-9]|2[0-3]):[0-5][0-9]$", options: .regularExpression) != nil
    }

    public static func cron(_ expression: String) -> Bool {
        guard expression.utf8.count <= 256 else { return false }
        let fields = expression.split(whereSeparator: { $0 == " " || $0 == "\t" })
        let ranges = [0...59, 0...23, 1...31, 1...12, 0...7]
        guard fields.count == ranges.count else { return false }
        return zip(fields, ranges).enumerated().allSatisfy { fieldIndex, pair in
            let (field, range) = pair
            func component(_ value: Substring) -> Int? {
                if let value = integer(value) { return value }
                let names = fieldIndex == 3 ? ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
                    : fieldIndex == 4 ? ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"] : []
                guard let index = names.firstIndex(of: String(value).uppercased()) else { return nil }
                return index + (fieldIndex == 3 ? 1 : 0)
            }
            let items = field.split(separator: ",", omittingEmptySubsequences: false)
            guard !items.isEmpty, items.count <= 64 else { return false }
            return items.allSatisfy { item in
                let parts = item.split(separator: "/", omittingEmptySubsequences: false)
                guard (1...2).contains(parts.count) else { return false }
                if parts.count == 2 {
                    guard let step = integer(parts[1]), step > 0, step <= range.upperBound - range.lowerBound + 1 else { return false }
                }
                if parts[0] == "*" { return true }
                let endpoints = parts[0].split(separator: "-", omittingEmptySubsequences: false)
                guard (1...2).contains(endpoints.count), let first = component(endpoints[0]), range.contains(first) else { return false }
                if endpoints.count == 1 { return true }
                guard let last = component(endpoints[1]), range.contains(last), first <= last else { return false }
                return true
            }
        }
    }

    private static func integer(_ value: Substring) -> Int? {
        guard !value.isEmpty, value.utf8.allSatisfy({ (48...57).contains($0) }) else { return nil }
        return Int(value)
    }
}
