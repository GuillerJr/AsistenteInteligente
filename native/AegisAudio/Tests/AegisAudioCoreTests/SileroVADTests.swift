import Testing
@testable import AegisAudioCore

@Test func sileroEndpointRequiresSpeechBeforeTrailingSilence() {
    var endpoint = SileroSpeechEndpoint()

    for _ in 0 ..< SileroSpeechEndpoint.trailingSilenceChunks + 5 {
        #expect(endpoint.observe(probability: 0.01) == nil)
    }

    #expect(endpoint.speechDetected == false)
    #expect(endpoint.ended == false)
}

@Test func sileroEndpointEndsAfterExactlyEightHundredMilliseconds() {
    var endpoint = SileroSpeechEndpoint()

    #expect(endpoint.observe(probability: 0.90) == .speechStarted)
    for _ in 0 ..< SileroSpeechEndpoint.trailingSilenceChunks - 1 {
        #expect(endpoint.observe(probability: 0.10) == nil)
    }
    #expect(endpoint.observe(probability: 0.10) == .speechEnded)
    #expect(endpoint.ended)
    #expect(
        SileroSpeechEndpoint.trailingSilenceChunks * 32 == 800
    )
}

@Test func sileroEndpointResetsTrailingSilenceWhenSpeechReturns() {
    var endpoint = SileroSpeechEndpoint()

    #expect(endpoint.observe(probability: 0.70) == .speechStarted)
    for _ in 0 ..< 20 {
        #expect(endpoint.observe(probability: 0.20) == nil)
    }
    #expect(endpoint.observe(probability: 0.60) == nil)
    #expect(endpoint.silenceChunks == 0)
    #expect(endpoint.ended == false)
}
