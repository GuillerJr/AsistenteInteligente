import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class JarvisScheduleDraftTests: XCTestCase {
    func testEditingOtherFieldsPreservesExactInterval() async throws {
        for minutes in [15, 30, 90, 120, 10_080] { try checkInterval(minutes: minutes) }
    }

    private func checkInterval(minutes: Int) throws {
        let data = try JSONSerialization.data(withJSONObject: [
            "task_id": "task-1", "title": "Resumen", "prompt": "Resume las novedades",
            "schedule": "Cada \(minutes) minutos", "notify": "always", "enabled": true,
            "spec": ["kind": "interval", "weekdays": [], "every_minutes": minutes],
        ])
        var draft = JarvisScheduleDraft(try JSONDecoder().decode(JarvisScheduledTask.self, from: data))
        draft.title = "Resumen editado"
        draft.onlyWhenNew = true
        XCTAssertTrue(draft.valid)
        XCTAssertTrue(draft.payload["every_minutes"] as? Int == minutes)
        XCTAssertTrue(draft.payload["title"] as? String == "Resumen editado")
        XCTAssertTrue(draft.payload["notify"] as? String == "when_new")
        XCTAssertTrue(draft.payload["task_id"] as? String == "task-1")
    }

    func testIntervalEditsUseMinutesAndValidateDaemonLimits() async {
        var draft = JarvisScheduleDraft()
        draft.title = "Resumen"
        draft.prompt = "Resume las novedades"
        draft.kind = "interval"
        draft.everyMinutes = 45
        XCTAssertTrue(draft.valid)
        XCTAssertTrue(draft.payload["every_minutes"] as? Int == 45)
        draft.everyMinutes = 14
        XCTAssertTrue(!draft.valid)
        draft.everyMinutes = 10_081
        XCTAssertTrue(!draft.valid)
    }
}
