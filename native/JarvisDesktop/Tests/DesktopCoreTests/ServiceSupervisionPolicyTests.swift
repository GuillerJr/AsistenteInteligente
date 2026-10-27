import XCTest
@testable import DesktopCore

final class ServiceSupervisionPolicyTests: XCTestCase {
    /// A visible maintenance panel or lost operation response pauses readers.
    /// The same pause must not suppress detection of the owned child's exit.
    func testExitedChildIsStillObservedWhileProfileReadersArePaused() {
        for paused in [false, true] {
            XCTAssertEqual(ServiceSupervisionPolicy.decision(
                childRunning: false, lifecycleChanging: false,
                recoveryBlocked: false, readersPaused: paused), .recover)
        }
    }

    func testShutdownHandoffAndUnconfirmedLifecycleNeverStartRecovery() {
        for running in [false, true] {
            for paused in [false, true] {
                for blocked in [false, true] {
                    XCTAssertEqual(ServiceSupervisionPolicy.decision(
                        childRunning: running, lifecycleChanging: true,
                        recoveryBlocked: blocked, readersPaused: paused), .wait)
                }
            }
        }
    }

    func testBlockedDeadOwnerNeedsAttentionAndLivePausedOwnerDoesNotRead() {
        XCTAssertEqual(ServiceSupervisionPolicy.decision(
            childRunning: false, lifecycleChanging: false,
            recoveryBlocked: true, readersPaused: true), .attention)
        XCTAssertEqual(ServiceSupervisionPolicy.decision(
            childRunning: true, lifecycleChanging: false,
            recoveryBlocked: false, readersPaused: true), .wait)
        XCTAssertEqual(ServiceSupervisionPolicy.decision(
            childRunning: true, lifecycleChanging: false,
            recoveryBlocked: true, readersPaused: false), .read)
    }
}
