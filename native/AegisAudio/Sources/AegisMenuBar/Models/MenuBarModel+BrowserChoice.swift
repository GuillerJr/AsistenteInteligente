import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for BrowserChoice; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func resolveBrowserTarget(
        for transcript: SpeechTranscriptEvent,
        secret: Data
    ) async -> IPCBrowserDiscovery? {
        guard requiresBrowserResolution(transcript.text) else { return nil }
        return await Task.detached(priority: .utility) {
            guard
                let response = try? LocalIPCClient(secret: secret).discoverBrowsers(
                    for: transcript.text
                )
            else {
                return nil
            }
            return IPCBrowserDiscovery(response: response)
        }.value
    }

    func requiresBrowserResolution(_ text: String) -> Bool {
        let normalized = text.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es")
        ).lowercased()
        return normalized.range(
            of: #"(?:^|\s)(?:el\s+navegador|the\s+browser|navegador|browser)(?:\s|$)"#,
            options: .regularExpression
        ) != nil
    }

    func presentBrowserSelection(
        transcript: SpeechTranscriptEvent,
        options: [IPCBrowserOption]
    ) {
        let running = options.filter(\.running)
        let candidates = running.isEmpty ? options : running
        guard candidates.count > 1 else { return }
        browserChoiceTask?.cancel()
        pendingBrowserSelection = PendingBrowserSelection(
            transcript: transcript,
            options: candidates
        )
        voiceState = .speaking
        // Only this public prompt reaches TTS. Installed browser names stay in the local HUD.
        speakWithWakeWordIsolation(
            String(localized: "¿Qué navegador quieres usar? Di su nombre o elígelo en pantalla.", bundle: .module)
        ) { [weak self] in
            guard let self, pendingBrowserSelection != nil else { return }
            browserChoiceTask = Task { @MainActor [weak self] in
                await self?.captureSpokenBrowserChoice()
            }
        }
    }

    private func captureSpokenBrowserChoice() async {
        guard pendingBrowserSelection != nil else { return }
        let capture = await captureSpokenPrompt(
            playCue: false,
            durationSeconds: 8
        )
        guard
            !Task.isCancelled,
            case let .transcript(transcript) = capture,
            let selection = matchingBrowserOption(for: transcript.text)
        else {
            if pendingBrowserSelection != nil {
                voiceState = .awaitingAuthorization
            }
            browserChoiceTask = nil
            return
        }
        browserChoiceTask = nil
        guard let trusted = await transcriptWithOwnerPresence(transcript),
              trusted.ownerSpeakerProfile, trusted.ownerPresenceVerified, userSessionAvailable else {
            if pendingBrowserSelection != nil { voiceState = .awaitingAuthorization }
            return
        }
        await selectBrowser(bundleIdentifier: selection.bundleIdentifier)
    }

    func matchingBrowserOption(for spokenText: String) -> IPCBrowserOption? {
        guard let pendingBrowserSelection else { return nil }
        let normalized = spokenText.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es")
        ).split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        return pendingBrowserSelection.options.first { option in
            let name = option.name.folding(
                options: [.caseInsensitive, .diacriticInsensitive],
                locale: Locale(identifier: "es")
            ).lowercased()
            return normalized.lowercased() == name
                || (name == "chrome" && normalized.lowercased() == "google chrome")
        }
    }

    func selectBrowser(bundleIdentifier: String) async {
        guard
            let selection = pendingBrowserSelection,
            let option = selection.options.first(where: {
                $0.bundleIdentifier == bundleIdentifier
            }),
            let secret = ipcSecret
        else {
            return
        }
        browserChoiceTask?.cancel()
        browserChoiceTask = nil
        pendingBrowserSelection = nil
        logger.info(
            "browser_choice_selected browser=\(option.name, privacy: .public)"
        )
        await submitResolvedVoiceTranscript(
            selection.transcript,
            preferredBrowserBundleIdentifier: option.bundleIdentifier,
            secret: secret
        )
    }
}
