import Foundation
import XCTest
@testable import DesktopCore

final class ManagementModelsTests: XCTestCase {
    func testManagementFailuresExplainStaleReviewAndUncertaintyWithoutExposingDiagnostics() {
        XCTAssertEqual(ManagementPresentation.errorMessage(RPCFailure.remote(-32035, "skill_source_changed")),
                       "La carpeta de la skill cambió desde la revisión. Vuelve a seleccionarla y revisa su contenido.")
        for code in ["agent_revision_conflict", "skill_revision_conflict", "scheduler_revision_conflict"] {
            XCTAssertEqual(ManagementPresentation.errorMessage(RPCFailure.remote(-32035, code)),
                           "La configuración cambió desde que la revisaste. Actualiza la lista y revisa de nuevo el cambio.")
        }
        XCTAssertTrue(ManagementPresentation.errorMessage(RPCFailure.remote(-32035, "registry_durability_uncertain"))
            .contains("no se repetirá automáticamente"))
        XCTAssertEqual(ManagementPresentation.errorMessage(RPCFailure.remote(-32035, "/private/diagnostico-secret")),
                       "No se pudo completar el cambio de gestión. Actualiza el estado antes de continuar.")
        XCTAssertEqual(ManagementPresentation.errorMessage(RPCFailure.remote(-32035, "future_unknown_code")),
                       "No se pudo completar el cambio de gestión. Actualiza el estado antes de continuar.")
        XCTAssertEqual(ManagementPresentation.errorMessage(RPCFailure.disconnected),
                       RPCFailure.disconnected.localizedDescription)
        XCTAssertEqual(ManagementPresentation.errorMessage(RPCFailure.remote(-32032, "El agente conserva conversaciones.")),
                       "El agente conserva conversaciones.")
    }

    private func object(_ fields: [String: RPCValue], _ updates: [String: RPCValue] = [:]) -> RPCValue {
        .object(fields.merging(updates) { _, changed in changed })
    }
    private var agent: [String: RPCValue] {
        ["agent_id": .string("jarvis"), "name": .string("Jarvis"), "instructions": .string(""), "revision": .number(1)]
    }
    private var schedule: [String: RPCValue] {
        ["schedule_id": .string(String(repeating: "a", count: 32)), "title": .string("Prueba"),
         "prompt": .string("Sólo responde"), "agent_id": .string("jarvis"),
         "workspace_id": .string(String(repeating: "b", count: 64)), "enabled": .bool(false),
         "revision": .number(1), "next_at": .number(2_000_000_000),
         "spec": .object(["kind": .string("once"), "timezone": .string("UTC"), "at": .number(2_000_000_000)])]
    }

    func testAgentIdentityRevisionAndInstructionBudgetAreValidated() throws {
        XCTAssertEqual(try AgentDescriptor(object(agent)).id, "jarvis")
        for change in [["agent_id": RPCValue.string("../another")], ["revision": .number(0)],
                       ["instructions": .string(String(repeating: "x", count: 8193))], ["name": .string("")]] {
            XCTAssertThrowsError(try AgentDescriptor(object(agent, change)))
        }
    }

    func testSkillPreviewHashMustMatchTheExactOwnerReviewedBytes() throws {
        let text = "---\nname: prueba\ndescription: Prueba local\n---\nTexto Ω\n"
        let fields: [String: RPCValue] = ["name": .string("prueba"), "description": .string("Prueba local"),
             "text": .string(text), "sha256": .string(ManagementValidation.digest(text)),
             "source_path": .string("/private/tmp/fixture"), "resources": .array([.string("notes/example.txt")])]
        let preview = try SkillPreviewSnapshot(object(fields))
        XCTAssertEqual(preview.text, text)
        XCTAssertThrowsError(try SkillPreviewSnapshot(object(fields, ["text": .string(text + "cambió")])))
        XCTAssertThrowsError(try SkillPreviewSnapshot(object(fields, ["sha256": .string(String(repeating: "c", count: 64))])))
        XCTAssertThrowsError(try SkillPreviewSnapshot(object(fields, ["source_path": .string("relative")])))
    }

    func testScheduleRulesAndWholeReviewedCatalogAreBounded() throws {
        let row = try ScheduleDescriptor(object(schedule))
        XCTAssertEqual(row.agentID, "jarvis")
        for spec in [RPCValue.object(["kind": .string("auto_cron"), "timezone": .string("UTC")]),
                     .object(["kind": .string("daily"), "timezone": .string("UTC"), "local_time": .string("24:30")]),
                     .object(["kind": .string("interval"), "timezone": .string("UTC"), "interval_seconds": .number(0)])] {
            XCTAssertThrowsError(try ScheduleDescriptor(object(schedule, ["spec": spec])))
        }
        let base: [String: RPCValue] = ["schedules": .array([object(schedule)]),
            "configuration_hash": .string(String(repeating: "d", count: 64)), "runner_running": .bool(false),
            "workspace_id": .string(String(repeating: "b", count: 64)), "error": .null]
        XCTAssertFalse(try SchedulerSnapshot(object(base)).running)
        XCTAssertThrowsError(try SchedulerSnapshot(object(base, ["schedules": .array([object(schedule), object(schedule)])])))
        XCTAssertThrowsError(try SchedulerSnapshot(object(base, ["configuration_hash": .string("changed")])))
        XCTAssertThrowsError(try SchedulerSnapshot(object(base, ["schedules": .array(Array(repeating: object(schedule), count: 33))])))
        XCTAssertThrowsError(try ScheduleDescriptor(object(schedule, ["prompt": .string(String(repeating: "x", count: 8001))])))
    }

    func testOccurrenceDoesNotTurnInterruptedOrPendingWorkIntoKnownCompletion() throws {
        let fields: [String: RPCValue] = ["occurrence_id": .string(String(repeating: "c", count: 32)),
            "schedule_id": .string(String(repeating: "a", count: 32)), "scheduled_at": .number(2_000_000_000),
            "status": .string("interrupted"), "outcome": .string("unknown"), "run_id": .null, "session_id": .null]
        XCTAssertEqual(try ScheduleOccurrenceSnapshot(object(fields)).outcome, "unknown")
        XCTAssertThrowsError(try ScheduleOccurrenceSnapshot(object(fields, ["status": .string("completed") ])))
        XCTAssertThrowsError(try ScheduleOccurrenceSnapshot(object(fields, ["status": .string("running") ])))
        XCTAssertThrowsError(try ScheduleOccurrenceSnapshot(object(fields, ["session_id": .string("outside")])))
    }

    func testProcessExitAndOutputCannotClaimIndependentEffectVerification() throws {
        let fields: [String: RPCValue] = ["process_id": .string(String(repeating: "a", count: 32)),
            "state": .string("completed"), "running": .bool(false), "exit_code": .number(0),
            "output": .string("salida"), "cursor": .number(0), "next_cursor": .number(6),
            "has_more": .bool(false), "output_bytes": .number(6), "retained_bytes": .number(6),
            "output_sha256": .string(ManagementValidation.digest("salida")), "output_truncated": .bool(false),
            "backend": .string("macos_seatbelt"), "verification": .string("process_state_only"),
            "effects_independently_verified": .bool(false)]
        XCTAssertEqual(try ProcessSnapshot(object(fields)).exitCode, 0)
        for change in [["effects_independently_verified": RPCValue.bool(true)],
                       ["running": .bool(true)], ["next_cursor": .number(7)], ["has_more": .bool(true)],
                       ["backend": .string("unsandboxed_host")]] {
            XCTAssertThrowsError(try ProcessSnapshot(object(fields, change)))
        }
    }

    func testManagementClientRejectsDirectProcessEffectsAsManagementMutations() async throws {
        let transport = LocalRPCClient(stateDirectory: "/private/tmp/nonexistent-management-test")
        let client = ManagementClient(transport: transport)
        do { _ = try await client.mutate("processes.stop", params: [:]); XCTFail("No debe eludir autoridad.") }
        catch { XCTAssertEqual(error as? RPCFailure, .configuration) }
        await client.close()
    }
}
