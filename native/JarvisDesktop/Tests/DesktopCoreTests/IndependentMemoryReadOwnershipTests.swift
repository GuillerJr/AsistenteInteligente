import Foundation
import XCTest
@testable import DesktopCore

private actor OwnedReadWire {
    var operations: [RPCValue] = []
    var loseInventory = false
    var receiptCalls = 0
    func loseInventoryAcknowledgement() { loseInventory = true }
    func count() -> Int { receiptCalls }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        switch method {
        case "maintenance.status":
            if let id = params["operation_id"]?.string,
               let row = operations.first(where: { $0["operation_id"] == .string(id) }) { return row }
            return .object(["profile_id": .string("synthetic_restored"), "generation": .number(7),
                            "closing": .bool(false), "operations": .array(operations),
                            "previous_operations": .array([]), "blockers": .array([])])
        case "maintenance.pending":
            return .object(["profile_id": .string("synthetic_restored"), "generation": .number(7),
                            "pending": .array([])])
        case "memory.restore.inventory":
            if loseInventory {
                operations.append(try RestoredMemoryFixture.operation(kind: method, phase: "working"))
                throw RPCFailure.disconnected
            }
            return try RestoredMemoryFixture.operation(kind: method, phase: "complete",
                result: .object(["inventory": RestoredMemoryFixture.inventory()]))
        case "memory.restore.review":
            return try RestoredMemoryFixture.operation(kind: method, phase: "complete",
                result: .object(["review": RestoredMemoryFixture.review()]))
        case "memory.restore.copy_scope":
            let result = try RestoredMemoryFixture.operation(phase: "publication_unknown",
                receipt: RestoredMemoryFixture.receipt(status: "unknown"), durable: true)
            operations = [result]
            return result
        case "memory.restore.receipt":
            receiptCalls += 1
            // The host legitimately keeps the lost-ACK read's cut. This is a
            // known rejection of a SECOND request, not settlement of the first.
            throw RPCFailure.remote(-32040, "profile_not_quiescent")
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class IndependentMemoryReadOwnershipTests: XCTestCase {
    @MainActor func testPriorLostReadCannotBeClearedByRejectedSecondObservation() async {
        let wire = OwnedReadWire(), runtime = ProfileRuntimeGuard()
        let store = RestoredMemoryStore(runtime: runtime, maxPolls: 1, pause: {
            try await Task.sleep(for: .seconds(60))
        })
        store.configure(RestoredMemoryClient(runtime: runtime) { try await wire.request($0, $1) })
        store.setVisible(UUID(), visible: true)
        store.selectAgent("jarvis")
        await store.loadInventory()
        store.selectSource(RestoredMemoryFixture.sourceID)
        await store.prepareReview()
        await store.submit()
        XCTAssertEqual(store.operation?.memoryCopyReceipt?.status, "unknown")
        await wire.loseInventoryAcknowledgement()
        await store.loadInventory()
        XCTAssertTrue(store.readAwaitingSettlement)
        XCTAssertTrue(store.blocksInteraction)
        XCTAssertNil(store.readOperation) // Accepted operation ID was in the lost ACK.
        await store.queryReceipt()
        let calls = await wire.count()
        XCTAssertEqual(calls, 0, "No second read should be sent while first cut is unconfirmed")
        XCTAssertTrue(store.readAwaitingSettlement, "Known rejection belongs only to second request")
        XCTAssertTrue(store.blocksInteraction)
        await store.close()
    }
}
