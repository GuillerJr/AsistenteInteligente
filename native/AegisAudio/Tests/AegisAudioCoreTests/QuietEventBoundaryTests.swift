import CoreGraphics
import Foundation
import Testing
@testable import AegisAudioCore

private enum BoundaryProbeError: Error { case contextChanged, deniedPoint, failedAX }

@Test func quietPairStopsBeforeEffectWhenOwnerHasTakenOver() throws {
    var delivered: [String] = []
    #expect(throws: QuietEventDispatcherError.userTakeover) {
        try QuietEventBoundary.pair(
            revalidate: { _ in throw QuietEventDispatcherError.userTakeover },
            down: { delivered.append("down") }, up: { delivered.append("up") }
        )
    }
    #expect(delivered.isEmpty)
}

@Test func quietPairReleasesDeliveredDownAndPreservesTakeover() throws {
    var delivered: [String] = []
    #expect(throws: QuietEventDispatcherError.userTakeover) {
        try QuietEventBoundary.pair(
            revalidate: { phase in
                if phase == .beforeRelease { throw QuietEventDispatcherError.userTakeover }
            },
            down: { delivered.append("down") }, up: { delivered.append("up") }
        )
    }
    #expect(delivered == ["down", "up"])
}

@Test func quietPairNeverReplaysAfterPartialEffectOrContextChange() throws {
    var delivered: [String] = []
    #expect(throws: QuietEventDispatcherError.effectUncertain) {
        try QuietEventBoundary.pair(
            revalidate: { phase in
                if phase == .beforeRelease { throw BoundaryProbeError.contextChanged }
            },
            down: { delivered.append("down") }, up: { delivered.append("up") }
        )
    }
    #expect(delivered == ["down", "up"])
}

@Test func quietMutationDoesNotRetryAnAmbiguousAXFailure() throws {
    var effects = 0
    #expect(throws: QuietEventDispatcherError.effectUncertain) {
        try QuietEventBoundary.mutation(revalidate: { _ in }) {
            effects += 1
            throw BoundaryProbeError.failedAX
        }
    }
    #expect(effects == 1)
}

@Test func quietTextStopsAtOwnerInputWithoutReplayingPrefix() throws {
    var delivered: [[UInt16]] = []
    #expect(throws: QuietEventDispatcherError.userTakeover) {
        try QuietEventBoundary.textFragments(String(repeating: "a", count: 61), revalidate: { _ in
            if !delivered.isEmpty { throw QuietEventDispatcherError.userTakeover }
        }, post: { delivered.append($0) })
    }
    #expect(delivered.count == 1)
    #expect(delivered[0].count == 20)
}

@Test func quietTextMarksContextChangeAfterPrefixAsTerminal() throws {
    var delivered: [[UInt16]] = []
    #expect(throws: QuietEventDispatcherError.effectUncertain) {
        try QuietEventBoundary.textFragments(String(repeating: "b", count: 61), revalidate: { phase in
            if phase == .beforeEffect, !delivered.isEmpty { throw BoundaryProbeError.contextChanged }
        }, post: { delivered.append($0) })
    }
    #expect(delivered.count == 1)
}

@Test func quietTextUsesActualGraphemeSafeFragmentPlan() throws {
    let text = String(repeating: "a", count: 19) + "🤖" + String(repeating: "é", count: 40)
    var delivered: [[UInt16]] = []
    try QuietEventBoundary.textFragments(text, revalidate: { _ in }, post: { delivered.append($0) })
    #expect(String(decoding: delivered.flatMap { $0 }, as: UTF16.self) == text)
    #expect(delivered[0].count == 19)
    #expect(delivered[1].starts(with: Array("🤖".utf16)))
    #expect(delivered.allSatisfy { $0.count <= 20 })
}

private final class VisionBoundaryProbe: @unchecked Sendable {
    var changed = false
    var analyzed = 0
    var posted: [CGPoint] = []
}

private func syntheticCapture() -> LocalizedVisionCapture {
    LocalizedVisionCapture(windowID: 2, processIdentifier: 7,
                           cropInWindow: CGRect(x: 0, y: 0, width: 120, height: 120),
                           expectedPointInCrop: CGPoint(x: 60, y: 60), imageData: Data([1]),
                           imageSHA256: String(repeating: "a", count: 64))
}

@Test func quietLocalizedVisionRejectsTakeoverDuringCaptureBeforeModel() async throws {
    let probe = VisionBoundaryProbe()
    do {
        try await QuietEventBoundary.localizedClick(
            expectedScreenPoint: CGPoint(x: 300, y: 300),
            revalidate: { _ in if probe.changed { throw QuietEventDispatcherError.userTakeover } },
            capture: { probe.changed = true; return syntheticCapture() },
            analyzer: { _ in
                probe.analyzed += 1
                return LocalizedVisionDecision(offsetX: 0, offsetY: 0, confidence: 1, targetFound: true)
            },
            validateCorrectedPoint: { _ in }, post: { probe.posted.append($0) }
        )
        Issue.record("Takeover must reject the observation")
    } catch { #expect(error as? QuietEventDispatcherError == .userTakeover) }
    #expect(probe.analyzed == 0)
    #expect(probe.posted.isEmpty)
}

@Test func quietLocalizedVisionRevalidatesAfterModelBeforeCorrectedClick() async throws {
    let probe = VisionBoundaryProbe()
    do {
        try await QuietEventBoundary.localizedClick(
            expectedScreenPoint: CGPoint(x: 300, y: 300),
            revalidate: { _ in if probe.changed { throw QuietEventDispatcherError.userTakeover } },
            capture: { syntheticCapture() },
            analyzer: { _ in
                probe.changed = true
                return LocalizedVisionDecision(offsetX: 1, offsetY: 1, confidence: 1, targetFound: true)
            },
            validateCorrectedPoint: { _ in }, post: { probe.posted.append($0) }
        )
        Issue.record("Takeover must reject the model result")
    } catch { #expect(error as? QuietEventDispatcherError == .userTakeover) }
    #expect(probe.posted.isEmpty)
}

@Test func quietLocalizedVisionCannotAuthorizeAnotherControl() async throws {
    let probe = VisionBoundaryProbe()
    do {
        try await QuietEventBoundary.localizedClick(
            expectedScreenPoint: CGPoint(x: 300, y: 300), revalidate: { _ in },
            capture: { syntheticCapture() },
            analyzer: { _ in
                LocalizedVisionDecision(offsetX: 50, offsetY: 0, confidence: 1, targetFound: true)
            },
            validateCorrectedPoint: { point in
                guard CGRect(x: 290, y: 290, width: 20, height: 20).contains(point)
                else { throw BoundaryProbeError.deniedPoint }
            },
            post: { probe.posted.append($0) }
        )
        Issue.record("Model coordinates must stay on the independently bound control")
    } catch { #expect(error is BoundaryProbeError) }
    #expect(probe.posted.isEmpty)
}

@Test func quietLocalizedVisionChecksCancellationEvenIfModelReturns() async throws {
    let probe = VisionBoundaryProbe()
    let task = Task {
        try await QuietEventBoundary.localizedClick(
            expectedScreenPoint: CGPoint(x: 300, y: 300), revalidate: { _ in },
            capture: { syntheticCapture() },
            analyzer: { _ in
                withUnsafeCurrentTask { $0?.cancel() }
                return LocalizedVisionDecision(offsetX: 0, offsetY: 0, confidence: 1, targetFound: true)
            },
            validateCorrectedPoint: { _ in }, post: { probe.posted.append($0) }
        )
    }
    do { try await task.value; Issue.record("Cancelled inference cannot cause a click") }
    catch { #expect(error is CancellationError) }
    #expect(probe.posted.isEmpty)
}

@Test func computerAXObservationPreservesExactValueAndSignalsTruncation() {
    let exact = ComputerAXObservationMetadata.value("first\n second  line")
    #expect(exact.text == "first\n second  line")
    #expect(!exact.truncated)
    let clipped = ComputerAXObservationMetadata.value(String(repeating: "a", count: 257))
    #expect(clipped.text.count == 256)
    #expect(clipped.truncated)
}

@Test func computerAXObservationOnlyExposesNonSecretExactHTTPURLs() {
    let exact = "https://example.invalid/search?q=Jarvis%20notes"
    #expect(ComputerAXObservationMetadata.currentURL(exact, secureContent: false) == exact)
    for rejected in ["https://user:pass@example.invalid", "https://example.invalid?access_token=abc",
                     "https://example.invalid?code=abc", "https://example.invalid#secret",
                     "https://example.invalid?q=owner%40example.invalid", "file:///private/secret"] {
        #expect(ComputerAXObservationMetadata.currentURL(rejected, secureContent: false) == nil)
    }
    #expect(ComputerAXObservationMetadata.currentURL(exact, secureContent: true) == nil)
}
