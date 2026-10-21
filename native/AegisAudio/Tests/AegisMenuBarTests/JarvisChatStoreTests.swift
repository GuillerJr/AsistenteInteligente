import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class JarvisChatStoreTests: XCTestCase {
    func testNewChatLeavesEveryPaneAndClearsConversationState() async {
        for pane in [JarvisChatStore.Pane.dot, .scheduled, .project("project")] {
            let chat = JarvisChatStore()
            chat.pane = pane
            chat.conversationID = UUID()
            chat.newChatProjectID = "old-project"
            chat.inputJobID = UUID()
            chat.nextBeforeSequence = 40
            chat.planReplies = [UUID()]
            chat.implementedPlans = chat.planReplies
            chat.planMode = true
            chat.error = "Error anterior"
            chat.notice = "Aviso anterior"
            chat.transcript.restore([.init(role: .user, text: "Anterior")])
            let revision = chat.transcriptRevision
            chat.newConversation()
            XCTAssertEqual(chat.pane, .chat)
            XCTAssertNil(chat.conversationID)
            XCTAssertNil(chat.newChatProjectID)
            XCTAssertNil(chat.inputJobID)
            XCTAssertNil(chat.nextBeforeSequence)
            XCTAssertTrue(chat.planReplies.isEmpty && chat.implementedPlans.isEmpty)
            XCTAssertTrue(chat.transcript.messages.isEmpty)
            XCTAssertNil(chat.error)
            XCTAssertNil(chat.notice)
            XCTAssertNotEqual(chat.transcriptRevision, revision)
            XCTAssertTrue(chat.hasExplicitConversationSelection)
            XCTAssertTrue(chat.planMode, "El modo elegido se conserva para el siguiente chat")
        }
    }

    func testBusyChatCannotBeReplacedOrAssignedToAnotherProject() async {
        for state in ["submitting", "job", "selection", "import"] {
            let chat = JarvisChatStore()
            chat.pane = .project("original")
            chat.newChatProjectID = "original"
            chat.submitting = state == "submitting"
            chat.selectionLoading = state == "selection"
            chat.importing = state == "import"
            if state == "job" { chat.transcript.attach(UUID()) }
            let revision = chat.transcriptRevision
            chat.newConversation(inProject: "another")
            chat.newTemporaryConversation()
            XCTAssertEqual(chat.transcriptRevision, revision)
            XCTAssertEqual(chat.newChatProjectID, "original")
            XCTAssertEqual(chat.pane, .project("original"))
            XCTAssertFalse(chat.temporary)
        }
    }

    func testTemporaryChatIsQueuedForDeletionExactlyOnceWhenLeaving() async {
        let chat = JarvisChatStore()
        let temporary = UUID()
        chat.newTemporaryConversation()
        chat.conversationID = temporary
        chat.adoptTemporaryConversation(temporary)
        chat.pane = .dot
        chat.newConversation(inProject: "work")
        XCTAssertFalse(chat.temporary)
        XCTAssertEqual(chat.abandonedTemporaryChats, [temporary])
        XCTAssertEqual(chat.newChatProjectID, "work")
        chat.newConversation()
        XCTAssertEqual(chat.abandonedTemporaryChats, [temporary])
        chat.adoptTemporaryConversation(UUID())
        chat.newConversation()
        XCTAssertEqual(chat.abandonedTemporaryChats, [temporary], "Un chat guardado no se borra")
    }

    func testUncreatedTemporaryChatDoesNotDeleteAnything() async {
        let chat = JarvisChatStore()
        chat.newTemporaryConversation()
        chat.newTemporaryConversation()
        XCTAssertTrue(chat.temporary)
        XCTAssertTrue(chat.abandonedTemporaryChats.isEmpty)
        XCTAssertNil(chat.newChatProjectID)
    }

    func testProjectLookupAndChatTitleCannotReuseDifferentConversation() async throws {
        let chat = JarvisChatStore()
        let project = JarvisProject(project_id: UUID().uuidString.lowercased(), name: "Pruebas",
            color: "clay", icon: "code", instructions: "Revisa", files: [], chats: 0)
        chat.projects = [project]
        XCTAssertEqual(chat.project(project.id), project)
        XCTAssertNil(chat.project(nil))
        XCTAssertNil(chat.project("otro"))
        chat.selectedConversation = try ChatConversationSummary.record(JSONSerialization.data(withJSONObject: [
            "conversation_id": UUID().uuidString, "title": "Otra conversación",
            "updated_at": "2026-10-21T15:30:00Z", "archived": false,
        ]))
        chat.conversationID = UUID()
        chat.transcript.restore([.init(role: .user, text: String(repeating: "x", count: 200))])
        XCTAssertNil(chat.currentConversation)
        XCTAssertEqual(chat.currentTitle.count, 128)
        chat.newConversation()
        XCTAssertEqual(chat.currentTitle, "Chat con Jarvis")
        XCTAssertEqual(chat.projects, [project])
    }

    func testAttentionPayloadIsBoundedAndMalformedItemsAreIgnored() async {
        let valid: [String: Any] = ["item_id": "task:1", "title": String(repeating: "a", count: 100),
            "reason": String(repeating: "b", count: 300), "action": "run_task",
            "conversation_id": "invalid"]
        let entries = JarvisChatStore.attentionEntries([[:]] + Array(repeating: valid, count: 30))
        XCTAssertEqual(entries.count, 19)
        XCTAssertEqual(entries.first?.title.count, 80)
        XCTAssertEqual(entries.first?.reason.count, 200)
        XCTAssertNil(entries.first?.conversationID)
    }
}
