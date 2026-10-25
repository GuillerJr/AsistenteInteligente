import Darwin
import Foundation
import XCTest

/// Test fixtures only. Foundation's cached isRunning/waitUntilExit must not
/// block XCTest teardown after a child was already reaped on another thread.
@MainActor final class FixtureProcess {
    enum StopResult: Equatable { case stopped, forced, unconfirmed }
    private enum ChildState: Equatable { case owned, gone, unknown }
    private let process: Process
    private var pid: pid_t?
    private var result: StopResult?
    private(set) var signalsSent = 0

    init(_ process: Process) { self.process = process }
    func run() throws {
        try process.run()
        let identifier = process.processIdentifier
        precondition(identifier > 1)
        pid = identifier
    }

    private func childState(_ pid: pid_t) -> ChildState {
        var status: Int32 = 0
        for _ in 0..<16 {
            let observed = waitpid(pid, &status, WNOHANG)
            if observed == pid { return .gone }
            if observed == 0 { return .owned }
            if errno == ECHILD { return .gone }
            if errno != EINTR { return .unknown }
        }
        return .unknown
    }

    private func waitForExit(_ pid: pid_t, seconds: TimeInterval) async -> Bool {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        while ProcessInfo.processInfo.systemUptime < deadline {
            switch childState(pid) {
            case .gone: return true
            case .unknown: return false
            case .owned: break
            }
            do { try await Task.sleep(for: .milliseconds(20)) }
            catch { await Task.yield() }
        }
        return childState(pid) == .gone
    }

    func stop(gracefulSeconds: TimeInterval = 2, forcedSeconds: TimeInterval = 1) async -> StopResult {
        precondition(gracefulSeconds.isFinite && (0.02...2).contains(gracefulSeconds))
        precondition(forcedSeconds.isFinite && (0.02...1).contains(forcedSeconds))
        if let result { return result }
        guard let pid else { result = .stopped; return .stopped }
        switch childState(pid) {
        case .gone: result = .stopped; return .stopped
        case .unknown: return .unconfirmed
        case .owned: break
        }
        // Never signal a PID merely because Foundation still calls it running:
        // waitpid must have established that it remains our direct child.
        guard kill(pid, SIGTERM) == 0 || errno == ESRCH else { return .unconfirmed }
        signalsSent += 1
        if await waitForExit(pid, seconds: gracefulSeconds) { result = .stopped; return .stopped }
        switch childState(pid) {
        case .gone: result = .stopped; return .stopped
        case .unknown: return .unconfirmed
        case .owned: break
        }
        guard kill(pid, SIGKILL) == 0 || errno == ESRCH else { return .unconfirmed }
        signalsSent += 1
        guard await waitForExit(pid, seconds: forcedSeconds) else { return .unconfirmed }
        result = .forced
        return .forced
    }
}

extension XCTestCase {
    @MainActor @discardableResult
    func startFixtureProcess(_ process: Process, directory: String,
                             file: StaticString = #filePath, line: UInt = #line) throws -> FixtureProcess {
        try validateFixtureDirectory(directory)
        let fixture = FixtureProcess(process)
        addTeardownBlock {
            let result = await fixture.stop()
            guard result == .stopped else {
                XCTFail("El cierre del fixture fue forzado o no se confirmó. Diagnóstico conservado en \(directory).", file: file, line: line)
                return
            }
            try validateFixtureDirectory(directory)
            try FileManager.default.removeItem(atPath: directory)
        }
        try fixture.run()
        return fixture
    }
}

private func validateFixtureDirectory(_ directory: String) throws {
    let prefix = "/private/tmp/"
    let name = directory.hasPrefix(prefix) ? String(directory.dropFirst(prefix.count)) : ""
    var info = stat()
    guard !name.contains("/"), name.hasPrefix("jarvis-") || name.hasPrefix("jvs-"),
          lstat(directory, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
          info.st_uid == getuid(), info.st_mode & 0o777 == 0o700 else {
        throw NSError(domain: "JarvisFixtureCleanup", code: 1)
    }
}
