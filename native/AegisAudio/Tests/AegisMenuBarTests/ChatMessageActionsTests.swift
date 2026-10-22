import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class ChatMessageActionsTests: XCTestCase {
    func testRejectedRegenerationKeepsDraftOnlyForTheSameUneditedConversation() async {
        let chat = JarvisChatStore()
        chat.conversationID = UUID()
        await chat.sendReplayPreservingDraft("Solicitud a regenerar") { false }
        XCTAssertEqual(chat.composerDraft, "Solicitud a regenerar")

        chat.composerDraft = nil
        await chat.sendReplayPreservingDraft("Solicitud aceptada") { true }
        XCTAssertNil(chat.composerDraft)

        await chat.sendReplayPreservingDraft("Texto anterior") {
            chat.composerDraft = "Edición posterior del dueño"
            return false
        }
        XCTAssertEqual(chat.composerDraft, "Edición posterior del dueño")

        chat.composerDraft = nil
        await chat.sendReplayPreservingDraft("Texto anterior") {
            chat.transcriptRevision = UUID()
            return false
        }
        XCTAssertNil(chat.composerDraft)

        await chat.sendReplayPreservingDraft("Texto de otra conversación") {
            chat.newConversation()
            return false
        }
        XCTAssertNil(chat.composerDraft)
    }

    func testReplayRequiresPersistedContiguousUserAndOnlyLastResponse() async {
        let chat = JarvisChatStore()
        let user = TextChatTranscript.Message(role: .user, text: "Pregunta", sequence: 3)
        let reply = TextChatTranscript.Message(role: .assistant, text: "Respuesta", sequence: 4)
        chat.transcript.restore([user, reply])
        XCTAssertEqual(chat.replayTarget(for: reply), user)
        XCTAssertEqual(chat.replayTarget(for: user), user)
        XCTAssertNil(chat.replayTarget(for: .init(role: .user, text: "Provisional")))
        var incomplete = user
        incomplete.truncated = true
        XCTAssertNil(chat.replayTarget(for: incomplete))
        let later = TextChatTranscript.Message(role: .assistant, text: "Otra", sequence: 6)
        chat.transcript.restore([user, reply, later])
        XCTAssertNil(chat.replayTarget(for: reply))
        XCTAssertNil(chat.replayTarget(for: later))
    }

    func testReplayDecodesDocumentsAndExplicitlyReportsImageNeedsReattachment() async throws {
        let id = UUID()
        let project = UUID()
        let data = try JSONSerialization.data(withJSONObject: [
            "conversation_id": id.uuidString, "title": "Rama", "updated_at": "2026-10-21",
            "project_id": project.uuidString,
            "replay": ["text": "Revisa", "documents": [["name": "nota.txt", "content": "Dato"]],
                       "image_name": "imagen.png"]
        ])
        let replay = try ChatReplayDraft(data)
        XCTAssertEqual(replay.conversation.id, id)
        XCTAssertEqual(replay.conversation.projectID, project)
        XCTAssertEqual(replay.attachments.first?.document?.content, "Dato")
        XCTAssertEqual(replay.missingImage, "imagen.png")
        let malformed = try JSONSerialization.data(withJSONObject: [
            "conversation_id": id.uuidString, "updated_at": "2026-10-21", "replay": ["text": "Hola"]
        ])
        XCTAssertThrowsError(try ChatReplayDraft(malformed))
    }

    func testPersistedHistoryRetainsSequenceFeedbackAndArchiveState() async throws {
        let id = UUID()
        let turn = UUID()
        let data = try JSONSerialization.data(withJSONObject: [
            "conversation_id": id.uuidString,
            "turns": [["conversation_id": id.uuidString, "turn_id": turn.uuidString,
                       "role": "assistant", "content": "Respuesta", "sequence": 8,
                       "owner_feedback": "helpful"]]
        ])
        let page = try ChatHistoryPage(data)
        XCTAssertEqual(page.messages[0].sequence, 8)
        XCTAssertEqual(page.messages[0].feedback, "helpful")
        var transcript = TextChatTranscript()
        transcript.restore(page.messages)
        transcript.setFeedback("unhelpful", messageID: turn)
        XCTAssertEqual(transcript.messages[0].feedback, "unhelpful")
        transcript.setFeedback(nil, messageID: turn)
        XCTAssertNil(transcript.messages[0].feedback)
    }

    func testRecoveryPreservesRequestAndClearsItWhenLeavingChat() async {
        let chat = JarvisChatStore()
        chat.conversationID = UUID()
        chat.activeSubmission = ChatSubmission(text: "Pregunta", attachments: [], planMode: true)
        chat.recordFailure("remote_authentication_failed")
        XCTAssertTrue(chat.retryAvailable)
        XCTAssertTrue(chat.error?.contains("clave") == true)
        XCTAssertEqual(chat.retrySubmission?.text, "Pregunta")
        XCTAssertEqual(chat.retrySubmission?.planMode, true)
        chat.recordFailure("job_status_unavailable")
        XCTAssertFalse(chat.retryAvailable, "Un resultado incierto no debe repetir acciones")
        chat.recordFailure("remote_provider_unavailable")
        XCTAssertTrue(chat.retryAvailable)
        chat.newConversation()
        XCTAssertNil(chat.retrySubmission)
        XCTAssertFalse(chat.retryAvailable)
        XCTAssertFalse(ChatRecovery.canRetry("security_compromised"))
        XCTAssertFalse(ChatRecovery.canRetry("approved_tool_execution_failed"))
    }

    func testScheduledReviewRejectsExpiredOrMalformedLinks() async {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let info: [AnyHashable: Any] = [
            "job_id": UUID().uuidString, "conversation_id": UUID().uuidString,
            "review_expires_at": ISO8601DateFormatter().string(from: now.addingTimeInterval(60))
        ]
        XCTAssertNotNil(ScheduledReviewLink(info, now: now))
        XCTAssertNil(ScheduledReviewLink(info, now: now.addingTimeInterval(61)))
        var malformed = info
        malformed["job_id"] = "invalid"
        XCTAssertNil(ScheduledReviewLink(malformed, now: now))
        malformed = info
        malformed.removeValue(forKey: "conversation_id")
        XCTAssertNil(ScheduledReviewLink(malformed, now: now))
    }
}

@MainActor
private final class CompletionPause {
    private var paused = false
    private var continuation: CheckedContinuation<Void, Never>?
    private var arrival: CheckedContinuation<Void, Never>?
    func suspend() async {
        paused = true
        arrival?.resume(); arrival = nil
        await withCheckedContinuation { continuation = $0 }
    }
    func waitUntilPaused() async {
        if !paused { await withCheckedContinuation { arrival = $0 } }
    }
    func resume() { continuation?.resume(); continuation = nil }
}

extension ChatMessageActionsTests {
    func testCompletionWaitsForHistoryBeforeSendingQueuedTurn() async {
        let chat = JarvisChatStore()
        let pause = CompletionPause()
        var calls: [String] = []
        let revision = chat.transcriptRevision
        let completion = Task {
            await chat.synchronizeCompletion(persisted: true, revision: revision,
                refreshRecent: { calls.append("recent"); await pause.suspend() },
                refreshHistory: { calls.append("history") }, sendNext: { calls.append("next") })
        }
        await pause.waitUntilPaused()
        XCTAssertEqual(calls, ["recent"])
        pause.resume()
        await completion.value
        XCTAssertEqual(calls, ["recent", "history", "next"])
    }

    func testUnpersistedResponseIsNotReplacedAndStaleCompletionCannotSendQueue() async {
        let chat = JarvisChatStore()
        chat.transcript.restore([.init(role: .assistant, text: "Respuesta local")])
        var calls: [String] = []
        await chat.synchronizeCompletion(persisted: false, revision: chat.transcriptRevision,
            refreshRecent: { chat.transcript.restore([]); calls.append("recent") },
            refreshHistory: { calls.append("history") }, sendNext: { calls.append("next") })
        XCTAssertEqual(chat.transcript.messages.last?.text, "Respuesta local")
        XCTAssertEqual(calls, ["history", "next"])
        let pause = CompletionPause()
        calls = []
        let revision = chat.transcriptRevision
        let completion = Task {
            await chat.synchronizeCompletion(persisted: true, revision: revision,
                refreshRecent: { await pause.suspend() }, refreshHistory: {},
                sendNext: { calls.append("next") })
        }
        await pause.waitUntilPaused()
        chat.newConversation()
        pause.resume()
        await completion.value
        XCTAssertTrue(calls.isEmpty)
    }

    func testStatusParsesPersistenceAsStrictBoolean() async {
        for value in [true, false] {
            let response = LocalIPCResponse(requestID: UUID(), ok: true, payload: [
                "job_id": UUID().uuidString, "status": "completed", "result": "Respuesta",
                "conversation_persisted": value
            ], errorCode: nil)
            XCTAssertEqual(IPCJobStatusEvent(response: response)?.conversationPersisted, value)
        }
        let invalid = LocalIPCResponse(requestID: UUID(), ok: true, payload: [
            "job_id": UUID().uuidString, "status": "completed", "result": "Respuesta",
            "conversation_persisted": 1
        ], errorCode: nil)
        XCTAssertNil(IPCJobStatusEvent(response: invalid))
    }
}
