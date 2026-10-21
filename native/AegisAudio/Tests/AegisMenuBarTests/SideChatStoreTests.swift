import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
private final class SideChatIPCStub {
    let conversation = UUID().uuidString.lowercased()
    let job = UUID().uuidString.lowercased()
    let pauseAt: String
    var calls: [(String, [String: Any])] = []
    var waitResult: [String: Any]? = ["status": "completed", "result": "Respuesta final"]
    private var paused = false
    private var continuation: CheckedContinuation<Void, Never>?
    private var arrival: CheckedContinuation<Void, Never>?

    init(pauseAt: String) { self.pauseAt = pauseAt }

    func call(_ method: String, _ payload: [String: Any]) async -> Data? {
        calls.append((method, payload))
        if method == pauseAt, !paused {
            paused = true
            arrival?.resume()
            arrival = nil
            await withCheckedContinuation { continuation = $0 }
        }
        let response: [String: Any]
        switch method {
        case "conversations.create": response = ["conversation_id": conversation]
        case "swarm.submit": response = ["job_id": job, "status": "running"]
        case "jobs.wait":
            guard let waitResult else { return nil }
            response = waitResult
        default: response = [:]
        }
        return try? JSONSerialization.data(withJSONObject: response)
    }

    func waitUntilPaused() async {
        if !paused { await withCheckedContinuation { arrival = $0 } }
    }

    func resume() { continuation?.resume(); continuation = nil }
}

@MainActor
final class SideChatStoreTests: XCTestCase {
    func testCloseDuringIPCDiscardsReplyAndCleansUp() async {
        for pauseAt in ["conversations.create", "swarm.submit", "jobs.wait"] {
            await checkClose(pauseAt: pauseAt)
        }
    }

    private func checkClose(pauseAt: String) async {
        let store = SideChatStore()
        let ipc = SideChatIPCStub(pauseAt: pauseAt)
        let send = Task { await store.send("Hola", using: ipc.call) }
        await ipc.waitUntilPaused()
        XCTAssertTrue(store.busy)
        await store.close(using: ipc.call)
        XCTAssertTrue(store.lines.isEmpty)
        XCTAssertTrue(!store.busy)
        ipc.resume()
        await send.value
        XCTAssertTrue(store.lines.isEmpty)
        XCTAssertTrue(!store.busy)
        XCTAssertTrue(ipc.calls.contains { $0.0 == "conversations.delete" && $0.1["conversation_id"] as? String == ipc.conversation })
        if pauseAt != "conversations.create" {
            XCTAssertTrue(ipc.calls.contains { $0.0 == "jobs.cancel" && $0.1["job_id"] as? String == ipc.job })
        } else {
            XCTAssertTrue(!ipc.calls.contains { $0.0 == "swarm.submit" })
        }
    }

    func testOldResponseCannotOverwriteReopenedChatOrClearItsBusyState() async {
        let store = SideChatStore()
        let old = SideChatIPCStub(pauseAt: "jobs.wait")
        let first = Task { await store.send("Primera", using: old.call) }
        await old.waitUntilPaused()
        await store.close(using: old.call)
        let current = SideChatIPCStub(pauseAt: "jobs.wait")
        let second = Task { await store.send("Segunda", using: current.call) }
        await current.waitUntilPaused()
        old.resume()
        await first.value
        XCTAssertTrue(store.busy)
        XCTAssertTrue(store.lines.map(\.text) == ["Segunda", ""])
        current.resume()
        await second.value
        XCTAssertTrue(!store.busy)
        XCTAssertTrue(store.lines.map(\.text) == ["Segunda", "Respuesta final"])
        await store.close(using: current.call)
    }

    func testUnfinishedJobsAreCancelled() async {
        for reason in ["disconnect", "approval", "timeout"] { await checkCancellation(reason: reason) }
    }

    private func checkCancellation(reason: String) async {
        let store = SideChatStore()
        let ipc = SideChatIPCStub(pauseAt: "")
        switch reason {
        case "disconnect": ipc.waitResult = nil
        case "approval": ipc.waitResult = ["status": "awaiting_confirmation"]
        default: ipc.waitResult = ["status": "running"]
        }
        await store.send("Hola", using: ipc.call)
        XCTAssertTrue(!store.busy)
        XCTAssertTrue(ipc.calls.contains { $0.0 == "jobs.cancel" })
        XCTAssertTrue(store.lines.last?.text.isEmpty == false)
        await store.close(using: ipc.call)
    }
}
