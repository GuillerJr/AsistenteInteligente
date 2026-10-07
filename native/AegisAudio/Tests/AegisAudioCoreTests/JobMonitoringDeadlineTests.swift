import Foundation
import Testing
@testable import AegisAudioCore

private func budgetStatus(total: Any = 120_000, remaining: Any = 110_000) -> IPCJobStatusEvent? {
    IPCJobStatusEvent(response: LocalIPCResponse(
        requestID: UUID(), ok: true,
        payload: ["job_id": UUID().uuidString, "status": "running",
                  "execution_budget_ms": total, "remaining_execution_ms": remaining],
        errorCode: nil
    ))
}

@Test func jobMonitoringAllowsLongTasksAndUsesConfiguredServiceBudget() throws {
    var monitoring = JobMonitoringDeadline(startedAt: 0)
    monitoring.observe(try #require(budgetStatus()), at: 10)
    #expect(!monitoring.isExpired(at: 70))
    #expect(!monitoring.isExpired(at: 144))
    #expect(monitoring.isExpired(at: 145))

    var configured = JobMonitoringDeadline(startedAt: 0)
    configured.observe(try #require(budgetStatus(total: 300_000, remaining: 290_000)), at: 10)
    #expect(!configured.isExpired(at: 300))
    #expect(configured.isExpired(at: 325))
}

@Test func jobMonitoringDoesNotExtendDeadlineOnRepeatedOrPartialUpdates() throws {
    var monitoring = JobMonitoringDeadline(startedAt: 0)
    monitoring.observe(try #require(budgetStatus(remaining: 20_000)), at: 100)
    monitoring.observe(try #require(budgetStatus(remaining: 20_000)), at: 110)
    #expect(monitoring.isExpired(at: 145))
}

@Test func jobMonitoringAfterApprovalUsesRemainingActiveTime() throws {
    var monitoring = JobMonitoringDeadline(startedAt: 300)
    monitoring.observe(try #require(budgetStatus(remaining: 15_000)), at: 300)
    #expect(!monitoring.isExpired(at: 339))
    #expect(monitoring.isExpired(at: 340))
}

@Test func jobBudgetRejectsMalformedTimesAndKeepsLegacyCompatibility() throws {
    for (total, remaining) in [(120_000, -1), (120_000, 120_001), (600_001, 1), (0, 0)] {
        #expect(budgetStatus(total: total, remaining: remaining) == nil)
    }
    #expect(budgetStatus(total: true) == nil)
    #expect(budgetStatus(remaining: 1.5) == nil)
    #expect(budgetStatus(remaining: NSNull()) == nil)
    let legacy = try #require(budgetStatus(total: NSNull(), remaining: NSNull()))
    #expect(legacy.executionBudgetMilliseconds == nil)
    var monitoring = JobMonitoringDeadline(startedAt: 0)
    monitoring.observe(legacy, at: 50)
    #expect(!monitoring.isExpired(at: 70))
    #expect(monitoring.isExpired(at: 145))
}
