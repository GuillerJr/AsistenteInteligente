import Foundation
import Testing
@testable import AegisAudioCore

private func powerStatusResponse(_ changes: [String: Any] = [:], ok: Bool = true) -> LocalIPCResponse {
    var payload: [String: Any] = [
        "state": "active", "thermal_state": "nominal",
        "low_power_mode": false, "power_source": "ac",
    ]
    payload.merge(changes) { _, next in next }
    return LocalIPCResponse(requestID: UUID(), ok: ok, payload: payload, errorCode: nil)
}

@Test func ipcPowerStatusDetectsStalePauseAndChangedPowerSource() throws {
    let stale = try #require(IPCRuntimePowerStatus(response: powerStatusResponse([
        "state": "active", "low_power_mode": true, "power_source": "battery",
    ])))
    #expect(!stale.matches(thermalState: .nominal, lowPowerMode: false, powerSource: .ac))
    #expect(stale.matches(thermalState: .nominal, lowPowerMode: true, powerSource: .battery))
    let current = try #require(IPCRuntimePowerStatus(response: powerStatusResponse()))
    #expect(current.matches(thermalState: .nominal, lowPowerMode: false, powerSource: .ac))
    #expect(!current.matches(thermalState: .nominal, lowPowerMode: false, powerSource: .battery))
    #expect(!current.matches(thermalState: .serious, lowPowerMode: false, powerSource: .ac))
}

@Test func ipcPowerStatusRejectsMalformedOrInconsistentRecovery() {
    #expect(IPCRuntimePowerStatus(response: powerStatusResponse(ok: false)) == nil)
    let invalidStates: [[String: Any]] = [
        ["state": "suspended", "low_power_mode": true],
        ["state": "active", "thermal_state": "critical"],
        ["state": "suspended"], ["state": "unknown"],
        ["thermal_state": "invalid"], ["low_power_mode": "false"],
        ["power_source": "invalid"],
    ]
    for invalid in invalidStates {
        #expect(IPCRuntimePowerStatus(response: powerStatusResponse(invalid)) == nil)
    }
    #expect(IPCRuntimePowerStatus(response: powerStatusResponse([
        "state": "suspended", "thermal_state": "critical",
    ])) != nil)
    #expect(IPCRuntimePowerStatus(response: powerStatusResponse([
        "state": "suspended", "thermal_state": "serious", "low_power_mode": true,
    ])) != nil)
}

@Test func ipcLowPowerModeKeepsManualRuntimeAndPausesOnlyWakeWord() {
    #expect(!AudioRuntimeTransitionCause.lowPowerMode.suspendsRuntime)
    #expect(AudioRuntimeTransitionCause.thermalThrottle.suspendsRuntime)
    #expect(!WakeWordEnergyPolicy.allowsListening(lowPowerModeEnabled: true))
    #expect(WakeWordEnergyPolicy.allowsListening(lowPowerModeEnabled: false))
    #expect(IPCRuntimePowerStatus(response: powerStatusResponse([
        "low_power_mode": true,
    ])) != nil)
}
