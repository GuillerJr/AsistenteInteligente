import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Single voice private audio replay")
struct SpeechAudioReplayBufferTests {
    @Test("Replays the exact PCM, not new synthesis or a different identity")
    func exactPCM() {
        var buffer = SpeechAudioReplayBuffer()
        let chunks = [Data([1, 0, 2, 0]), Data([3, 0])]
        let stored = buffer.store(text: "  Hola\nJarvis ", chunks: chunks, at: 10)
        #expect(stored)
        #expect(buffer.audio(matching: "Hola Jarvis", at: 11) == chunks)
        #expect(buffer.audio(matching: "Otro texto", at: 11) == nil)
        #expect(buffer.audio(matching: "Hola Jarvis", at: 12) == chunks)
    }

    @Test("Expiry, reverse clock and privacy reset erase audio")
    func privacyLifecycle() {
        var buffer = SpeechAudioReplayBuffer()
        let pcm = [Data([1, 0])]
        let first = buffer.store(text: "Hola", chunks: pcm, at: 100)
        #expect(first)
        #expect(buffer.audio(matching: "Hola", at: 400) == nil)
        #expect(buffer.audio(matching: "Hola", at: 101) == nil)
        let second = buffer.store(text: "Hola", chunks: pcm, at: 100)
        #expect(second)
        #expect(buffer.audio(matching: "Hola", at: 99) == nil)
        #expect(buffer.audio(matching: "Hola", at: 101) == nil)
        let third = buffer.store(text: "Hola", chunks: pcm, at: 100)
        #expect(third)
        buffer.clear()
        #expect(buffer.audio(matching: "Hola", at: 101) == nil)
    }

    @Test("Rejects malformed, empty and oversized PCM", arguments: [
        "empty", "empty_chunk", "odd", "oversized_chunk", "byte_limit", "chunk_limit",
    ])
    func rejectsInvalidAudio(kind: String) {
        let chunks: [Data] = switch kind {
        case "empty": []
        case "empty_chunk": [Data()]
        case "odd": [Data([1])]
        case "oversized_chunk": [Data(count: 4_098)]
        case "byte_limit": Array(repeating: Data(count: 4_096), count: 1_025)
        default: Array(repeating: Data([1, 0]), count: 4_097)
        }
        var buffer = SpeechAudioReplayBuffer()
        let stored = buffer.store(text: "Hola", chunks: chunks, at: 100)
        #expect(!stored)
        #expect(buffer.audio(matching: "Hola", at: 101) == nil)
    }

    @Test("Rejects empty text and invalid clocks")
    func invalidMetadata() {
        var buffer = SpeechAudioReplayBuffer()
        let empty = buffer.store(text: " ", chunks: [Data([1, 0])], at: 1)
        let invalidClock = buffer.store(text: "Hola", chunks: [Data([1, 0])], at: .nan)
        let valid = buffer.store(text: "Hola", chunks: [Data([1, 0])], at: 1)
        #expect(!empty)
        #expect(!invalidClock)
        #expect(valid)
        #expect(buffer.audio(matching: "Hola", at: .infinity) == nil)
    }
}
