import CoreGraphics
import CoreText
import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Chat file attachments")
struct ChatAttachmentTests {
    private func folder() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    @Test func textFilesKeepTheirFullContentAndName() throws {
        let root = try folder()
        defer { try? FileManager.default.removeItem(at: root) }
        let url = root.appendingPathComponent("propuesta.md")
        let source = "# Propuesta\nProyecto Alfa: 314.\n"
        try source.write(to: url, atomically: true, encoding: .utf8)
        let attachment = try ChatAttachmentLoader.load(url)
        #expect(attachment.name == "propuesta.md")
        #expect(attachment.document?.content == source)
        #expect(!attachment.isImage)
    }

    @Test func pdfUsesExtractedTextWithoutInference() throws {
        let root = try folder()
        defer { try? FileManager.default.removeItem(at: root) }
        let data = NSMutableData()
        let consumer = try #require(CGDataConsumer(data: data))
        var page = CGRect(x: 0, y: 0, width: 300, height: 300)
        let context = try #require(CGContext(consumer: consumer, mediaBox: &page, nil))
        context.beginPDFPage(nil)
        context.textPosition = CGPoint(x: 20, y: 200)
        let line = NSAttributedString(string: "Proyecto Alfa: 314.", attributes: [
            NSAttributedString.Key(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 14, nil)
        ])
        CTLineDraw(CTLineCreateWithAttributedString(line), context)
        context.endPDFPage()
        context.closePDF()
        let url = root.appendingPathComponent("propuesta.pdf")
        try (data as Data).write(to: url)
        let attachment = try ChatAttachmentLoader.load(url)
        #expect(attachment.document?.content.contains("Proyecto Alfa: 314.") == true)
    }

    @Test func excessiveTextUnsupportedFilesAndRemoteURLsAreRejected() throws {
        let root = try folder()
        defer { try? FileManager.default.removeItem(at: root) }
        let text = root.appendingPathComponent("grande.txt")
        try String(repeating: "😀", count: 9_000).write(to: text, atomically: true, encoding: .utf8)
        #expect(throws: ChatAttachmentError.self) { try ChatAttachmentLoader.load(text) }
        let archive = root.appendingPathComponent("archivo.zip")
        try Data([80, 75, 3, 4]).write(to: archive)
        #expect(throws: ChatAttachmentError.self) { try ChatAttachmentLoader.load(archive) }
        #expect(throws: ChatAttachmentError.self) {
            try ChatAttachmentLoader.load(URL(string: "https://example.com/file.txt")!)
        }
    }

    @Test func combinedBudgetAndDuplicateNamesAreRejected() throws {
        let root = try folder()
        defer { try? FileManager.default.removeItem(at: root) }
        let files = try (0..<3).map { index in
            let url = root.appendingPathComponent("\(index).txt")
            try String(repeating: "a", count: 30_000).write(to: url, atomically: true, encoding: .utf8)
            return try ChatAttachmentLoader.load(url)
        }
        #expect(throws: ChatAttachmentError.self) { try ChatAttachmentLoader.validate(files) }
        #expect(throws: ChatAttachmentError.self) { try ChatAttachmentLoader.validate([files[0], files[0]]) }
        try ChatAttachmentLoader.validate(Array(files.prefix(2)))
    }

    @Test func restoredHistoryKeepsAttachmentLabelsAndRejectsAnotherConversation() throws {
        let id = UUID().uuidString.lowercased()
        var turn: [String: Any] = ["conversation_id": id, "turn_id": UUID().uuidString,
                                  "sequence": 1, "role": "user", "content": "Resume el archivo",
                                  "attachment_names": ["propuesta.txt"], "truncated": false]
        func page(_ turn: [String: Any]) throws -> Data {
            try JSONSerialization.data(withJSONObject: ["conversation_id": id, "turns": [turn]])
        }
        let restored = try ChatHistoryPage(page(turn))
        #expect(restored.messages[0].attachmentNames == ["propuesta.txt"])
        #expect(restored.messages[0].role == .user)
        turn["conversation_id"] = UUID().uuidString
        #expect(throws: LocalIPCError.self) { try ChatHistoryPage(page(turn)) }
    }

    @Test func chatActionResponsesDecodeNewIdentityAndRejectMalformedIDs() throws {
        let id = UUID()
        var row: [String: Any] = ["conversation_id": id.uuidString.lowercased(),
                                  "title": "Proyecto · fork", "updated_at": "2026-09-17T12:00:00Z",
                                  "archived": false]
        let fork = try ChatConversationSummary.record(JSONSerialization.data(withJSONObject: row))
        #expect(fork.id == id)
        #expect(fork.title == "Proyecto · fork")
        #expect(!fork.archived)
        row["conversation_id"] = "invalid"
        #expect(throws: LocalIPCError.self) {
            try ChatConversationSummary.record(JSONSerialization.data(withJSONObject: row))
        }
    }
}
