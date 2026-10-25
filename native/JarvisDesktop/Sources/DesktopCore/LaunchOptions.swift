import Foundation

public struct ExpectedRestoreBootstrap: Sendable, Equatable {
    public let identity: MaintenanceFileIdentity
    public let markerHash: String
    public let admissionHash: String
    public let operationID: String
    public init(target: ProfileOpenTarget) {
        identity = target.identity; markerHash = target.markerHash; admissionHash = target.admissionHash; operationID = target.operationID
    }
    init(flags: [String: String]) throws {
        identity = try MaintenanceFileIdentity(.object(["device": .string(flags[Self.flags[0]]!), "inode": .string(flags[Self.flags[1]]!)]))
        guard let marker = flags[Self.flags[2]], ManagementValidation.hash(marker), let admission = flags[Self.flags[3]], ManagementValidation.hash(admission), let operation = flags[Self.flags[4]], ManagementValidation.identifier(operation) else { throw RPCFailure.configuration }
        markerHash = marker; admissionHash = admission; operationID = operation
    }
    static let flags = ["--expected-restore-device", "--expected-restore-inode", "--expected-restore-marker-sha256", "--expected-restore-admission-sha256", "--expected-restore-operation-id"]
    public var arguments: [String] {
        let values = [identity.device, identity.inode, markerHash, admissionHash, operationID]
        return zip(Self.flags, values).flatMap { [$0.0, $0.1] }
    }
}

public struct LaunchOptions: Sendable, Equatable {
    public let python: String
    public let source: String
    public let state: String
    public let codex: String
    public let providerState: String?
    public let expectedRestore: ExpectedRestoreBootstrap?

    public static func parse(_ arguments: [String]) throws -> Self {
        let paths = ["--python", "--source", "--state", "--codex", "--provider-state"]
        var flags: [String: String] = [:]
        var index = 0
        while index < arguments.count {
            let flag = arguments[index]
            guard (paths + ExpectedRestoreBootstrap.flags).contains(flag), index + 1 < arguments.count, flags[flag] == nil else { throw RPCFailure.configuration }
            let value = arguments[index + 1]
            guard !value.contains("\0"), !paths.contains(flag) || MaintenanceValidation.path(value) else { throw RPCFailure.configuration }
            flags[flag] = value; index += 2
        }
        guard let python = flags["--python"], let source = flags["--source"], let state = flags["--state"], let codex = flags["--codex"] else { throw RPCFailure.configuration }
        let expectedCount = ExpectedRestoreBootstrap.flags.filter { flags[$0] != nil }.count
        guard expectedCount == 0 || expectedCount == 5, expectedCount == 0 || flags["--provider-state"] == nil else { throw RPCFailure.configuration }
        return Self(python: python, source: source, state: state, codex: codex, providerState: flags["--provider-state"], expectedRestore: expectedCount == 0 ? nil : try ExpectedRestoreBootstrap(flags: flags))
    }
    public func openingRestored(_ target: ProfileOpenTarget) -> Self {
        Self(python: python, source: source, state: target.path, codex: codex, providerState: nil, expectedRestore: ExpectedRestoreBootstrap(target: target))
    }
    public var serviceArguments: [String] {
        var args = ["-m", "jarvis_core.service", "--state", state, "--codex", codex]
        if let providerState { args += ["--provider-state", providerState] }
        if let expectedRestore { args += expectedRestore.arguments }
        return args
    }
}
