import Testing
@testable import AegisAudioCore

@Test func wakeWordTrainingWindowRemovesQuietPaddingAndKeepsTheWholeWord() throws {
    var rms = [Double](repeating: 0.002, count: 63)
    for index in 20 ... 40 { rms[index] = 0.02 }
    let range = try #require(WakeWordTrainingWindow.sampleRange(
        frameRMS: rms, framesPerRMS: 1_536, sampleCount: 96_000, sampleRate: 48_000
    ))
    #expect(range.count == 48_000)
    #expect(range.lowerBound <= 20 * 1_536)
    #expect(range.upperBound >= 41 * 1_536)
}

@Test func wakeWordTrainingWindowKeepsWordsAtEitherEdgeAndLongUtterances() throws {
    for active in [0 ... 15, 50 ... 62, 10 ... 60] {
        var rms = [Double](repeating: 0.002, count: 63)
        for index in active { rms[index] = 0.02 }
        let range = try #require(WakeWordTrainingWindow.sampleRange(
            frameRMS: rms, framesPerRMS: 1_536, sampleCount: 96_000, sampleRate: 48_000
        ))
        #expect(range.lowerBound >= 0)
        #expect(range.upperBound <= 96_000)
        #expect(range.lowerBound <= active.lowerBound * 1_536)
        #expect(range.upperBound >= min((active.upperBound + 1) * 1_536, 96_000))
    }
}

@Test func wakeWordTrainingWindowRetainsAmbiguousSamplesAndRejectsInvalidMetrics() {
    let quiet = [Double](repeating: 0.001, count: 63)
    #expect(WakeWordTrainingWindow.sampleRange(frameRMS: quiet, framesPerRMS: 1_536,
                                               sampleCount: 96_000, sampleRate: 48_000) == 0 ..< 96_000)
    #expect(WakeWordTrainingWindow.sampleRange(frameRMS: [.nan], framesPerRMS: 512,
                                               sampleCount: 512, sampleRate: 16_000) == nil)
    #expect(WakeWordTrainingWindow.sampleRange(frameRMS: quiet, framesPerRMS: 0,
                                               sampleCount: 96_000, sampleRate: 48_000) == nil)
}
