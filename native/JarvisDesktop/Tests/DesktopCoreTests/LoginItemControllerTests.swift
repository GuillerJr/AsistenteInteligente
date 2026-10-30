import XCTest
@testable import DesktopCore

@MainActor private final class SyntheticLoginItemService: LoginItemRegistrationService {
    var registrationStatus: LoginItemRegistrationStatus = .notRegistered
    var registerResult: LoginItemRegistrationStatus = .enabled
    var unregisterResult: LoginItemRegistrationStatus = .notRegistered
    var failRegister = false
    var failUnregister = false
    var registrations = 0
    var removals = 0
    var settingsOpened = 0
    var onUnregister: (() async throws -> Void)?
    func register() throws {
        registrations += 1; registrationStatus = registerResult
        if failRegister { throw RPCFailure.unavailable }
    }
    func unregister() async throws {
        removals += 1
        if let onUnregister { try await onUnregister() }
        registrationStatus = unregisterResult
        if failUnregister { throw RPCFailure.unavailable }
    }
    func openSettings() { settingsOpened += 1 }
}

final class LoginItemControllerTests: XCTestCase {
    @MainActor func testConstructionRefreshAndExternalRevocationNeverRegister() async {
        let service = SyntheticLoginItemService()
        let controller = LoginItemController(service: service, validateBundle: { true })
        XCTAssertEqual(controller.status, .unknown)
        controller.refresh()
        XCTAssertEqual(controller.status, .notRegistered)
        await controller.change(enabled: true, confirm: { false })
        XCTAssertEqual(service.registrations, 0)
        await controller.change(enabled: true, confirm: { true })
        XCTAssertEqual(service.registrations, 1)
        XCTAssertEqual(controller.status, .enabled)
        service.registrationStatus = .requiresApproval
        controller.refresh(); controller.refresh()
        XCTAssertEqual(controller.status, .requiresApproval)
        XCTAssertFalse(controller.canEnable)
        XCTAssertTrue(controller.canDisable)
        XCTAssertEqual(service.registrations, 1, "OS revocation is not a request to silently restore consent")
        controller.openSettings()
        XCTAssertEqual(service.settingsOpened, 1)
        XCTAssertEqual(service.registrations, 1)
    }

    @MainActor func testRequiresApprovalAfterRegisterIsNotReportedAsEnabled() async {
        let service = SyntheticLoginItemService(); service.registerResult = .requiresApproval
        let controller = LoginItemController(service: service, validateBundle: { true })
        await controller.change(enabled: true, confirm: { true })
        XCTAssertEqual(controller.status, .requiresApproval)
        XCTAssertTrue(controller.notice?.contains("requiere tu aprobación") == true)
        XCTAssertEqual(service.registrations, 1)
        await controller.change(enabled: true, confirm: { XCTFail("No second registration review"); return true })
        XCTAssertEqual(service.registrations, 1)
    }

    @MainActor func testInvalidOrChangedBundleNeverMutatesEvenAfterOwnerReview() async {
        let service = SyntheticLoginItemService()
        var valid = false
        let controller = LoginItemController(service: service, eligibility: { valid }, validateBundle: { valid })
        await controller.change(enabled: true, confirm: { XCTFail("Ineligible bundle"); return true })
        XCTAssertEqual(service.registrations, 0)
        valid = true
        await controller.change(enabled: true, confirm: { valid = false; return true })
        XCTAssertEqual(service.registrations, 0)
        XCTAssertFalse(controller.eligible)
        XCTAssertTrue(controller.notice?.contains("cambió") == true)
    }

    @MainActor func testReviewDoesNotOverrideAChangedOSAuthorization() async {
        let service = SyntheticLoginItemService()
        let controller = LoginItemController(service: service, validateBundle: { true })
        await controller.change(enabled: true, confirm: { service.registrationStatus = .requiresApproval; return true })
        XCTAssertEqual(service.registrations, 0)
        XCTAssertEqual(controller.status, .requiresApproval)
        XCTAssertTrue(controller.notice?.contains("cambió") == true)
    }

    @MainActor func testNestedRefreshCannotRebindTheStatusBeingReviewed() async {
        let service = SyntheticLoginItemService()
        let controller = LoginItemController(service: service, validateBundle: { true })
        await controller.change(enabled: true, confirm: {
            service.registrationStatus = .requiresApproval
            controller.refresh()
            return true
        })
        XCTAssertEqual(service.registrations, 0)
        XCTAssertEqual(controller.status, .requiresApproval)
    }

    @MainActor func testMissingOrFutureOSStatusCannotRegisterOrUnregister() async {
        let service = SyntheticLoginItemService()
        let controller = LoginItemController(service: service, validateBundle: { true })
        for status in [LoginItemRegistrationStatus.notFound, .unknown] {
            service.registrationStatus = status
            controller.refresh()
            XCTAssertFalse(controller.canEnable); XCTAssertFalse(controller.canDisable)
            await controller.change(enabled: true, confirm: { XCTFail("Unknown service state"); return true })
            await controller.change(enabled: false, confirm: { XCTFail("Unknown service state"); return true })
        }
        XCTAssertEqual(service.registrations, 0); XCTAssertEqual(service.removals, 0)
    }

    @MainActor func testFailedAcknowledgementReadsOSStateAndNeverRetries() async {
        let service = SyntheticLoginItemService(); service.failRegister = true
        let controller = LoginItemController(service: service, validateBundle: { true })
        await controller.change(enabled: true, confirm: { true })
        XCTAssertEqual(service.registrations, 1)
        XCTAssertEqual(controller.status, .enabled, "A thrown API call does not prove there was no change")
        controller.refresh()
        XCTAssertEqual(service.registrations, 1)
        XCTAssertTrue(controller.notice?.contains("no confirmó") == true)
    }

    @MainActor func testUnregisterNeedsSeparateReviewAndDoesNotClaimServiceQuit() async {
        let service = SyntheticLoginItemService(); service.registrationStatus = .enabled
        let controller = LoginItemController(service: service, validateBundle: { true })
        await controller.change(enabled: false, confirm: { false })
        XCTAssertEqual(service.removals, 0)
        await controller.change(enabled: false, confirm: { true })
        XCTAssertEqual(service.removals, 1)
        XCTAssertEqual(controller.status, .notRegistered)
        XCTAssertTrue(controller.notice?.contains("siguen abiertos") == true)
        XCTAssertEqual(service.registrations, 0)
    }

    @MainActor func testConcurrentChangeCannotRegisterWhileUnregisterIsPending() async throws {
        let service = SyntheticLoginItemService(); service.registrationStatus = .enabled
        var entered: CheckedContinuation<Void, Never>?
        var finish: CheckedContinuation<Void, Never>?
        service.onUnregister = {
            entered?.resume(); entered = nil
            await withCheckedContinuation { finish = $0 }
        }
        let controller = LoginItemController(service: service, validateBundle: { true })
        let first = Task { @MainActor in await controller.change(enabled: false, confirm: { true }) }
        await withCheckedContinuation { ready in
            if service.removals == 1 { ready.resume() }
            else { entered = ready }
        }
        XCTAssertTrue(controller.isChanging)
        await controller.change(enabled: true, confirm: { XCTFail("Concurrent mutation"); return true })
        XCTAssertEqual(service.registrations, 0)
        let release = try XCTUnwrap(finish); finish = nil; release.resume()
        await first.value
        XCTAssertFalse(controller.isChanging)
        XCTAssertEqual(controller.status, .notRegistered)
    }
}
