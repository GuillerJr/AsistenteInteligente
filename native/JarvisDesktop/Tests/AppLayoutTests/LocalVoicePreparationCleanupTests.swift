import DesktopCore
import XCTest
@testable import JarvisApp

@MainActor private final class SyntheticPreparedRecognition: PreparedLocalRecognition {
    var cancelCount = 0
    var closed = false
    private var waiter: CheckedContinuation<Void, Never>?
    func start(receive: @escaping @MainActor (LocalRecognitionEvent) -> Void) throws { XCTFail("No microphone operation belongs to this test") }
    func finish() { XCTFail("No recognition operation belongs to this test") }
    func cancel() { cancelCount += 1 }
    func waitForCleanup() async {
        guard !closed else { return }
        await withCheckedContinuation { waiter = $0 }
    }
    func acknowledgeCleanup() { closed = true; waiter?.resume(); waiter = nil }
}

@MainActor private final class SyntheticPreparationFactory {
    var calls = 0
    var first: CheckedContinuation<any PreparedLocalRecognition, Error>?
    let retired = SyntheticPreparedRecognition()
    let next = SyntheticPreparedRecognition()
    func prepare(_ locale: String, current: @escaping @MainActor () -> Bool) async throws -> any PreparedLocalRecognition {
        calls += 1
        if calls == 1 { return try await withCheckedThrowingContinuation { first = $0 } }
        return next
    }
    func resolveRetired() { first?.resume(returning: retired); first = nil }
}

final class LocalVoicePreparationCleanupTests: XCTestCase {
    @MainActor func testCancelledPreparationRetainsCleanupBeforeAnotherModuleCanPrepare() async throws {
        guard #available(macOS 26.0, *) else { throw XCTSkip("El adaptador moderno requiere macOS 26") }
        let factory = SyntheticPreparationFactory()
        let driver = MacLocalVoiceDriver(prepareModern: { try await factory.prepare($0, current: $1) })
        var firstCompletion = false, nextCompletion = false
        let first = driver.prepareRecognition(localeIdentifier: "es-ES") { _ in firstCompletion = true }
        for _ in 0..<100 where factory.calls == 0 { await Task.yield() }
        XCTAssertEqual(factory.calls, 1)
        driver.cancelRecognition()
        let next = driver.prepareRecognition(localeIdentifier: "es-ES") { result in
            if case .success = result { nextCompletion = true }
        }
        for _ in 0..<30 { await Task.yield() }
        XCTAssertEqual(factory.calls, 1); XCTAssertFalse(nextCompletion)
        factory.resolveRetired()
        for _ in 0..<100 where factory.retired.cancelCount == 0 { await Task.yield() }
        XCTAssertEqual(factory.retired.cancelCount, 1)
        XCTAssertEqual(factory.calls, 1); XCTAssertFalse(firstCompletion)
        factory.retired.acknowledgeCleanup()
        for _ in 0..<200 where !nextCompletion { await Task.yield() }
        XCTAssertEqual(factory.calls, 2); XCTAssertTrue(nextCompletion); XCTAssertFalse(firstCompletion)
        driver.cancelRecognition(); factory.next.acknowledgeCleanup()
        _ = (first, next) // Retain request identities through both acknowledgments.
    }
    @MainActor func testCancelledBeforePreparationStartsCannotAwaitItsOwnCleanupFence() async throws {
        guard #available(macOS 26.0, *) else { throw XCTSkip("El adaptador moderno requiere macOS 26") }
        var preparations = 0, completed = false
        let ready = SyntheticPreparedRecognition()
        let driver = MacLocalVoiceDriver(prepareModern: { _, _ in preparations += 1; return ready })
        let first = driver.prepareRecognition(localeIdentifier: "es-ES") { _ in XCTFail("Cancelled callback") }
        driver.cancelRecognition()
        let next = driver.prepareRecognition(localeIdentifier: "es-ES") { result in
            if case .success = result { completed = true }
        }
        for _ in 0..<200 where !completed { await Task.yield() }
        XCTAssertEqual(preparations, 1); XCTAssertTrue(completed)
        driver.cancelRecognition(); ready.acknowledgeCleanup()
        _ = (first, next)
    }
}
