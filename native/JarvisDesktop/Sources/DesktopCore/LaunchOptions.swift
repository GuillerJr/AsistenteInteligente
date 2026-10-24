import Foundation

public struct LaunchOptions: Sendable, Equatable {
    public let python: String
    public let source: String
    public let state: String
    public let codex: String

    public static func parse(_ arguments: [String]) throws -> Self {
        var flags: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let flag = arguments[index]
            guard ["--python", "--source", "--state", "--codex"].contains(flag),
                  index + 1 < arguments.count, flags[flag] == nil else {
                throw RPCFailure.configuration
            }
            let value = arguments[index + 1]
            guard value.hasPrefix("/"), !value.contains("\0") else { throw RPCFailure.configuration }
            flags[flag] = value
            index += 2
        }
        guard let python = flags["--python"], let source = flags["--source"],
              let state = flags["--state"], let codex = flags["--codex"] else {
            throw RPCFailure.configuration
        }
        return Self(python: python, source: source, state: state, codex: codex)
    }
}
