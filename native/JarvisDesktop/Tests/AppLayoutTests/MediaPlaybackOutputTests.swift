@preconcurrency import AVFoundation
import CoreAudio
import DesktopCore
import Foundation
import XCTest
@testable import JarvisApp

@MainActor private final class PlaybackOutputFixture: LocalMediaOutputDriver {
    static let builtin = LocalMediaOutputDevice(id: "fixture-builtin", name: "Integrada sintética", transport: kAudioDeviceTransportTypeBuiltIn)
    static let usb = LocalMediaOutputDevice(id: "fixture-usb", name: "USB sintética", transport: kAudioDeviceTransportTypeUSB)
    var current = try! LocalMediaOutputSnapshot(devices: [PlaybackOutputFixture.builtin, PlaybackOutputFixture.usb], defaultDeviceID: PlaybackOutputFixture.builtin.id)
    var callbacks: [@MainActor @Sendable () -> Void] = []
    var retired = 0
    var monitorFails = false
    func snapshot() throws -> LocalMediaOutputSnapshot { current }
    func observe(_ changed: @escaping @MainActor @Sendable () -> Void) throws -> LocalMediaOutputObservation {
        if monitorFails { throw LocalMediaOutputIssue.monitoringUnavailable }
        callbacks.append(changed)
        return LocalMediaOutputObservation { [weak self] in Task { @MainActor in self?.retired += 1 } }
    }
    func changed() { callbacks.last?() }
}
@MainActor private final class PlaybackPlayerFixture: MediaPlaybackPlayerDriver {
    var player: AVPlayer? { nil } // Routing fixtures never open an audio device.
    var onFailure: (@MainActor () -> Void)?
    private(set) var outputDeviceID: String?
    var prepared = 0, closed = 0, paused = 0
    var position = 0.0
    func prepare(_ source: MemoryMediaAsset) { prepared += 1; position = 0 }
    func selectOutput(_ id: String) { outputDeviceID = id }
    func pause() { paused += 1 }
    func close() { closed += 1; outputDeviceID = nil }
}

final class MediaPlaybackOutputTests: XCTestCase {
    @MainActor private func preview(id: String = "a", session: String = "b") throws -> MediaPreview {
        // Metadata/routing only. Real WAV, AVPlayer and decoder qualification
        // remains separate from this injected output-device fixture.
        let data = Data("SYNTHETIC MEDIA ROUTING BYTES".utf8)
        let attachment = try MediaAttachment(.object(["format": .string("jarvis-attachment/1"),
            "attachment_id": .string(String(repeating: id, count: 32)), "session_id": .string(String(repeating: session, count: 32)),
            "workspace_id": .string(String(repeating: "c", count: 64)), "temporary": .bool(true),
            "name": .string("fixture.wav"), "mime": .string("audio/wav"), "kind": .string("audio"),
            "byte_length": .number(Double(data.count)), "sha256": .string(MediaFilePolicy.digest(data)),
            "source": .string("composer"), "revoked": .bool(false)]))
        return MediaPreview(attachment: attachment, data: data)
    }
    @MainActor func testPerPlayerSelectionPreservesResourceAndSeekWithoutChangingSystemDefault() throws {
        let outputs = PlaybackOutputFixture(), driver = PlaybackPlayerFixture()
        let model = MediaPlayback(outputs: outputs, playerDriver: driver)
        model.prepare(try preview()); let resource = model.resourceID
        XCTAssertTrue(model.ready); XCTAssertEqual(model.resolvedOutputID, PlaybackOutputFixture.builtin.id)
        XCTAssertNil(model.selectedOutputID); XCTAssertEqual(driver.prepared, 1)
        driver.position = 12.5; model.selectOutput(PlaybackOutputFixture.usb.id)
        XCTAssertEqual(driver.position, 12.5); XCTAssertEqual(driver.prepared, 1); XCTAssertEqual(model.resourceID, resource)
        XCTAssertEqual(model.selectedOutputID, PlaybackOutputFixture.usb.id)
        XCTAssertEqual(outputs.current.defaultDeviceID, PlaybackOutputFixture.builtin.id)
        XCTAssertEqual(driver.outputDeviceID, PlaybackOutputFixture.usb.id); XCTAssertGreaterThan(driver.paused, 0)
        model.stop()
    }
    @MainActor func testDisconnectedExplicitOutputStopsAndCannotResumeOrFallbackAutomatically() throws {
        let outputs = PlaybackOutputFixture(), driver = PlaybackPlayerFixture(), model = MediaPlayback(outputs: outputs, playerDriver: driver)
        model.prepare(try preview()); model.selectOutput(PlaybackOutputFixture.usb.id)
        outputs.current = try LocalMediaOutputSnapshot(devices: [PlaybackOutputFixture.builtin], defaultDeviceID: PlaybackOutputFixture.builtin.id)
        outputs.changed()
        XCTAssertFalse(model.ready); XCTAssertNil(model.resolvedOutputID); XCTAssertNil(driver.outputDeviceID)
        XCTAssertEqual(model.selectedOutputID, PlaybackOutputFixture.usb.id); XCTAssertNotNil(model.issue)
        outputs.current = try LocalMediaOutputSnapshot(devices: [PlaybackOutputFixture.builtin, PlaybackOutputFixture.usb], defaultDeviceID: PlaybackOutputFixture.builtin.id)
        outputs.changed(); XCTAssertFalse(model.ready); XCTAssertEqual(driver.prepared, 1)
        model.selectOutput(PlaybackOutputFixture.usb.id); XCTAssertTrue(model.ready); XCTAssertEqual(driver.prepared, 2)
        model.stop()
    }
    @MainActor func testDefaultChangeRetiresPinnedPlayerAndModelsHaveIndependentSelections() throws {
        let outputs = PlaybackOutputFixture(), firstDriver = PlaybackPlayerFixture(), secondDriver = PlaybackPlayerFixture()
        let first = MediaPlayback(outputs: outputs, playerDriver: firstDriver), second = MediaPlayback(outputs: outputs, playerDriver: secondDriver)
        first.prepare(try preview()); second.prepare(try preview(id: "d"))
        first.selectOutput(PlaybackOutputFixture.usb.id)
        XCTAssertNil(second.selectedOutputID); XCTAssertEqual(second.resolvedOutputID, PlaybackOutputFixture.builtin.id)
        outputs.current = try LocalMediaOutputSnapshot(devices: [PlaybackOutputFixture.builtin, PlaybackOutputFixture.usb], defaultDeviceID: PlaybackOutputFixture.usb.id)
        // Each observer receives the system notification, without choosing a
        // new device for the player that used the old default.
        let active = Array(outputs.callbacks.suffix(2)); for callback in active { callback() }
        XCTAssertTrue(first.ready); XCTAssertFalse(second.ready)
        first.stop(); second.stop()
    }
    @MainActor func testContextRetirementIgnoresLateDeviceCallbackAndReleasesPlayer() throws {
        let outputs = PlaybackOutputFixture(), driver = PlaybackPlayerFixture(), model = MediaPlayback(outputs: outputs, playerDriver: driver)
        model.prepare(try preview()); let stale = try XCTUnwrap(outputs.callbacks.first)
        model.stop(); model.prepare(try preview(id: "d", session: "e")); let next = model.resourceID
        outputs.current = try LocalMediaOutputSnapshot(devices: [], defaultDeviceID: nil)
        stale()
        XCTAssertTrue(model.ready); XCTAssertEqual(model.resourceID, next)
        model.stop(); stale()
        XCTAssertFalse(model.ready); XCTAssertNil(model.resourceID); XCTAssertNil(model.selectedOutputID); XCTAssertNil(driver.outputDeviceID)
    }
    @MainActor func testMissingDeviceMonitoringFailureAndDecoderFailureReleaseResources() throws {
        let outputs = PlaybackOutputFixture(), driver = PlaybackPlayerFixture(), model = MediaPlayback(outputs: outputs, playerDriver: driver)
        outputs.monitorFails = true; model.prepare(try preview())
        XCTAssertFalse(model.ready); XCTAssertEqual(driver.prepared, 0); XCTAssertNotNil(model.issue)
        outputs.monitorFails = false; model.selectOutput(nil); XCTAssertTrue(model.ready)
        driver.onFailure?(); XCTAssertFalse(model.ready); XCTAssertNil(driver.outputDeviceID); XCTAssertNotNil(model.issue)
        model.stop()
    }
}
