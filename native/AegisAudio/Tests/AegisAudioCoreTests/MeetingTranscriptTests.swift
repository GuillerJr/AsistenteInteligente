import Foundation
import Testing
@testable import AegisAudioCore

@Test func shortMeetingIsOneDocument() {
    let parts = ChatAttachment.meetingTranscript("Hola a todos, empezamos.", title: "Reunión 20/10 10:00")
    #expect(parts.count == 1)
    #expect(parts[0].name == "Reunión 20/10 10:00.txt")
    #expect(parts[0].document?.content == "Hola a todos, empezamos.")
}

@Test func longMeetingSplitsIntoAtMostFourBoundedParts() {
    let words = Array(repeating: "palabra", count: 20_000).joined(separator: " ")
    let parts = ChatAttachment.meetingTranscript(words, title: "Reunión")
    #expect(parts.count == 4)
    #expect(parts.allSatisfy { ($0.document?.content.utf8.count ?? 0) <= 30_000 })
    #expect(parts[1].name == "Reunión (parte 2).txt")
}
