import Foundation

public struct IPCRuntimePowerStatus: Equatable, Sendable {
    public let thermalState: AudioRuntimeThermalState
    public let lowPowerMode: Bool
    public let powerSource: AudioRuntimePowerSource

    public init?(response: LocalIPCResponse) {
        guard response.ok,
              let state = response.payload["state"] as? String,
              let rawThermal = response.payload["thermal_state"] as? String,
              let thermal = AudioRuntimeThermalState(rawValue: rawThermal),
              let lowPower = response.payload["low_power_mode"] as? Bool,
              let rawSource = response.payload["power_source"] as? String,
              let source = AudioRuntimePowerSource(rawValue: rawSource),
              state == ((!thermal.allowsFullRuntime || lowPower) ? "suspended" : "active")
        else { return nil }
        thermalState = thermal
        lowPowerMode = lowPower
        powerSource = source
    }

    public func matches(
        thermalState: AudioRuntimeThermalState,
        lowPowerMode: Bool,
        powerSource: AudioRuntimePowerSource
    ) -> Bool {
        self.thermalState == thermalState
            && self.lowPowerMode == lowPowerMode
            && self.powerSource == powerSource
    }
}
