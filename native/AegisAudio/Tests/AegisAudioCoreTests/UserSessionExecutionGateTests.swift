import Foundation
import Testing
@testable import AegisAudioCore

private final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    func increment() {
        lock.withLock { count += 1 }
    }

    var value: Int {
        lock.withLock { count }
    }
}

@Test func userSessionGateCancelsOneActiveExecutionAndFailsClosed() throws {
    let gate = UserSessionExecutionGate()
    let cancellations = LockedCounter()
    let permit = try #require(gate.permit)
    let startedLease = gate.begin(
        for: permit,
        cancellation: {
            cancellations.increment()
            #expect(!gate.isAvailable)
        },
        start: {}
    )
    let lease = try #require(startedLease)

    gate.suspend()
    gate.suspend()

    #expect(!gate.isAvailable)
    #expect(cancellations.value == 1)
    let suspendedLease = gate.begin(for: permit, cancellation: {}, start: {})
    #expect(suspendedLease == nil)

    gate.resume()
    #expect(!gate.isCurrent(permit))
    let staleLease = gate.begin(for: permit, cancellation: {}, start: {})
    #expect(staleLease == nil)
    gate.finish(lease)
    let replacementPermit = try #require(gate.permit)
    let replacementLease = gate.begin(
        for: replacementPermit,
        cancellation: {},
        start: {}
    )
    #expect(replacementLease != nil)
}

@Test func userSessionGateClearsFailedStartAndRequiresExplicitResume() throws {
    enum StartError: Error {
        case failed
    }

    let gate = UserSessionExecutionGate()
    let permit = try #require(gate.permit)
    var startFailed = false
    do {
        _ = try gate.begin(
            for: permit,
            cancellation: {},
            start: { throw StartError.failed }
        )
    } catch StartError.failed {
        startFailed = true
    }
    #expect(startFailed)
    let recoveredLease = gate.begin(for: permit, cancellation: {}, start: {})
    #expect(recoveredLease != nil)

    let suspended = UserSessionExecutionGate(available: false)
    #expect(suspended.permit == nil)
    suspended.resume()
    #expect(suspended.isAvailable)
}
