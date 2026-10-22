import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class ChatDraftRetentionTests: XCTestCase {
    private func show(_ id: UUID, in chat: JarvisChatStore) throws {
        let conversation = ChatConversationSummary(id: id, title: "Chat sintético",
            updatedAt: "2026-10-22", archived: false)
        let page = try ChatHistoryPage(JSONSerialization.data(withJSONObject: [
            "conversation_id": id.uuidString, "turns": [],
        ]))
        try chat.restoreConversation(conversation, page: page)
    }

    private func document(_ text: String = "Contenido sintético") -> ChatAttachment {
        ChatAttachment(document: .init(name: "prueba.txt", content: text))
    }

    func testSwitchingBetweenChatsRestoresTextAndExactAttachments() async throws {
        let chat = JarvisChatStore()
        let a = UUID(), b = UUID()
        let image = ChatAttachment(name: "prueba.png", content: .image(try LocalImageAttachment(
            mediaType: "image/png", data: Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        )))
        let files = [document(), image]
        try show(a, in: chat)
        chat.composerDraft = "Borrador A sin enviar"
        chat.attachments = files
        try show(b, in: chat)
        XCTAssertNil(chat.composerDraft)
        XCTAssertTrue(chat.attachments.isEmpty)
        chat.composerDraft = "Borrador B"
        try show(a, in: chat)
        XCTAssertEqual(chat.composerDraft, "Borrador A sin enviar")
        XCTAssertEqual(chat.attachments, files)
        chat.attachments.removeAll { $0.id == image.id }
        chat.composerDraft = nil
        try show(b, in: chat)
        XCTAssertEqual(chat.composerDraft, "Borrador B")
        try show(a, in: chat)
        XCTAssertNil(chat.composerDraft)
        XCTAssertEqual(chat.attachments, [files[0]], "Un adjunto quitado no debe reaparecer")
    }

    func testNewChatStartsEmptyAndLeavingTemporaryChatDiscardsItsDraft() async throws {
        let chat = JarvisChatStore()
        let saved = UUID(), temporary = UUID()
        try show(saved, in: chat)
        chat.composerDraft = "Conservar"
        chat.attachments = [document()]
        chat.newConversation()
        XCTAssertNil(chat.composerDraft)
        XCTAssertTrue(chat.attachments.isEmpty)
        chat.composerDraft = "Nuevo chat aún sin enviar"
        chat.newConversation()
        XCTAssertNil(chat.composerDraft, "Nuevo chat siempre empieza vacío")
        try show(saved, in: chat)
        XCTAssertEqual(chat.composerDraft, "Conservar")
        XCTAssertEqual(chat.attachments.count, 1)

        chat.newTemporaryConversation()
        chat.conversationID = temporary
        chat.adoptTemporaryConversation(temporary)
        chat.composerDraft = "Temporal"
        chat.attachments = [document()]
        try show(saved, in: chat)
        XCTAssertEqual(chat.composerDraft, "Conservar")
        XCTAssertEqual(chat.abandonedTemporaryChats, [temporary])
        try show(temporary, in: chat)
        XCTAssertNil(chat.composerDraft)
        XCTAssertTrue(chat.attachments.isEmpty)
    }

    func testVoiceConversationAdoptionCannotExposeAnotherChatsDraft() async throws {
        let chat = JarvisChatStore()
        let textChat = UUID(), voiceChat = UUID()
        let file = document()
        try show(textChat, in: chat)
        chat.composerDraft = "Borrador del chat de texto"
        chat.attachments = [file]

        chat.adoptVoiceConversation(voiceChat, requestedConversationID: voiceChat)
        XCTAssertEqual(chat.conversationID, voiceChat)
        XCTAssertNil(chat.composerDraft)
        XCTAssertTrue(chat.attachments.isEmpty)
        chat.composerDraft = "Borrador del chat de voz"
        let revision = chat.transcriptRevision
        chat.adoptVoiceConversation(voiceChat, requestedConversationID: voiceChat)
        XCTAssertEqual(chat.composerDraft, "Borrador del chat de voz")
        XCTAssertEqual(chat.transcriptRevision, revision, "Continuar la misma voz no reemplaza el chat")

        chat.adoptVoiceConversation(textChat, requestedConversationID: textChat)
        XCTAssertEqual(chat.composerDraft, "Borrador del chat de texto")
        XCTAssertEqual(chat.attachments, [file])
        chat.adoptVoiceConversation(voiceChat, requestedConversationID: voiceChat)
        XCTAssertEqual(chat.composerDraft, "Borrador del chat de voz")
        XCTAssertTrue(chat.attachments.isEmpty)
    }

    func testFirstVoiceConversationIdentityPreservesNewChatsComposer() async {
        let chat = JarvisChatStore()
        let conversation = UUID()
        let file = document()
        chat.composerDraft = "Borrador del chat nuevo"
        chat.attachments = [file]
        let revision = chat.transcriptRevision

        chat.adoptVoiceConversation(conversation, requestedConversationID: nil)

        XCTAssertEqual(chat.conversationID, conversation)
        XCTAssertEqual(chat.composerDraft, "Borrador del chat nuevo")
        XCTAssertEqual(chat.attachments, [file])
        XCTAssertEqual(chat.transcriptRevision, revision)
        XCTAssertTrue(chat.abandonedTemporaryChats.isEmpty)
    }

    func testResumingVoiceConversationDoesNotTakeTheUnidentifiedChatsDraft() async throws {
        for hasRetainedDraft in [false, true] {
            let chat = JarvisChatStore()
            let resumed = UUID()
            let previousFile = document("Documento del chat reanudado")
            if hasRetainedDraft {
                try show(resumed, in: chat)
                chat.composerDraft = "Borrador del chat reanudado"
                chat.attachments = [previousFile]
                chat.newConversation()
            }
            chat.composerDraft = "Borrador inicial sin chat seleccionado"
            chat.attachments = [document("Adjunto inicial")]
            XCTAssertNil(chat.conversationID)

            chat.adoptVoiceConversation(resumed, requestedConversationID: resumed)

            XCTAssertEqual(chat.conversationID, resumed)
            XCTAssertEqual(chat.composerDraft, hasRetainedDraft ? "Borrador del chat reanudado" : nil)
            XCTAssertEqual(chat.attachments, hasRetainedDraft ? [previousFile] : [])
        }
    }

    func testSubmittedTextAndAttachmentsDoNotReturnAfterChangingChats() async throws {
        let chat = JarvisChatStore()
        let a = UUID(), b = UUID()
        try show(a, in: chat)
        chat.composerDraft = "  Enviar ahora  "
        let submitted = [document()]
        chat.attachments = submitted
        try show(b, in: chat)
        try show(a, in: chat)

        XCTAssertTrue(chat.beginSubmission(.init(text: "Enviar ahora", attachments: submitted,
                                                planMode: false), consumingComposerText: "Enviar ahora"))
        chat.consumeSubmittedAttachments(submitted)
        chat.transcript.finish("Respuesta sintética")
        try show(b, in: chat)
        try show(a, in: chat)

        XCTAssertNil(chat.composerDraft)
        XCTAssertTrue(chat.attachments.isEmpty)
    }

    func testSubmissionKeepsNewerComposerTextAndUnsubmittedAttachment() async throws {
        let chat = JarvisChatStore()
        let a = UUID(), b = UUID()
        try show(a, in: chat)
        let sent = document("Ya enviado")
        let next = ChatAttachment(document: .init(name: "siguiente.txt", content: "Siguiente"))
        chat.composerDraft = "Texto posterior"
        chat.attachments = [sent, next]
        chat.consumeSubmittedDraft("Texto enviado")
        chat.consumeSubmittedAttachments([sent])
        try show(b, in: chat)
        try show(a, in: chat)
        XCTAssertEqual(chat.composerDraft, "Texto posterior")
        XCTAssertEqual(chat.attachments, [next])
    }

    func testAutomaticSubmissionDoesNotConsumeIdenticalNewDraft() async throws {
        let chat = JarvisChatStore()
        let a = UUID(), b = UUID()
        try show(a, in: chat)
        chat.composerDraft = "El mismo texto"
        let submission = ChatSubmission(text: "El mismo texto", attachments: [], planMode: false)
        XCTAssertTrue(chat.beginSubmission(submission, consumingComposerText: nil))
        chat.transcript.finish("Respuesta sintética")
        try show(b, in: chat)
        try show(a, in: chat)
        XCTAssertEqual(chat.composerDraft, "El mismo texto")
    }

    func testQueuedMessageIsConsumedWithoutClearingTheFollowingDraft() async throws {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        let a = UUID(), b = UUID(), job = UUID()
        try show(a, in: model.chat)
        _ = model.chat.transcript.begin("Primera solicitud sintética")
        model.chat.transcript.attach(job)
        model.chat.composerDraft = "Mensaje en cola"
        XCTAssertTrue(model.queueChatMessage("Mensaje en cola"))
        XCTAssertNil(model.chat.composerDraft)
        XCTAssertEqual(model.queuedChatMessages, ["Mensaje en cola"])
        model.chat.composerDraft = "Otro borrador"
        model.chat.consumeSubmittedDraft("Mensaje en cola")
        model.chat.transcript.finish("Respuesta sintética", jobID: job)
        try show(b, in: model.chat)
        try show(a, in: model.chat)
        XCTAssertEqual(model.chat.composerDraft, "Otro borrador")
        XCTAssertNil(model.ipcSecret)
    }

    func testImplementingCompletedPlanDoesNotSendOrRemoveNewDraftAttachments() async throws {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        let origin = UUID(), other = UUID()
        try show(origin, in: model.chat)
        XCTAssertTrue(model.chat.transcript.begin("Prepara un plan sintético"))
        model.chat.transcript.finish("1. Crear una nota sintética")
        let newFile = document("Solo para el borrador posterior al plan")
        model.chat.composerDraft = "Analiza después este archivo"
        model.chat.attachments = [newFile]
        model.chat.planMode = false
        let request = "Implementa este plan:\n1. Crear una nota sintética"

        let accepted = await model.sendChatMessage(request) { submission, consumed in
            XCTAssertEqual(submission.text, request)
            XCTAssertTrue(submission.attachments.isEmpty)
            XCTAssertNil(submission.payload(conversationID: origin)["documents"])
            XCTAssertNil(submission.payload(conversationID: origin)["image"])
            XCTAssertNil(consumed)
            XCTAssertTrue(model.chat.beginSubmission(submission, consumingComposerText: consumed))
            model.chat.consumeSubmittedAttachments(submission.attachments)
            model.chat.transcript.finish("Implementación sintética finalizada")
            return true
        }

        XCTAssertTrue(accepted)
        XCTAssertEqual(model.chat.composerDraft, "Analiza después este archivo")
        XCTAssertEqual(model.chat.attachments, [newFile])
        try show(other, in: model.chat)
        try show(origin, in: model.chat)
        XCTAssertEqual(model.chat.composerDraft, "Analiza después este archivo")
        XCTAssertEqual(model.chat.attachments, [newFile])
        XCTAssertNil(model.ipcSecret)
    }

    func testSendingQueuedTurnDoesNotCaptureNewDraftAttachments() async throws {
        for accepted in [false, true] {
            let model = MenuBarModel()
            model.wakeWordOptedIn = false
            model.daemonState = .online
            model.securityState = .intact
            model.providerState = .configured
            let origin = UUID(), job = UUID()
            try show(origin, in: model.chat)
            XCTAssertTrue(model.chat.transcript.begin("Primera solicitud sintética"))
            model.chat.transcript.attach(job)
            XCTAssertTrue(model.queueChatMessage("Solicitud en cola"))
            model.chat.transcript.finish("Primera respuesta sintética", jobID: job)
            let newFile = document("Archivo del borrador nuevo")
            model.chat.composerDraft = "Siguiente borrador"
            model.chat.attachments = [newFile]
            var sends = 0

            await model.sendNextQueuedChatMessage { text in
                await model.sendChatMessage(text) { submission, consumed in
                    sends += 1
                    XCTAssertEqual(submission.text, "Solicitud en cola")
                    XCTAssertTrue(submission.attachments.isEmpty)
                    XCTAssertNil(submission.payload(conversationID: origin)["documents"])
                    XCTAssertNil(consumed)
                    if accepted {
                        XCTAssertTrue(model.chat.beginSubmission(submission, consumingComposerText: consumed))
                        model.chat.consumeSubmittedAttachments(submission.attachments)
                        model.chat.transcript.finish("Respuesta de cola sintética")
                    }
                    return accepted
                }
            }

            XCTAssertEqual(sends, 1)
            XCTAssertEqual(model.queuedChatMessages, accepted ? [] : ["Solicitud en cola"])
            XCTAssertEqual(model.chat.composerDraft, "Siguiente borrador")
            XCTAssertEqual(model.chat.attachments, [newFile])
            XCTAssertNil(model.ipcSecret)
        }
    }

    func testQueueCannotSilentlyOmitAttachmentsAlreadyInComposer() async {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        XCTAssertTrue(model.chat.transcript.begin("Solicitud sintética en marcha"))
        model.chat.transcript.attach(UUID())
        let file = document("Adjunto que aún debe enviarse")
        model.chat.composerDraft = "Petición con archivo"
        model.chat.attachments = [file]
        XCTAssertFalse(model.canQueueChatMessage)
        XCTAssertFalse(model.queueChatMessage("Petición con archivo"))
        XCTAssertTrue(model.queuedChatMessages.isEmpty)
        XCTAssertEqual(model.chat.composerDraft, "Petición con archivo")
        XCTAssertEqual(model.chat.attachments, [file])
        model.chat.attachments = []
        model.chat.importing = true
        XCTAssertFalse(model.queueChatMessage("Petición con archivo"))
        XCTAssertEqual(model.chat.composerDraft, "Petición con archivo")
        XCTAssertTrue(model.queuedChatMessages.isEmpty)
        XCTAssertNil(model.ipcSecret)
    }

    func testQuickEntryAndInteractionTextCannotBorrowComposerAttachments() async throws {
        for request in ["Pregunta desde entrada rápida", "Respuesta a una aclaración"] {
            let model = MenuBarModel()
            model.wakeWordOptedIn = false
            model.chat.newConversation()
            let newFile = document("Adjunto de otro borrador")
            model.chat.composerDraft = request
            model.chat.attachments = [newFile]
            let accepted = await model.sendChatMessage(request) { submission, consumed in
                XCTAssertTrue(submission.attachments.isEmpty)
                XCTAssertEqual(submission.method, "swarm.submit")
                XCTAssertNil(consumed)
                XCTAssertTrue(model.chat.beginSubmission(submission, consumingComposerText: consumed))
                model.chat.consumeSubmittedAttachments(submission.attachments)
                model.chat.transcript.finish("Respuesta sintética")
                return true
            }
            XCTAssertTrue(accepted)
            XCTAssertEqual(model.chat.composerDraft, request)
            XCTAssertEqual(model.chat.attachments, [newFile])
            XCTAssertNil(model.ipcSecret)
        }
    }

    func testComposerSubmissionStillOwnsAndConsumesItsExplicitAttachments() async {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        let file = document("Archivo que sí debe enviarse")
        model.chat.attachments = [file]
        let accepted = await model.sendChatMessage("", consumingComposer: true) { submission, consumed in
            XCTAssertFalse(submission.text.isEmpty)
            XCTAssertEqual(submission.attachments, [file])
            XCTAssertNotNil(submission.payload(conversationID: UUID())["documents"])
            XCTAssertEqual(consumed, "")
            XCTAssertTrue(model.chat.beginSubmission(submission, consumingComposerText: consumed))
            model.chat.consumeSubmittedAttachments(submission.attachments)
            model.chat.transcript.finish("Respuesta sintética")
            return true
        }
        XCTAssertTrue(accepted)
        XCTAssertTrue(model.chat.attachments.isEmpty)
        XCTAssertNil(model.ipcSecret)
    }

    func testEditingForkKeepsOriginalDraftAndRejectedRegenerationKeepsForkDraft() async throws {
        let chat = JarvisChatStore()
        let original = UUID(), fork = UUID()
        let originalFile = document()
        try show(original, in: chat)
        chat.composerDraft = "Borrador del original"
        chat.attachments = [originalFile]
        try show(fork, in: chat)
        let replayFile = document("Documento del mensaje editado")
        chat.attachments = [replayFile]
        await chat.sendReplayPreservingDraft("Mensaje para regenerar") { false }
        try show(original, in: chat)
        XCTAssertEqual(chat.composerDraft, "Borrador del original")
        XCTAssertEqual(chat.attachments, [originalFile])
        try show(fork, in: chat)
        XCTAssertEqual(chat.composerDraft, "Mensaje para regenerar")
        XCTAssertEqual(chat.attachments, [replayFile])
    }

    func testDeletingCurrentOrInactiveConversationCannotReviveItsDraft() async throws {
        for active in [false, true] {
            let chat = JarvisChatStore()
            let deleted = UUID(), other = UUID()
            try show(deleted, in: chat)
            chat.composerDraft = "Borrar junto al chat"
            chat.attachments = [document()]
            if !active { try show(other, in: chat) }

            chat.discardDraft(for: deleted)
            if active { chat.resetConversation(preservingDraft: false) }
            try show(deleted, in: chat)

            XCTAssertNil(chat.composerDraft)
            XCTAssertTrue(chat.attachments.isEmpty)
        }
    }

    func testCountLimitEvictsOldestAndReadingCurrentChatDoesNotEvict() async throws {
        let chat = JarvisChatStore()
        chat.draftCache = ChatDraftCache(maximumCount: 2)
        let a = UUID(), b = UUID(), c = UUID(), d = UUID()
        for (id, text) in [(a, "A"), (b, "B"), (c, "C")] {
            try show(id, in: chat)
            chat.composerDraft = text
        }
        try show(c, in: chat)
        XCTAssertEqual(chat.composerDraft, "C")
        XCTAssertNil(chat.notice, "Leer el chat actual no debe gastar una plaza del caché")
        try show(a, in: chat)
        XCTAssertEqual(chat.composerDraft, "A", "Restaurar un borrador libera su plaza antes de guardar el saliente")
        try show(d, in: chat)
        XCTAssertTrue(chat.notice?.contains("menos recientes") == true)
        try show(b, in: chat)
        XCTAssertNil(chat.composerDraft, "B era el borrador menos reciente")
        try show(c, in: chat)
        XCTAssertEqual(chat.composerDraft, "C")
    }

    func testByteLimitCountsAttachmentContentAndNeverTruncatesDrafts() async throws {
        let chat = JarvisChatStore()
        let a = UUID(), b = UUID(), c = UUID()
        let file = document("contenido")
        let text = "éééé"
        let size = ChatComposerDraft(text: text, attachments: [file]).byteCount
        XCTAssertEqual(size, text.utf8.count + file.name.utf8.count + "contenido".utf8.count)
        chat.draftCache = ChatDraftCache(maximumBytes: size)
        try show(a, in: chat)
        chat.composerDraft = text
        chat.attachments = [file]
        try show(b, in: chat)
        chat.composerDraft = "B"
        try show(c, in: chat)
        XCTAssertTrue(chat.notice?.contains("menos recientes") == true)
        try show(a, in: chat)
        XCTAssertNil(chat.composerDraft)
        XCTAssertTrue(chat.attachments.isEmpty)
        try show(b, in: chat)
        XCTAssertEqual(chat.composerDraft, "B")
    }

    func testOversizedDraftIsExplicitlyDiscardedAndFreshStoreHasNoOldDrafts() async throws {
        let chat = JarvisChatStore()
        let a = UUID(), b = UUID()
        chat.draftCache = ChatDraftCache(maximumBytes: 4)
        try show(a, in: chat)
        chat.composerDraft = "demasiado grande"
        try show(b, in: chat)
        XCTAssertTrue(chat.notice?.contains("superaba el límite") == true)
        try show(a, in: chat)
        XCTAssertNil(chat.composerDraft)
        chat.composerDraft = "RAM"
        try show(b, in: chat)
        let fresh = JarvisChatStore()
        try show(a, in: fresh)
        XCTAssertNil(fresh.composerDraft)
        XCTAssertTrue(fresh.attachments.isEmpty)
    }

    func testMalformedHistoryDoesNotReplaceCurrentDraft() async throws {
        let chat = JarvisChatStore()
        let original = UUID(), incoming = UUID()
        try show(original, in: chat)
        chat.composerDraft = "No perder"
        chat.attachments = [document()]
        let summary = ChatConversationSummary(id: incoming, title: "Sintético",
            updatedAt: "2026-10-22", archived: false)
        let wrongPage = try ChatHistoryPage(JSONSerialization.data(withJSONObject: [
            "conversation_id": UUID().uuidString, "turns": [],
        ]))
        XCTAssertThrowsError(try chat.restoreConversation(summary, page: wrongPage))
        XCTAssertEqual(chat.conversationID, original)
        XCTAssertEqual(chat.composerDraft, "No perder")
        XCTAssertEqual(chat.attachments.count, 1)
    }

    func testSuspendedCancellationCannotSteerIntoAnotherConversationOrRevision() async throws {
        for changedConversation in [true, false] {
            let model = MenuBarModel()
            model.wakeWordOptedIn = false
            let original = UUID(), job = UUID()
            try show(original, in: model.chat)
            let revision = model.chat.transcriptRevision
            let file = document()
            model.chat.composerDraft = "Corrección anterior"
            model.chat.attachments = [file]
            _ = model.chat.transcript.begin("Solicitud original sintética")
            model.chat.transcript.attach(job)
            let pause = DraftCancellationPause()
            var sent: [String] = []
            let steering = Task {
                await model.steerChatResponse("Corrección anterior", cancel: {
                    model.chat.transcript.finish("Cancelado", jobID: job)
                    await pause.suspend()
                }, send: { sent.append($0); return true })
            }
            await pause.waitUntilPaused()
            if changedConversation {
                try show(UUID(), in: model.chat)
                model.chat.composerDraft = "Borrador del destino"
            } else { model.chat.transcriptRevision = UUID() }
            pause.resume()
            let accepted = await steering.value
            if !accepted {
                model.chat.restoreRejectedComposer("Corrección anterior",
                    conversationID: original, revision: revision)
            }
            XCTAssertFalse(accepted)
            XCTAssertTrue(sent.isEmpty)
            if changedConversation {
                XCTAssertEqual(model.chat.composerDraft, "Borrador del destino")
                XCTAssertTrue(model.chat.attachments.isEmpty)
                try show(original, in: model.chat)
            }
            XCTAssertEqual(model.chat.composerDraft, "Corrección anterior")
            XCTAssertEqual(model.chat.attachments, [file])
            XCTAssertNil(model.ipcSecret)
        }
    }

    func testSameChatCanBeSteeredAndRejectedCorrectionDoesNotOverwriteNewDraft() async throws {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        let conversation = UUID(), job = UUID()
        try show(conversation, in: model.chat)
        let revision = model.chat.transcriptRevision
        _ = model.chat.transcript.begin("Solicitud original sintética")
        model.chat.transcript.attach(job)
        var sent: [String] = []
        let accepted = await model.steerChatResponse("Corrección sintética", cancel: {
            model.chat.transcript.finish("Cancelado", jobID: job)
        }, send: { sent.append($0); return false })
        XCTAssertFalse(accepted)
        XCTAssertEqual(sent, ["Solicitud original sintética\n\n(Corrección: Corrección sintética)"])
        model.chat.restoreRejectedComposer("Corrección sintética",
            conversationID: conversation, revision: revision)
        XCTAssertEqual(model.chat.composerDraft, "Corrección sintética")
        model.chat.composerDraft = "Edición posterior"
        model.chat.restoreRejectedComposer("Corrección anterior",
            conversationID: conversation, revision: revision)
        XCTAssertEqual(model.chat.composerDraft, "Edición posterior")
    }

    func testCorrectionTextIsConsumedOnlyWhenItsCombinedSubmissionBegins() async {
        let chat = JarvisChatStore()
        chat.composerDraft = "Usa el título corregido"
        let submission = ChatSubmission(text: "Solicitud original\n\n(Corrección: Usa el título corregido)",
            attachments: [], planMode: false)
        XCTAssertTrue(chat.beginSubmission(submission, consumingComposerText: "Usa el título corregido"))
        XCTAssertNil(chat.composerDraft)
        chat.transcript.finish("Respuesta sintética")
        chat.composerDraft = "Texto posterior"
        XCTAssertTrue(chat.beginSubmission(submission, consumingComposerText: "Usa el título corregido"))
        XCTAssertEqual(chat.composerDraft, "Texto posterior")
    }

    func testSuspendedCorrectionKeepsNewerDraftInOrigin() async throws {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        let origin = UUID(), destination = UUID(), job = UUID()
        try show(origin, in: model.chat)
        let revision = model.chat.transcriptRevision
        model.chat.composerDraft = "Corrección anterior"
        _ = model.chat.transcript.begin("Solicitud sintética")
        model.chat.transcript.attach(job)
        let pause = DraftCancellationPause()
        var sent = false
        let steering = Task {
            await model.steerChatResponse("Corrección anterior", cancel: {
                model.chat.transcript.finish("Cancelado", jobID: job)
                await pause.suspend()
            }, send: { _ in sent = true; return true })
        }
        await pause.waitUntilPaused()
        try show(destination, in: model.chat)
        try show(origin, in: model.chat)
        model.chat.composerDraft = "Edición posterior en origen"
        try show(destination, in: model.chat)
        pause.resume()
        let accepted = await steering.value
        XCTAssertFalse(accepted)
        model.chat.restoreRejectedComposer("Corrección anterior", conversationID: origin, revision: revision)
        XCTAssertNil(model.chat.composerDraft)
        XCTAssertFalse(sent)
        try show(origin, in: model.chat)
        XCTAssertEqual(model.chat.composerDraft, "Edición posterior en origen")
    }

    func testSuspendedCorrectionDoesNotReviveTemporaryOrTrashedDraft() async throws {
        for temporary in [true, false] {
            let model = MenuBarModel()
            model.wakeWordOptedIn = false
            let origin = UUID(), job = UUID()
            try show(origin, in: model.chat)
            model.chat.temporary = temporary
            if temporary { model.chat.adoptTemporaryConversation(origin) }
            let revision = model.chat.transcriptRevision
            model.chat.composerDraft = "Corrección que se debe descartar al salir"
            model.chat.attachments = [document()]
            _ = model.chat.transcript.begin("Solicitud sintética")
            model.chat.transcript.attach(job)
            let pause = DraftCancellationPause()
            var sent = false
            let steering = Task {
                await model.steerChatResponse("Corrección que se debe descartar al salir", cancel: {
                    model.chat.transcript.finish("Cancelado", jobID: job)
                    await pause.suspend()
                }, send: { _ in sent = true; return true })
            }
            await pause.waitUntilPaused()
            if !temporary {
                model.chat.discardDraft(for: origin)
                model.chat.resetConversation(preservingDraft: false)
            }
            try show(UUID(), in: model.chat)
            pause.resume()
            let accepted = await steering.value
            XCTAssertFalse(accepted)
            model.chat.restoreRejectedComposer("Corrección anterior", conversationID: origin, revision: revision)
            try show(origin, in: model.chat)
            XCTAssertNil(model.chat.composerDraft)
            XCTAssertTrue(model.chat.attachments.isEmpty)
            XCTAssertFalse(sent)
        }
    }

    func testRetryUsesOriginalAttachmentsAndModeWithoutReplacingNewComposer() async throws {
        for accepted in [false, true] {
            let model = MenuBarModel()
            model.wakeWordOptedIn = false
            model.daemonState = .online
            model.securityState = .intact
            model.providerState = .configured
            let origin = UUID(), other = UUID()
            try show(origin, in: model.chat)
            let originalFile = document("Contenido del reintento")
            let newFile = document("Contenido del borrador nuevo")
            let retry = ChatSubmission(text: "Mismo texto", attachments: [originalFile], planMode: true)
            model.chat.activeSubmission = retry
            model.chat.recordFailure("remote_provider_unavailable")
            model.chat.composerDraft = "Mismo texto"
            model.chat.attachments = [newFile]
            model.chat.planMode = false
            var submitted: ChatSubmission?

            await model.retryChatMessage(refresh: {}, send: { submission in
                submitted = submission
                XCTAssertEqual(model.chat.attachments, [newFile])
                if accepted {
                    XCTAssertTrue(model.chat.beginSubmission(submission, consumingComposerText: nil))
                    model.chat.consumeSubmittedAttachments(submission.attachments)
                    model.chat.transcript.finish("Respuesta sintética")
                }
                return accepted
            })

            XCTAssertEqual(submitted?.attachments, [originalFile])
            XCTAssertEqual(submitted?.planMode, true)
            XCTAssertEqual(model.chat.composerDraft, "Mismo texto")
            XCTAssertEqual(model.chat.attachments, [newFile])
            XCTAssertFalse(model.chat.planMode)
            try show(other, in: model.chat)
            try show(origin, in: model.chat)
            XCTAssertEqual(model.chat.composerDraft, "Mismo texto")
            XCTAssertEqual(model.chat.attachments, [newFile])
            XCTAssertNil(model.ipcSecret)
        }
    }

    func testRetryRechecksConversationAfterSuspendedRefresh() async throws {
        let model = MenuBarModel()
        model.wakeWordOptedIn = false
        model.daemonState = .online
        model.securityState = .intact
        model.providerState = .configured
        let origin = UUID(), destination = UUID()
        try show(origin, in: model.chat)
        model.chat.activeSubmission = .init(text: "Solicitud anterior", attachments: [document()], planMode: true)
        model.chat.recordFailure("remote_provider_unavailable")
        let pause = DraftCancellationPause()
        var sent = false
        let retrying = Task {
            await model.retryChatMessage(refresh: { await pause.suspend() },
                send: { _ in sent = true; return true })
        }
        await pause.waitUntilPaused()
        try show(destination, in: model.chat)
        let destinationFile = document("Adjunto del destino")
        model.chat.composerDraft = "Borrador del destino"
        model.chat.attachments = [destinationFile]
        pause.resume()
        await retrying.value
        XCTAssertFalse(sent)
        XCTAssertEqual(model.chat.composerDraft, "Borrador del destino")
        XCTAssertEqual(model.chat.attachments, [destinationFile])
    }
}

@MainActor
private final class DraftCancellationPause {
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
