import Foundation
import Testing
@testable import AegisAudioCore

private final class SileroTestBundleToken: NSObject {}

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

@Test func sileroIsolatedRuntimeProcessesPCMAndResetWithinFrameBudget() throws {
    let detector = try SileroVADDetector(bundle: Bundle(for: SileroTestBundleToken.self))
    let frame = [Float](repeating: 0, count: 512)
    #expect(try detector.append(samples: Array(frame.prefix(128))).isEmpty)
    let first = try detector.append(samples: Array(frame.dropFirst(128)))
    #expect(first.count == 1)
    #expect(first[0].speechProbability.isFinite)
    detector.reset()
    let clock = ContinuousClock()
    let start = clock.now
    for _ in 0..<100 {
        let observations = try detector.append(samples: frame)
        #expect(observations.count == 1)
        #expect((0...1).contains(observations[0].speechProbability))
        #expect(observations[0].event == nil)
    }
    // 100 x 32 ms of PCM must be processed faster than real time, including IPC.
    #expect(start.duration(to: clock.now) < .seconds(3.2))
    #expect(throws: SileroVADError.invalidInput) {
        try detector.append(samples: [Float](repeating: 0, count: 4097))
    }
    #expect(throws: SileroVADError.invalidInput) { try detector.append(samples: [.nan]) }
}

@Test func sileroStalledWorkerTimesOutAndFailsClosed() throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: folder) }
    let helper = folder.appendingPathComponent("stalled-worker")
    try Data("#!/bin/sh\nprintf VAD1\nexec /bin/sleep 10\n".utf8).write(to: helper)
    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
    let client = try VADProcessClient(executableURL: helper, timeout: 0.05)
    let start = ContinuousClock.now
    #expect(throws: SileroVADError.inferenceFailed) {
        try client.infer(chunk: [Float](repeating: 0, count: 512))
    }
    #expect(start.duration(to: .now) < .seconds(1))
    #expect(throws: SileroVADError.inferenceFailed) {
        try client.infer(chunk: [Float](repeating: 0, count: 512))
    }
}

@Test func sileroRejectsUnsignedAndSymlinkedBundleHelpers() throws {
    let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let bundle = folder.appendingPathComponent("Jarvis.app")
    let helper = bundle.appendingPathComponent("Contents/Helpers/jarvis-vad-helper")
    try FileManager.default.createDirectory(at: helper.deletingLastPathComponent(), withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: folder) }
    try Data("#!/bin/sh\nexit 0\n".utf8).write(to: helper)
    try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
    #expect(throws: SileroVADError.invalidModel) {
        try VADProcessClient.validateHelper(helper, bundleURL: bundle)
    }
    try FileManager.default.removeItem(at: helper)
    try FileManager.default.createSymbolicLink(at: helper, withDestinationURL: URL(fileURLWithPath: "/bin/cat"))
    #expect(throws: SileroVADError.invalidModel) {
        try VADProcessClient.validateHelper(helper, bundleURL: bundle)
    }
}

@Test func sileroTrustRequiresSameCertificateAndDedicatedHelperIdentifier() {
    let appCertificate = Data([0x30, 0x82, 0x01, 0x02])
    #expect(VADProcessClient.matchesSigningIdentity(
        ownCertificate: appCertificate, helperCertificate: appCertificate,
        helperIdentifier: "jarvis-vad-helper"
    ))
    #expect(!VADProcessClient.matchesSigningIdentity(
        ownCertificate: appCertificate, helperCertificate: Data([0x30, 0x82, 0x03, 0x04]),
        helperIdentifier: "jarvis-vad-helper"
    ))
    #expect(!VADProcessClient.matchesSigningIdentity(
        ownCertificate: appCertificate, helperCertificate: appCertificate,
        helperIdentifier: "aegis-audio-helper"
    ))
    #expect(!VADProcessClient.matchesSigningIdentity(
        ownCertificate: nil, helperCertificate: nil, helperIdentifier: "jarvis-vad-helper"
    ))
    #expect(!VADProcessClient.matchesSigningIdentity(
        ownCertificate: appCertificate, helperCertificate: nil,
        helperIdentifier: "jarvis-vad-helper"
    ))
}
