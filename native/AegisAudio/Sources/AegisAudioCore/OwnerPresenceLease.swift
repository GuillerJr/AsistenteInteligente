import Foundation

public struct OwnerPresenceLease: Sendable {
    public static let durationSeconds: TimeInterval = 30 * 60

    private var authorizedAt: TimeInterval?

    public init(authorizedAt: TimeInterval? = nil) {
        if let authorizedAt, authorizedAt.isFinite, authorizedAt >= 0 {
            self.authorizedAt = authorizedAt
        }
    }

    @discardableResult
    public mutating func authorize(at uptime: TimeInterval) -> Bool {
        guard uptime.isFinite, uptime >= 0 else {
            authorizedAt = nil
            return false
        }
        authorizedAt = uptime
        return true
    }

    public mutating func revoke() {
        authorizedAt = nil
    }

    public func isAuthorized(at uptime: TimeInterval) -> Bool {
        guard
            uptime.isFinite,
            let authorizedAt,
            uptime >= authorizedAt,
            uptime - authorizedAt < Self.durationSeconds
        else {
            return false
        }
        return true
    }
}
