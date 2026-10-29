import CoreAudio
import Foundation
import XCTest
@testable import DesktopCore

final class LocalMediaAudioOutputTests: XCTestCase {
    func testOnlyAlreadyConnectedPhysicalOutputTransportsAreAdmitted() throws {
        for transport in LocalMediaOutputPolicy.physicalTransports {
            XCTAssertTrue(LocalMediaOutputPolicy.admits(.init(id: "synthetic", name: "Salida de prueba", transport: transport)))
        }
        for transport in [kAudioDeviceTransportTypeAirPlay, kAudioDeviceTransportTypeAVB,
                          kAudioDeviceTransportTypeVirtual, kAudioDeviceTransportTypeAggregate, kAudioDeviceTransportTypeUnknown] {
            XCTAssertFalse(LocalMediaOutputPolicy.admits(.init(id: "not-local", name: "No admitida", transport: transport)))
        }
        XCTAssertFalse(LocalMediaOutputPolicy.admits(.init(id: "input", name: "Sólo entrada", transport: kAudioDeviceTransportTypeUSB, hasOutput: false)))
        XCTAssertFalse(LocalMediaOutputPolicy.admits(.init(id: "gone", name: "Desconectada", transport: kAudioDeviceTransportTypeBluetooth, alive: false)))
    }
    func testDefaultResolvesToAnExactLocalUIDAndExplicitRetirementCannotFallBack() throws {
        let a = LocalMediaOutputDevice(id: "builtin", name: "Integrada", transport: kAudioDeviceTransportTypeBuiltIn)
        let b = LocalMediaOutputDevice(id: "usb", name: "USB", transport: kAudioDeviceTransportTypeUSB)
        let snapshot = try LocalMediaOutputSnapshot(devices: [a, b], defaultDeviceID: a.id)
        XCTAssertEqual(try LocalMediaOutputPolicy.resolve(selectedID: nil, snapshot: snapshot), a)
        XCTAssertEqual(try LocalMediaOutputPolicy.resolve(selectedID: b.id, snapshot: snapshot), b)
        let removed = try LocalMediaOutputSnapshot(devices: [a], defaultDeviceID: a.id)
        XCTAssertFalse(LocalMediaOutputPolicy.isCurrent(selectedID: b.id, resolvedID: b.id, snapshot: removed))
        XCTAssertThrowsError(try LocalMediaOutputPolicy.resolve(selectedID: b.id, snapshot: removed))
        let switchedDefault = try LocalMediaOutputSnapshot(devices: [a, b], defaultDeviceID: b.id)
        XCTAssertFalse(LocalMediaOutputPolicy.isCurrent(selectedID: nil, resolvedID: a.id, snapshot: switchedDefault))
        XCTAssertTrue(LocalMediaOutputPolicy.isCurrent(selectedID: a.id, resolvedID: a.id, snapshot: switchedDefault))
    }
    func testAirPlayDefaultAndDuplicateOrForgedIdentifiersAreRejected() throws {
        let remote = LocalMediaOutputDevice(id: "remote", name: "Remota", transport: kAudioDeviceTransportTypeAirPlay)
        let snapshot = try LocalMediaOutputSnapshot(devices: [remote], defaultDeviceID: remote.id)
        XCTAssertTrue(snapshot.devices.isEmpty); XCTAssertNil(snapshot.defaultDeviceID)
        XCTAssertThrowsError(try LocalMediaOutputPolicy.resolve(selectedID: nil, snapshot: snapshot))
        XCTAssertThrowsError(try LocalMediaOutputSnapshot(devices: [remote, remote], defaultDeviceID: remote.id))
        XCTAssertFalse(LocalMediaOutputPolicy.admits(.init(id: "uid\n", name: "No válida", transport: kAudioDeviceTransportTypeBuiltIn)))
    }
    func testObservationRetirementRunsOnlyOnce() {
        var retired = 0
        let observation = LocalMediaOutputObservation { retired += 1 }
        observation.cancel(); observation.cancel()
        XCTAssertEqual(retired, 1)
    }
}
