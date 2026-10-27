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
    public let dependencies: String?
    public let state: String
    public let codex: String
    public let providerState: String?
    public let expectedRestore: ExpectedRestoreBootstrap?
    public let recoveryOnly: Bool
    public let automationTestOrigin: String?

    public init(python: String, source: String, dependencies: String? = nil, state: String,
                codex: String, providerState: String? = nil,
                expectedRestore: ExpectedRestoreBootstrap? = nil, recoveryOnly: Bool = false,
                automationTestOrigin: String? = nil) {
        self.python = python; self.source = source; self.dependencies = dependencies
        self.state = state; self.codex = codex; self.providerState = providerState
        self.expectedRestore = expectedRestore; self.recoveryOnly = recoveryOnly
        self.automationTestOrigin = automationTestOrigin
    }

    public static func parse(_ arguments: [String]) throws -> Self {
        let paths = ["--python", "--source", "--dependencies", "--state", "--codex", "--provider-state"]
        var flags: [String: String] = [:]
        var index = 0
        var recoveryOnly = false
        while index < arguments.count {
            let flag = arguments[index]
            if flag == "--recovery-only" {
                guard !recoveryOnly else { throw RPCFailure.configuration }
                recoveryOnly = true; index += 1; continue
            }
            guard (paths + ExpectedRestoreBootstrap.flags + ["--automation-test-origin"]).contains(flag), index + 1 < arguments.count, flags[flag] == nil else { throw RPCFailure.configuration }
            let value = arguments[index + 1]
            guard !value.contains("\0"), !paths.contains(flag) || MaintenanceValidation.path(value) else { throw RPCFailure.configuration }
            flags[flag] = value; index += 2
        }
        guard let python = flags["--python"], let source = flags["--source"], let state = flags["--state"], let codex = flags["--codex"] else { throw RPCFailure.configuration }
        let expectedCount = ExpectedRestoreBootstrap.flags.filter { flags[$0] != nil }.count
        guard expectedCount == 0 || expectedCount == 5, expectedCount == 0 || flags["--provider-state"] == nil else { throw RPCFailure.configuration }
        guard !recoveryOnly || expectedCount == 0 else { throw RPCFailure.configuration }
        if let origin = flags["--automation-test-origin"] {
            guard state.hasPrefix("/private/tmp/"), expectedCount == 0, !recoveryOnly,
                  let url = URLComponents(string: origin), url.scheme == "http",
                  url.host == "127.0.0.1", let port = url.port, (1024...65535).contains(port),
                  url.path.isEmpty, url.query == nil, url.fragment == nil,
                  url.user == nil, url.password == nil,
                  origin == "http://127.0.0.1:\(port)" else { throw RPCFailure.configuration }
        }
        return Self(python: python, source: source, dependencies: flags["--dependencies"], state: state, codex: codex, providerState: flags["--provider-state"], expectedRestore: expectedCount == 0 ? nil : try ExpectedRestoreBootstrap(flags: flags), recoveryOnly: recoveryOnly, automationTestOrigin: flags["--automation-test-origin"])
    }
    public func openingRestored(_ target: ProfileOpenTarget) -> Self {
        Self(python: python, source: source, dependencies: dependencies, state: target.path, codex: codex, providerState: nil, expectedRestore: ExpectedRestoreBootstrap(target: target), recoveryOnly: false)
    }
    public var serviceArguments: [String] {
        var args = ["-m", "jarvis_core.service", "--state", state, "--codex", codex]
        if let providerState { args += ["--provider-state", providerState] }
        if let expectedRestore { args += expectedRestore.arguments }
        return args
    }
}
