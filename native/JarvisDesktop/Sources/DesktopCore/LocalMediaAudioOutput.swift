import CoreAudio
import Foundation

public enum LocalMediaOutputIssue: Error, LocalizedError, Sendable {
    case unavailable, changed, monitoringUnavailable
    public var errorDescription: String? {
        switch self {
        case .unavailable: "No hay una salida local disponible. Conecta o elige un dispositivo local."
        case .changed: "La salida elegida cambió o se desconectó. Se detuvo la reproducción; elige una salida antes de continuar."
        case .monitoringUnavailable: "No se pudo comprobar la continuidad de la salida. La reproducción permanece detenida."
        }
    }
}

public struct LocalMediaOutputDevice: Equatable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let transport: UInt32
    public let alive: Bool
    public let hasOutput: Bool
    public init(id: String, name: String, transport: UInt32, alive: Bool = true, hasOutput: Bool = true) {
        self.id = id; self.name = name; self.transport = transport; self.alive = alive; self.hasOutput = hasOutput
    }
}

public struct LocalMediaOutputSnapshot: Equatable, Sendable {
    public let devices: [LocalMediaOutputDevice]
    public let defaultDeviceID: String?
    public init(devices: [LocalMediaOutputDevice], defaultDeviceID: String?) throws {
        guard devices.count <= 128, Set(devices.map(\.id)).count == devices.count else { throw LocalMediaOutputIssue.unavailable }
        self.devices = devices.filter(LocalMediaOutputPolicy.admits).sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        self.defaultDeviceID = self.devices.contains(where: { $0.id == defaultDeviceID }) ? defaultDeviceID : nil
    }
}

public enum LocalMediaOutputPolicy {
    public static let physicalTransports: Set<UInt32> = [kAudioDeviceTransportTypeBuiltIn, kAudioDeviceTransportTypeUSB,
        kAudioDeviceTransportTypePCI, kAudioDeviceTransportTypeFireWire, kAudioDeviceTransportTypeThunderbolt,
        kAudioDeviceTransportTypeHDMI, kAudioDeviceTransportTypeDisplayPort,
        kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE]
    public static func admits(_ device: LocalMediaOutputDevice) -> Bool {
        device.alive && device.hasOutput && physicalTransports.contains(device.transport)
            && !device.id.isEmpty && device.id.utf8.count <= 512 && !device.name.isEmpty && device.name.utf8.count <= 256
            && !device.id.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
            && !device.name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
    }
    public static func resolve(selectedID: String?, snapshot: LocalMediaOutputSnapshot) throws -> LocalMediaOutputDevice {
        guard let id = selectedID ?? snapshot.defaultDeviceID,
              let device = snapshot.devices.first(where: { $0.id == id }), admits(device) else { throw LocalMediaOutputIssue.unavailable }
        return device
    }
    public static func isCurrent(selectedID: String?, resolvedID: String, snapshot: LocalMediaOutputSnapshot) -> Bool {
        (try? resolve(selectedID: selectedID, snapshot: snapshot).id) == resolvedID
    }
}

/// A one-shot, thread-safe retirement receipt for CoreAudio listeners. Device
/// identifiers and the selection are never serialized or sent to a model.
public final class LocalMediaOutputObservation: @unchecked Sendable {
    private let lock = NSLock()
    private var cleanup: (() -> Void)?
    public init(_ cleanup: @escaping () -> Void) { self.cleanup = cleanup }
    public func cancel() {
        lock.lock(); let action = cleanup; cleanup = nil; lock.unlock(); action?()
    }
    deinit { cancel() }
}

@MainActor public protocol LocalMediaOutputDriver: AnyObject {
    func snapshot() throws -> LocalMediaOutputSnapshot
    func observe(_ changed: @escaping @MainActor @Sendable () -> Void) throws -> LocalMediaOutputObservation
}

/// Reads only devices already published by CoreAudio. No discovery, pairing,
/// network APIs, output activation or system-default setter is used here.
@MainActor public final class CoreAudioLocalMediaOutputDriver: LocalMediaOutputDriver {
    public init() {}
    public func snapshot() throws -> LocalMediaOutputSnapshot {
        let ids = try deviceIDs()
        let devices = ids.compactMap { id -> LocalMediaOutputDevice? in
            guard let transport = uint32(id, kAudioDevicePropertyTransportType),
                  LocalMediaOutputPolicy.physicalTransports.contains(transport),
                  uint32(id, kAudioDevicePropertyDeviceIsAlive) == 1, hasOutput(id),
                  let uid = string(id, kAudioDevicePropertyDeviceUID), let name = string(id, kAudioObjectPropertyName) else { return nil }
            return .init(id: uid, name: name, transport: transport)
        }
        let defaultID = uint32(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice)
        let defaultUID = defaultID.flatMap { id -> String? in
            guard let transport = uint32(id, kAudioDevicePropertyTransportType), LocalMediaOutputPolicy.physicalTransports.contains(transport) else { return nil }
            return string(id, kAudioDevicePropertyDeviceUID)
        }
        return try LocalMediaOutputSnapshot(devices: devices, defaultDeviceID: defaultUID)
    }

    public func observe(_ changed: @escaping @MainActor @Sendable () -> Void) throws -> LocalMediaOutputObservation {
        let queue = DispatchQueue.main
        let callback: AudioObjectPropertyListenerBlock = { _, _ in Task { @MainActor in changed() } }
        var registrations: [(AudioObjectID, AudioObjectPropertyAddress)] = []
        func remove() {
            for (id, original) in registrations { var address = original; _ = AudioObjectRemovePropertyListenerBlock(id, &address, queue, callback) }
        }
        func add(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector, scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws {
            var address = Self.address(selector, scope: scope)
            guard AudioObjectAddPropertyListenerBlock(id, &address, queue, callback) == noErr else { throw LocalMediaOutputIssue.monitoringUnavailable }
            registrations.append((id, address))
        }
        do {
            try add(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDevices)
            try add(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultOutputDevice)
            for id in try deviceIDs() {
                guard let transport = uint32(id, kAudioDevicePropertyTransportType), LocalMediaOutputPolicy.physicalTransports.contains(transport) else { continue }
                try add(id, kAudioDevicePropertyDeviceIsAlive)
                try add(id, kAudioDevicePropertyTransportType)
                try add(id, kAudioDevicePropertyDeviceUID)
                try add(id, kAudioDevicePropertyStreams, scope: kAudioObjectPropertyScopeOutput)
            }
        } catch { remove(); throw error }
        // Freeze the exact registration set; cancellation cannot remove a new
        // observer or release a different preview's resources.
        let captured = registrations
        return LocalMediaOutputObservation {
            for (id, original) in captured { var address = original; _ = AudioObjectRemovePropertyListenerBlock(id, &address, queue, callback) }
        }
    }

    private static func address(_ selector: AudioObjectPropertySelector, scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        .init(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }
    private func deviceIDs() throws -> [AudioObjectID] {
        var address = Self.address(kAudioHardwarePropertyDevices), size: UInt32 = 0
        let system = AudioObjectID(kAudioObjectSystemObject)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr,
              size % UInt32(MemoryLayout<AudioObjectID>.size) == 0, size <= UInt32(128 * MemoryLayout<AudioObjectID>.size) else { throw LocalMediaOutputIssue.unavailable }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard !ids.isEmpty else { return [] }
        let status = ids.withUnsafeMutableBytes { AudioObjectGetPropertyData(system, &address, 0, nil, &size, $0.baseAddress!) }
        guard status == noErr, size <= UInt32(ids.count * MemoryLayout<AudioObjectID>.size) else { throw LocalMediaOutputIssue.unavailable }
        return Array(ids.prefix(Int(size) / MemoryLayout<AudioObjectID>.size))
    }
    private func uint32(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
        var address = Self.address(selector), value: UInt32 = 0, size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(id, &address, 0, nil, &size, &value) == noErr, size == UInt32(MemoryLayout<UInt32>.size) else { return nil }
        return value
    }
    private func string(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String? {
        var address = Self.address(selector), value: Unmanaged<CFString>?, size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let status = AudioObjectGetPropertyData(id, &address, 0, nil, &size, &value)
        guard status == noErr, let value else { value?.release(); return nil }
        return value.takeRetainedValue() as String
    }
    private func hasOutput(_ id: AudioObjectID) -> Bool {
        var address = Self.address(kAudioDevicePropertyStreams, scope: kAudioObjectPropertyScopeOutput), size: UInt32 = 0
        return AudioObjectGetPropertyDataSize(id, &address, 0, nil, &size) == noErr
            && size > 0 && size <= UInt32(64 * MemoryLayout<AudioStreamID>.size) && size % UInt32(MemoryLayout<AudioStreamID>.size) == 0
    }
}
