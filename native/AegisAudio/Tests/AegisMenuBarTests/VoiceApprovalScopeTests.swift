import Foundation
import XCTest
@testable import AegisAudioCore
@testable import AegisMenuBar

/// Nearby speech once approved a Gmail draft on its own (22-10-2026). The microphone
/// only opens for an approval when the owner turned voice approval on and the daemon
/// says the action only reads.
@MainActor
final class VoiceApprovalScopeTests: XCTestCase {
    private func confirmation(voice: Bool?) throws -> IPCPendingConfirmation {
        var object: [String: Any] = [
            "call_digest": String(repeating: "a", count: 64),
            "tool_name": "mcp_google_workspace__draft_gmail_message",
            "summary": "Borrador sintético", "expires_at": "2099-01-01T00:00:00Z",
        ]
        if let voice { object["voice_approval_available"] = voice }
        return try XCTUnwrap(IPCPendingConfirmation(object: object))
    }

    func testVoiceApprovalIsOffUntilTheOwnerTurnsItOn() {
        let defaults = UserDefaults.standard
        let saved = defaults.object(forKey: voiceApprovalDefaultsKey)
        defer { defaults.set(saved, forKey: voiceApprovalDefaultsKey) }
        defaults.removeObject(forKey: voiceApprovalDefaultsKey)
        XCTAssertFalse(MenuBarModel().voiceApprovalEnabled)
    }

    func testMicrophoneOpensOnlyWhenEnabledAndTheActionOnlyReads() throws {
        let reads = try confirmation(voice: true)
        let writes = try confirmation(voice: false)
        let olderDaemon = try confirmation(voice: nil)
        XCTAssertEqual(
            MenuBarModel.voiceApprovalOwner("guiller", enabled: true, confirmation: reads),
            "guiller"
        )
        XCTAssertNil(MenuBarModel.voiceApprovalOwner("guiller", enabled: false, confirmation: reads))
        XCTAssertNil(MenuBarModel.voiceApprovalOwner("guiller", enabled: true, confirmation: writes))
        XCTAssertNil(
            MenuBarModel.voiceApprovalOwner("guiller", enabled: true, confirmation: olderDaemon)
        )
    }
}
