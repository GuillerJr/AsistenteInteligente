import Foundation
import XCTest
@testable import DesktopCore

final class AutomationFlowModelsTests: XCTestCase {
    private let identity = String(repeating: "a", count: 32)
    private let workspace = String(repeating: "b", count: 64)
    private var step: RPCValue { .object(["prompt": .string("JARVIS PASO SINTÉTICO"), "pause_after": .bool(true)]) }
    private var hook: [String: RPCValue] {
        ["hook_id": .string(identity), "title": .string("Prueba propia"), "event": .string("manual"),
         "agent_id": .string("jarvis"), "workspace_id": .string(workspace), "revision": .number(1),
         "enabled": .bool(false), "steps": .array([step])]
    }
    private var flow: [String: RPCValue] {
        ["flow_id": .string(String(repeating: "c", count: 32)), "hook_id": .string(identity),
         "hook_revision": .number(1), "title": .string("Prueba propia"), "agent_id": .string("jarvis"),
         "workspace_id": .string(workspace), "revision": .number(2), "status": .string("paused"),
         "outcome": .string("known"), "cursor": .number(0), "steps": .array([step]),
         "run_ids": .array([]), "receipts": .array([]), "expires_at": .number(2_500_000_000), "error_code": .null]
    }
    private func changed(_ value: [String: RPCValue], _ changes: [String: RPCValue] = [:]) -> RPCValue {
        .object(value.merging(changes) { _, value in value })
    }

    func testCronCalendarGrammarRejectsInvalidOrUnboundedFields() {
        for cron in ["*/15 9-17 * * 1-5", "0 0 1,15 * 0,7", "5/2 * * * *", "0 9 * JAN-MAR MON-FRI"] { XCTAssertTrue(ScheduleRuleValidation.cron(cron), cron) }
        for cron in ["* * * *", "* * * * * *", "60 * * * *", "0 24 * * *", "0 0 0 * *", "0 0 * 13 *", "0 0 * * 8", "*/0 * * * *", "0 0 * * MOON", "*/61 * * * *", "0 0 * * 5-1", "0,,5 * * * *", "0 * * * */-1", "0 0 * * *\n"] {
            XCTAssertFalse(ScheduleRuleValidation.cron(cron), cron)
        }
    }

    func testRuleKindsCannotSmuggleFieldsFromOtherScheduleTypes() {
        let cron = ["kind": RPCValue.string("cron"), "timezone": .string("America/Guayaquil"), "cron_expression": .string("0 9 * * 1-5")]
        XCTAssertTrue(ScheduleRuleValidation.valid(changed(cron)))
        XCTAssertFalse(ScheduleRuleValidation.valid(changed(cron, ["at": .number(2_000_000_000)])))
        XCTAssertFalse(ScheduleRuleValidation.valid(changed(cron, ["timezone": .string("../invalid")])))
        XCTAssertFalse(ScheduleRuleValidation.valid(changed(cron, ["extra": .bool(true)])))
        let heartbeat = ["kind": RPCValue.string("heartbeat"), "timezone": .string("UTC"), "interval_seconds": .number(60),
                         "active_start": .string("22:00"), "active_end": .string("06:00")]
        XCTAssertTrue(ScheduleRuleValidation.valid(changed(heartbeat)))
        XCTAssertFalse(ScheduleRuleValidation.valid(changed(heartbeat, ["active_end": .null])))
        XCTAssertFalse(ScheduleRuleValidation.valid(changed(heartbeat, ["active_end": .string("22:00")])))
        XCTAssertFalse(ScheduleRuleValidation.valid(changed(heartbeat, ["kind": .string("interval")])))
        XCTAssertTrue(ScheduleRuleValidation.valid(.object(["kind": .string("event"), "timezone": .string("UTC")])))
    }

    func testHookRequiresBoundedStepsEventIdentityAndExactRevision() throws {
        XCTAssertEqual(try AutomationHook(changed(hook)).steps.count, 1)
        for invalid in [["hook_id": RPCValue.string("../private")], ["event": .string("run completed")],
                        ["revision": .number(0)], ["steps": .array([])], ["steps": .array(Array(repeating: step, count: 17))],
                        ["workspace_id": .string("other")], ["steps": .array([.object(["prompt": .string("x"), "pause_after": .string("yes")])])]] {
            XCTAssertThrowsError(try AutomationHook(changed(hook, invalid)))
        }
    }

    func testUnknownOrIncompleteFlowNeverBecomesAResumableSuccess() throws {
        XCTAssertTrue(try AutomationFlow(changed(flow)).canResume)
        let interrupted = try AutomationFlow(changed(flow, ["status": .string("interrupted"), "outcome": .string("unknown")]))
        XCTAssertFalse(interrupted.canResume); XCTAssertFalse(interrupted.canCancel)
        XCTAssertThrowsError(try AutomationFlow(changed(flow, ["status": .string("completed")])))
        XCTAssertThrowsError(try AutomationFlow(changed(flow, ["status": .string("paused"), "outcome": .string("unknown")])))
        XCTAssertThrowsError(try AutomationFlow(changed(flow, ["cursor": .number(2)])))
        XCTAssertThrowsError(try AutomationFlow(changed(flow, ["run_ids": .array([.string("outside")])])) )
        XCTAssertFalse(try AutomationFlow(changed(flow, ["expires_at": .number(1)])).canResume)
        let run = String(repeating: "d", count: 32)
        let receipt = RPCValue.object(["claim_id": .string(String(repeating: "e", count: 32)),
            "step_index": .number(0), "run_id": .string(run), "status": .string("completed"), "outcome": .string("known")])
        let completed = try AutomationFlow(changed(flow, ["status": .string("completed"), "cursor": .number(1),
            "run_ids": .array([.string(run)]), "receipts": .array([receipt])]))
        XCTAssertEqual(completed.receipts.first?.runID, run)
        XCTAssertFalse(completed.canResume)
        let linked = try AutomationFlow(changed(flow, ["status": .string("completed"), "cursor": .number(1),
            "run_ids": .array([.string(run)]), "receipts": .array([receipt]),
            "sessions": .array([.object(["run_id": .string(run), "session_id": .string(identity)])])]))
        XCTAssertEqual(linked.sessions.first?.sessionID, identity)
        XCTAssertEqual(linked.sessions.first?.stepNumber, 1)
        XCTAssertThrowsError(try AutomationFlow(changed(flow, ["sessions": .array([
            .object(["run_id": .string(run), "session_id": .string(identity)])])])) )
        XCTAssertThrowsError(try AutomationFlow(changed(flow, ["status": .string("completed"), "cursor": .number(1),
            "run_ids": .array([]), "receipts": .array([receipt])])))
    }

    func testWebhookNeverAcceptsRemoteEndpointCredentialsOrReturnedSecret() throws {
        let active: [String: RPCValue] = ["running": .bool(true), "url": .string("http://127.0.0.1:12345/hook"), "hook_id": .string(identity)]
        XCTAssertTrue(try AutomationListener(changed(active)).running)
        XCTAssertTrue(try AutomationListener(changed(active, ["running": .bool(false), "url": .null, "pending_cleanup": .bool(true)])).pendingCleanup)
        for change in [["url": RPCValue.string("http://example.com/hook")], ["url": .string("http://127.0.0.1:12345/hook?secret=leak")],
                       ["url": .string("http://owner:secret@127.0.0.1:12345/hook")], ["secret": .string("must-not-return")],
                       ["running": .bool(false)], ["hook_id": .string("outside")]] {
            XCTAssertThrowsError(try AutomationListener(changed(active, change)))
        }
        XCTAssertTrue(AutomationWebhookInput.validSecret(String(repeating: "a", count: 32)))
        for secret in [String(repeating: "a", count: 31), String(repeating: "a", count: 65), String(repeating: "á", count: 16), String(repeating: "a", count: 31) + "\n"] {
            XCTAssertFalse(AutomationWebhookInput.validSecret(secret))
        }
    }

    func testCatalogRejectsDuplicateIdentitiesInsteadOfMergingOwnerTargets() throws {
        let snapshot: [String: RPCValue] = ["hooks": .array([changed(hook)]), "flows": .array([changed(flow)]),
            "configuration_hash": .string(workspace), "workspace_id": .string(workspace),
            "runner_running": .bool(false), "webhook": .object(["running": .bool(false)])]
        XCTAssertFalse(try AutomationSnapshot(changed(snapshot)).running)
        XCTAssertThrowsError(try AutomationSnapshot(changed(snapshot, ["hooks": .array([changed(hook), changed(hook)])])))
        XCTAssertThrowsError(try AutomationSnapshot(changed(snapshot, ["flows": .array([changed(flow), changed(flow)])])))
        let paged = try AutomationSnapshot(changed(snapshot, ["offset": .number(8), "next_offset": .number(9), "has_more": .bool(true)]))
        XCTAssertEqual(paged.nextOffset, 9)
        XCTAssertNil(try AutomationSnapshot(changed(snapshot, ["offset": .number(0), "next_offset": .number(1), "has_more": .bool(false)])).nextOffset)
        XCTAssertThrowsError(try AutomationSnapshot(changed(snapshot, ["offset": .number(8), "next_offset": .number(8), "has_more": .bool(true)])))
        XCTAssertThrowsError(try AutomationSnapshot(changed(snapshot, ["flows": .array([]), "next_offset": .number(1), "has_more": .bool(true)])))
    }

    func testMutationCannotAddressUnrelatedToolOrAcceptAnotherFlow() async throws {
        let reply = changed(flow)
        let client = ManagementClient(request: { _, _ in .object(["flow": reply]) })
        do { _ = try await client.mutateAutomation("processes.run", params: [:]); XCTFail("Sin vía alternativa de efectos") }
        catch { XCTAssertEqual(error as? RPCFailure, .configuration) }
        do {
            _ = try await client.mutateAutomation("automation.flows.resume", params: ["flow_id": .string(identity), "expected_revision": .number(2)])
            XCTFail("El recibo corresponde a otro flujo")
        } catch { XCTAssertEqual(error as? RPCFailure, .invalidResponse) }
    }
}
