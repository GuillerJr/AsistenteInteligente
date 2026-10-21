import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

final class ChatSubmissionTests: XCTestCase {
    func testImageAndDocumentKeepSelectedMode() throws {
        for planMode in [true, false] { try checkImageAndDocumentMode(planMode: planMode) }
    }

    private func checkImageAndDocumentMode(planMode: Bool) throws {
        let image = try LocalImageAttachment(mediaType: "image/png", data: Data([137, 80, 78, 71, 13, 10, 26, 10]))
        let submission = ChatSubmission(text: "Prepara un plan", attachments: [
            ChatAttachment(name: "dibujo.png", content: .image(image)),
            ChatAttachment(document: .init(name: "notas.txt", content: "Revisa el dibujo.")),
        ], planMode: planMode)
        let conversation = UUID()
        let payload = submission.payload(conversationID: conversation)
        XCTAssertTrue(submission.method == "image.submit")
        XCTAssertTrue((payload["plan_mode"] as? Bool ?? false) == planMode)
        XCTAssertTrue(payload["conversation_id"] as? String == conversation.uuidString.lowercased())
        XCTAssertTrue(payload["image_name"] as? String == "dibujo.png")
        XCTAssertTrue((payload["image"] as? [String: String])?["data_base64"] == image.data.base64EncodedString())
        XCTAssertTrue((payload["documents"] as? [[String: String]])?.first?["content"] == "Revisa el dibujo.")
    }

    func testTextKeepsSelectedMode() {
        for planMode in [true, false] { checkTextMode(planMode: planMode) }
    }

    private func checkTextMode(planMode: Bool) {
        let submission = ChatSubmission(text: "Planea", attachments: [], planMode: planMode)
        XCTAssertTrue(submission.method == "swarm.submit")
        XCTAssertTrue((submission.payload(conversationID: UUID())["plan_mode"] as? Bool ?? false) == planMode)
    }
}
