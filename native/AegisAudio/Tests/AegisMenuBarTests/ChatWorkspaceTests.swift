import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class ChatWorkspaceTests: XCTestCase {
    private func project(_ name: String) -> JarvisProject {
        JarvisProject(project_id: UUID().uuidString.lowercased(), name: name, color: "clay",
                      icon: "folder", instructions: "Instrucciones sintéticas", files: [], chats: 0)
    }

    private func document() -> ChatAttachment {
        ChatAttachment(document: .init(name: "prueba.txt", content: "Contenido sintético"))
    }

    private func conversation(_ id: UUID, project: JarvisProject?) -> ChatConversationSummary {
        ChatConversationSummary(id: id, title: "Conversación sintética",
                                updatedAt: "2026-10-22T17:00:00Z", archived: false,
                                projectID: project.flatMap { UUID(uuidString: $0.id) })
    }

    func testEveryStarterOnlyPreparesAnUnsentDraftAndKeepsAttachments() {
        for starter in JarvisChatStarter.allCases {
            let chat = JarvisChatStore()
            let file = document()
            chat.attachments = [file]
            let revision = chat.transcriptRevision

            XCTAssertTrue(chat.prepareStarter(starter))

            XCTAssertEqual(chat.composerDraft, starter.prompt)
            XCTAssertFalse((chat.composerDraft ?? "").isEmpty)
            XCTAssertEqual(chat.planMode, starter == .plan)
            XCTAssertEqual(chat.attachments, [file])
            XCTAssertTrue(chat.transcript.messages.isEmpty)
            XCTAssertNil(chat.transcript.jobID)
            XCTAssertNil(chat.conversationID)
            XCTAssertNil(chat.activeSubmission)
            XCTAssertFalse(chat.submitting)
            XCTAssertEqual(chat.transcriptRevision, revision)
        }
    }

    func testStarterNeverOverwritesOwnersDraftOrChangesItsMode() {
        for starter in JarvisChatStarter.allCases {
            for planMode in [false, true] {
                let chat = JarvisChatStore()
                chat.composerDraft = "  Mi borrador sin enviar  "
                chat.planMode = planMode
                let file = document()
                chat.attachments = [file]

                XCTAssertFalse(chat.prepareStarter(starter))

                XCTAssertEqual(chat.composerDraft, "  Mi borrador sin enviar  ")
                XCTAssertEqual(chat.planMode, planMode)
                XCTAssertEqual(chat.attachments, [file])
                XCTAssertTrue(chat.transcript.messages.isEmpty)
            }
        }
    }

    func testStarterRefusesSubmittingActiveJobLoadingAndImportingStates() {
        let states: [(String, @MainActor (JarvisChatStore) -> Void)] = [
            ("submitting", { $0.submitting = true }),
            ("active job", { $0.transcript.attach(UUID()) }),
            ("loading conversation", { $0.selectionLoading = true }),
            ("importing", { $0.importing = true }),
        ]
        for (name, configure) in states {
            let chat = JarvisChatStore()
            chat.composerDraft = " \n "
            let file = document()
            chat.attachments = [file]
            configure(chat)
            let job = chat.transcript.jobID

            XCTAssertFalse(chat.prepareStarter(.plan), name)

            XCTAssertEqual(chat.composerDraft, " \n ", name)
            XCTAssertFalse(chat.planMode, name)
            XCTAssertEqual(chat.attachments, [file], name)
            XCTAssertEqual(chat.transcript.jobID, job, name)
            XCTAssertTrue(chat.transcript.messages.isEmpty, name)
        }
    }

    func testStarterRefusesExistingTranscriptAndKeepsChosenPlanMode() {
        let chat = JarvisChatStore()
        chat.transcript.restore([.init(role: .user, text: "Conversación que ya empezó")])
        let messages = chat.transcript.messages
        XCTAssertFalse(chat.prepareStarter(.plan))
        XCTAssertNil(chat.composerDraft)
        XCTAssertEqual(chat.transcript.messages, messages)
        XCTAssertFalse(chat.planMode)

        let blank = JarvisChatStore()
        blank.planMode = true
        blank.composerDraft = " \t\n "
        XCTAssertTrue(blank.prepareStarter(.organize))
        XCTAssertTrue(blank.planMode, "Una sugerencia normal conserva el modo elegido por el dueño")
    }

    func testChoosingAndClearingNewChatProjectPreservesDraftAndExactAttachments() {
        let chat = JarvisChatStore()
        let first = project("Proyecto A"), second = project("Proyecto B")
        chat.projects = [first, second]
        chat.composerDraft = "Borrador con detalles que deben conservarse"
        let file = document()
        chat.attachments = [file]
        chat.planMode = true
        let revision = chat.transcriptRevision

        for chosen in [first.id, second.id, nil] as [String?] {
            XCTAssertTrue(chat.canChooseWorkspaceProject)
            XCTAssertTrue(chat.chooseWorkspaceProject(chosen))
            XCTAssertEqual(chat.newChatProjectID, chosen)
            XCTAssertEqual(chat.workspaceProject?.id, chosen)
            XCTAssertEqual(chat.composerDraft, "Borrador con detalles que deben conservarse")
            XCTAssertEqual(chat.attachments, [file])
            XCTAssertTrue(chat.planMode)
            XCTAssertTrue(chat.transcript.messages.isEmpty)
            XCTAssertNil(chat.conversationID)
            XCTAssertNil(chat.activeSubmission)
            XCTAssertEqual(chat.transcriptRevision, revision)
        }
    }

    func testProjectSelectionRejectsUnknownTemporaryExistingAndBusyChats() {
        let states: [(String, @MainActor (JarvisChatStore) -> Void)] = [
            ("temporary", { $0.temporary = true }),
            ("existing identity", { $0.conversationID = UUID() }),
            ("existing transcript", { $0.transcript.restore([.init(role: .user, text: "Ya enviado")]) }),
            ("submitting", { $0.submitting = true }),
            ("active job", { $0.transcript.attach(UUID()) }),
            ("loading conversation", { $0.selectionLoading = true }),
            ("importing", { $0.importing = true }),
        ]
        for (name, configure) in states {
            let chat = JarvisChatStore()
            let current = project("Actual"), other = project("Otro")
            chat.projects = [current, other]
            chat.newChatProjectID = current.id
            chat.composerDraft = "Conservar"
            let file = document()
            chat.attachments = [file]
            configure(chat)

            XCTAssertFalse(chat.canChooseWorkspaceProject, name)
            XCTAssertFalse(chat.chooseWorkspaceProject(other.id), name)
            XCTAssertFalse(chat.chooseWorkspaceProject(nil), name)
            XCTAssertEqual(chat.newChatProjectID, current.id, name)
            XCTAssertEqual(chat.composerDraft, "Conservar", name)
            XCTAssertEqual(chat.attachments, [file], name)
        }

        let chat = JarvisChatStore()
        let existing = project("Existente")
        chat.projects = [existing]
        chat.newChatProjectID = existing.id
        XCTAssertFalse(chat.chooseWorkspaceProject(UUID().uuidString.lowercased()))
        XCTAssertEqual(chat.newChatProjectID, existing.id)
    }

    func testWorkspaceUsesSelectedConversationProjectInsteadOfStaleNewChatProject() {
        let chat = JarvisChatStore()
        let stale = project("Contexto anterior"), selected = project("Contexto seleccionado")
        chat.projects = [stale, selected]
        chat.newChatProjectID = stale.id
        let id = UUID()
        chat.conversationID = id
        chat.selectedConversation = conversation(id, project: selected)
        // An older listing must not override the explicitly selected record.
        chat.conversations = [conversation(id, project: stale)]
        XCTAssertEqual(chat.workspaceProject, selected)

        chat.selectedConversation = conversation(id, project: nil)
        XCTAssertNil(chat.workspaceProject, "Un chat sin proyecto no hereda el contexto anterior")

        chat.selectedConversation = nil
        chat.conversations = [conversation(id, project: selected)]
        XCTAssertEqual(chat.workspaceProject, selected)
        chat.temporary = true
        XCTAssertNil(chat.workspaceProject, "Un chat temporal no muestra contexto de proyecto")
    }

    func testUnknownOrDeletedConversationProjectCannotFallBackToNewChatContext() {
        let chat = JarvisChatStore()
        let stale = project("Contexto anterior"), deleted = project("Proyecto eliminado")
        chat.projects = [stale]
        chat.newChatProjectID = stale.id
        let id = UUID()
        chat.conversationID = id
        chat.selectedConversation = conversation(id, project: deleted)
        XCTAssertNil(chat.workspaceProject)

        chat.selectedConversation = conversation(UUID(), project: stale)
        XCTAssertNil(chat.workspaceProject, "Una selección antigua no pertenece al chat actual")
        chat.selectedConversation = nil
        XCTAssertNil(chat.workspaceProject, "Un chat aún sin cargar tampoco hereda el proyecto anterior")
    }
}
