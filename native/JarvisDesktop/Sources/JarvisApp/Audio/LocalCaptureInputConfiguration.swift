@preconcurrency import AVFoundation
import AudioToolbox
import CoreAudio
import Foundation

/// Input identity remains in RAM. A configuration notification also describes
/// output changes, so the notification name alone is not evidence of retargeting.
struct LocalCaptureInputConfiguration: Equatable, Sendable {
    let deviceUID: String
    let defaultDeviceUID: String
    let sampleRate: Double
    let channels: UInt32
    let formatID: UInt32
    let formatFlags: UInt32
    let bytesPerPacket: UInt32
    let framesPerPacket: UInt32
    let bytesPerFrame: UInt32
    let bitsPerChannel: UInt32

    init?(deviceUID: String, defaultDeviceUID: String, format: AVAudioFormat) {
        guard !deviceUID.isEmpty, !defaultDeviceUID.isEmpty,
              deviceUID.utf8.count <= 4096, defaultDeviceUID.utf8.count <= 4096,
              format.sampleRate.isFinite, format.sampleRate > 0, format.sampleRate <= 96_000,
              (1...2).contains(format.channelCount) else { return nil }
        let description = format.streamDescription.pointee
        self.deviceUID = deviceUID; self.defaultDeviceUID = defaultDeviceUID
        sampleRate = format.sampleRate; channels = format.channelCount
        formatID = description.mFormatID; formatFlags = description.mFormatFlags
        bytesPerPacket = description.mBytesPerPacket; framesPerPacket = description.mFramesPerPacket
        bytesPerFrame = description.mBytesPerFrame; bitsPerChannel = description.mBitsPerChannel
    }

    /// Reads only the selected engine input and the current default input. This
    /// never enumerates devices, changes routing or starts an audio unit.
    @MainActor static func read(engine: AVAudioEngine, format: AVAudioFormat? = nil) -> Self? {
        let input = engine.inputNode
        guard let unit = input.audioUnit else { return nil }
        var selected = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
            kAudioUnitScope_Global, 0, &selected, &size) == noErr,
              size == UInt32(MemoryLayout<AudioDeviceID>.size), selected != kAudioObjectUnknown,
              let selectedUID = uid(selected) else { return nil }
        var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var currentDefault = AudioDeviceID(kAudioObjectUnknown)
        size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil,
            &size, &currentDefault) == noErr, size == UInt32(MemoryLayout<AudioDeviceID>.size),
              currentDefault != kAudioObjectUnknown, let defaultUID = uid(currentDefault) else { return nil }
        return Self(deviceUID: selectedUID, defaultDeviceUID: defaultUID,
                    format: format ?? input.outputFormat(forBus: 0))
    }

    private static func uid(_ device: AudioDeviceID) -> String? {
        var address = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var value: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value) == noErr,
              size == UInt32(MemoryLayout<Unmanaged<CFString>?>.size), let value else {
            value?.release(); return nil
        }
        return value.takeRetainedValue() as String
    }
}

struct LocalCaptureInputState: Equatable, Sendable {
    let configuration: LocalCaptureInputConfiguration
    let isRunning: Bool

    @MainActor static func read(engine: AVAudioEngine) -> Self? {
        guard let configuration = LocalCaptureInputConfiguration.read(engine: engine) else { return nil }
        return Self(configuration: configuration, isRunning: engine.isRunning)
    }
}
