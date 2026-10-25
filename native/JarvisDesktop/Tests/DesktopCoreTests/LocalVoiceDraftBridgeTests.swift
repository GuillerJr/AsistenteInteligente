import Foundation
import XCTest
@testable import DesktopCore

final class LocalVoiceDraftBridgeTests: XCTestCase {
    func testDictationPreservesExistingDraftAndNeverCrossesConversationOrTemporaryMode() {
        let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: false)
        let transcript = LocalVoiceTranscript(contextID: LocalVoiceDraftBridge.contextID(scope),
                                               text: "Dictado pendiente de revisar", completion: .recognizedFinal)
        XCTAssertEqual(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "Texto escrito", eligible: true),
                       "Texto escrito\nDictado pendiente de revisar")
        let another = SessionScope(sessionID: String(repeating: "b", count: 32), temporary: false)
        let temporary = SessionScope(sessionID: scope.sessionID, temporary: true)
        for invalid in [another, temporary] {
            XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: invalid, existing: "Conservar", eligible: true))
        }
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: nil, existing: "Conservar", eligible: true))
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "Conservar", eligible: false))
    }

    func testPartialTextRemainsReviewMaterialAndUTF8BudgetRejectsWithoutTruncation() {
        let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
        let transcript = LocalVoiceTranscript(contextID: LocalVoiceDraftBridge.contextID(scope), text: "Ω", completion: .partialAtStop)
        XCTAssertTrue(transcript.requiresReview)
        XCTAssertEqual(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "x\n", eligible: true, maximumBytes: 4), "x\nΩ")
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: "x\n", eligible: true, maximumBytes: 3))
        XCTAssertNil(LocalVoiceDraftBridge.append(transcript, scope: scope, existing: String(repeating: "x", count: 32000), eligible: true))
    }
}
