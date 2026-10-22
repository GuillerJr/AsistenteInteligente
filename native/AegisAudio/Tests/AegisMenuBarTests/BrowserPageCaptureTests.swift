import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

@MainActor
final class BrowserPageCaptureTests: XCTestCase {
    private func attachment(_ name: String = "existente.txt") -> ChatAttachment {
        ChatAttachment(document: .init(name: name, content: "Contenido sintético"))
    }

    private func begin(
        _ capture: JarvisBrowserPageCapture, chat: JarvisChatStore,
        source: CapturePageSource, reader: CapturePausedReader
    ) -> Task<Void, Never> {
        let page = source.page
        guard let task = capture.start(page: page, to: chat, isCurrentPage: source.matches,
                                       readText: reader.read) else {
            XCTFail("La lectura sintética no comenzó")
            return Task {}
        }
        return task
    }

    private func readImmediately(_ capture: JarvisBrowserPageCapture, chat: JarvisChatStore,
                                 source: CapturePageSource,
                                 readText: @escaping @MainActor () async throws -> String?) async {
        await capture.start(page: source.page, to: chat, isCurrentPage: source.matches,
                            readText: readText)?.value
    }

    func testSuccessKeepsPageMetadataAndExistingAttachments() async throws {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let existing = attachment()
        chat.attachments = [existing]
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        XCTAssertTrue(capture.isReading)
        source.page = .init(revision: source.page.revision, url: source.page.url, title: "Título posterior")
        reader.finish(.success("Texto sintético de la página"))
        await task.value
        XCTAssertFalse(capture.isReading)
        XCTAssertEqual(chat.attachments.first, existing)
        let document = try XCTUnwrap(chat.attachments.last?.document)
        XCTAssertEqual(document.name, "Página inicial.txt")
        XCTAssertEqual(document.content, "Página: https://example.invalid/inicial\n\nTexto sintético de la página")
        XCTAssertNil(chat.error)
    }

    func testConversationSwitchDiscardsTextAndDoesNotTouchDestination() async {
        for returnToOrigin in [false, true] {
            let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
            let source = CapturePageSource(), reader = CapturePausedReader()
            let origin = UUID(), destination = UUID(), originalFile = attachment()
            chat.conversationID = origin
            chat.attachments = [originalFile]
            let task = begin(capture, chat: chat, source: source, reader: reader)
            await reader.waitUntilStarted()
            chat.switchConversation(to: destination)
            if returnToOrigin { chat.switchConversation(to: origin) }
            chat.error = "Estado del chat actual"
            let expectedFiles = chat.attachments
            reader.finish(.success("Texto del chat anterior"))
            await task.value
            XCTAssertEqual(chat.attachments, expectedFiles)
            XCTAssertEqual(chat.error, "Estado del chat actual")
            XCTAssertFalse(capture.isReading)
        }
    }

    func testNewUnidentifiedChatInvalidatesPreviousUnidentifiedChat() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        XCTAssertNil(chat.conversationID)
        let revision = chat.transcriptRevision
        chat.newConversation()
        XCTAssertNil(chat.conversationID)
        XCTAssertNotEqual(chat.transcriptRevision, revision)
        reader.finish(.success("Texto anterior"))
        await task.value
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertNil(chat.error)
    }

    func testNavigationAndSameURLReloadDiscardPendingRead() async {
        for sameURL in [false, true] {
            let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
            let source = CapturePageSource(), reader = CapturePausedReader()
            let task = begin(capture, chat: chat, source: source, reader: reader)
            await reader.waitUntilStarted()
            source.page = .init(revision: UUID(),
                url: sameURL ? source.page.url : URL(string: "https://example.invalid/otra")!,
                title: "Documento nuevo")
            reader.finish(.success("Texto del documento anterior"))
            await task.value
            XCTAssertTrue(chat.attachments.isEmpty)
            XCTAssertNil(chat.error)
        }
    }

    func testReturningToSameURLStillDiscardsOldDocument() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let originalURL = source.page.url
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        source.page = .init(revision: UUID(), url: URL(string: "https://example.invalid/otra")!, title: "Otra")
        source.page = .init(revision: UUID(), url: originalURL, title: "Página inicial")
        reader.finish(.success("Texto de la primera visita"))
        await task.value
        XCTAssertTrue(chat.attachments.isEmpty)
    }

    func testClosingPanelReleasesButtonAndOldReaderCannotFinishNewReader() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        capture.invalidate()
        XCTAssertFalse(capture.isReading)
        let newReader = CapturePausedReader()
        let newTask = begin(capture, chat: chat, source: source, reader: newReader)
        await newReader.waitUntilStarted()
        reader.finish(.success("Texto tras cerrar el panel"))
        await task.value
        XCTAssertTrue(capture.isReading, "El lector anterior no libera la nueva lectura")
        XCTAssertTrue(chat.attachments.isEmpty)
        newReader.finish(.success("Nueva lectura"))
        await newTask.value
        XCTAssertFalse(capture.isReading)
        XCTAssertEqual(chat.attachments.count, 1)
        XCTAssertTrue(chat.attachments[0].document?.content.hasSuffix("Nueva lectura") == true)
    }

    func testGestureCapturesContextBeforeTheTaskStarts() async {
        for closePanel in [false, true] {
            let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
            let source = CapturePageSource()
            var reads = 0
            let task = capture.start(page: source.page, to: chat, isCurrentPage: source.matches) {
                reads += 1
                return "No debe leerse"
            }
            XCTAssertTrue(capture.isReading)
            // Deliberately do not yield before changing context: the task has not run.
            if closePanel { capture.invalidate() } else { chat.newConversation() }
            await task?.value
            XCTAssertEqual(reads, 0)
            XCTAssertTrue(chat.attachments.isEmpty)
            XCTAssertNil(chat.error)
            XCTAssertFalse(capture.isReading)
        }
    }

    func testChatChangeCanFinishNewReadBeforeThePreviousReaderReturns() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), oldReader = CapturePausedReader()
        chat.conversationID = UUID()
        let oldTask = begin(capture, chat: chat, source: source, reader: oldReader)
        await oldReader.waitUntilStarted()
        chat.switchConversation(to: UUID())
        capture.invalidate() // The panel's conversation/revision onChange handler.
        let currentReader = CapturePausedReader()
        let currentTask = begin(capture, chat: chat, source: source, reader: currentReader)
        await currentReader.waitUntilStarted()
        currentReader.finish(.success("Texto del destino"))
        await currentTask.value
        let currentFiles = chat.attachments
        XCTAssertEqual(currentFiles.count, 1)
        oldReader.finish(.failure(CaptureReadError.synthetic))
        await oldTask.value
        XCTAssertEqual(chat.attachments, currentFiles)
        XCTAssertNil(chat.error)
        XCTAssertFalse(capture.isReading)
    }

    func testCancellationDiscardsReaderThatDoesNotCooperateWithCancellation() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        task.cancel()
        reader.finish(.success("Resultado tardío"))
        await task.value
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertNil(chat.error)
        XCTAssertFalse(capture.isReading)
    }

    func testDoubleClickStartsOnlyOneReader() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        var duplicateReads = 0
        await readImmediately(capture, chat: chat, source: source) {
            duplicateReads += 1
            return "Duplicado"
        }
        reader.finish(.success("Lectura única"))
        await task.value
        XCTAssertEqual(duplicateReads, 0)
        XCTAssertEqual(chat.attachments.count, 1)
        XCTAssertNil(chat.error)
    }

    func testLateErrorsAndEmptyTextDoNotOverwriteAnotherChatError() async {
        for throwsError in [false, true] {
            let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
            let source = CapturePageSource(), reader = CapturePausedReader()
            chat.conversationID = UUID()
            let task = begin(capture, chat: chat, source: source, reader: reader)
            await reader.waitUntilStarted()
            chat.switchConversation(to: UUID())
            chat.error = "Error del destino"
            reader.finish(throwsError ? .failure(CaptureReadError.synthetic) : .success(nil))
            await task.value
            XCTAssertEqual(chat.error, "Error del destino")
            XCTAssertTrue(chat.attachments.isEmpty)
        }
    }

    func testSamePageErrorsAreShownAndReleaseTheRead() async {
        for throwsError in [false, true] {
            let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
            let source = CapturePageSource(), reader = CapturePausedReader()
            let task = begin(capture, chat: chat, source: source, reader: reader)
            await reader.waitUntilStarted()
            reader.finish(throwsError ? .failure(CaptureReadError.synthetic) : .success(nil))
            await task.value
            XCTAssertNotNil(chat.error)
            XCTAssertFalse(capture.isReading)
            XCTAssertTrue(chat.attachments.isEmpty)
        }
    }

    func testAttachmentEditsDuringReadArePreservedWithoutRevivingRemovedOnes() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        let removed = attachment("retirado.txt"), added = attachment("nuevo.txt")
        chat.attachments = [removed]
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        chat.attachments = [added]
        reader.finish(.success("Texto sintético"))
        await task.value
        XCTAssertEqual(chat.attachments.count, 2)
        XCTAssertEqual(chat.attachments.first, added)
        XCTAssertFalse(chat.attachments.contains(removed))
    }

    func testAttachmentCapacityAndDuplicateNamesAreRevalidatedAfterReading() async {
        for fillCapacity in [false, true] {
            let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
            let source = CapturePageSource(), reader = CapturePausedReader()
            let task = begin(capture, chat: chat, source: source, reader: reader)
            await reader.waitUntilStarted()
            let files = fillCapacity ? (0..<4).map { attachment("\($0).txt") } : [attachment("Página inicial.txt")]
            chat.attachments = files
            reader.finish(.success("Texto sintético"))
            await task.value
            XCTAssertEqual(chat.attachments, files)
            XCTAssertNotNil(chat.error)
        }
    }

    func testCombinedTextBudgetIsPreserved() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource()
        let files = (0..<2).map { ChatAttachment(document: .init(name: "\($0).txt",
            content: String(repeating: "a", count: 30_000))) }
        chat.attachments = files
        await readImmediately(capture, chat: chat, source: source) { String(repeating: "b", count: 30_000) }
        XCTAssertEqual(chat.attachments, files)
        XCTAssertNotNil(chat.error)
        XCTAssertFalse(capture.isReading)
    }

    func testBusyChatCannotStartOrReceiveAPageRead() async {
        let capture = JarvisBrowserPageCapture(), chat = JarvisChatStore()
        let source = CapturePageSource(), reader = CapturePausedReader()
        chat.submitting = true
        var started = false
        await readImmediately(capture, chat: chat, source: source) {
            started = true
            return "No debe leerse"
        }
        XCTAssertFalse(started)
        chat.submitting = false
        let task = begin(capture, chat: chat, source: source, reader: reader)
        await reader.waitUntilStarted()
        chat.submitting = true
        reader.finish(.success("Lectura anterior al envío"))
        await task.value
        XCTAssertTrue(chat.attachments.isEmpty)
        XCTAssertFalse(capture.isReading)
    }

    func testNativeNavigationStartsInvalidateIdentityEvenWithoutAURLChange() async {
        let browser = JarvisBrowserStore(persistent: false)
        var revision = browser.documentRevision
        browser.webView(browser.webView, didStartProvisionalNavigation: nil)
        XCTAssertNotEqual(browser.documentRevision, revision)
        revision = browser.documentRevision
        browser.webView(browser.webView, didStartProvisionalNavigation: nil)
        XCTAssertNotEqual(browser.documentRevision, revision)
        revision = browser.documentRevision
        browser.stopLoading()
        XCTAssertNotEqual(browser.documentRevision, revision)
    }
}

@MainActor
private final class CapturePageSource {
    var page = JarvisBrowserPageCapture.Page(revision: UUID(),
        url: URL(string: "https://example.invalid/inicial")!, title: "Página inicial")

    func matches(_ captured: JarvisBrowserPageCapture.Page) -> Bool {
        captured.revision == page.revision && captured.url == page.url
    }
}

private enum CaptureReadError: Error { case synthetic }

@MainActor
private final class CapturePausedReader {
    private var started = false
    private var continuation: CheckedContinuation<String?, any Error>?
    private var arrival: CheckedContinuation<Void, Never>?

    func read() async throws -> String? {
        started = true
        arrival?.resume(); arrival = nil
        return try await withCheckedThrowingContinuation { continuation = $0 }
    }

    func waitUntilStarted() async {
        if !started { await withCheckedContinuation { arrival = $0 } }
    }

    func finish(_ result: Result<String?, any Error>) {
        continuation?.resume(with: result)
        continuation = nil
    }
}
