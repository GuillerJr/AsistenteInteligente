import AegisAudioCore
import Foundation
import IOKit.ps
import OSLog

struct ThermalBudgetSnapshot: Equatable, Sendable {
    let thermalState: AudioRuntimeThermalState
    let lowPowerMode: Bool
    let powerSource: AudioRuntimePowerSource

    var throttled: Bool {
        !thermalState.allowsFullRuntime || lowPowerMode
    }

    var transitionCause: AudioRuntimeTransitionCause {
        if !thermalState.allowsFullRuntime { return .thermalThrottle }
        if lowPowerMode { return .lowPowerMode }
        return .thermalRecovery
    }
}

@MainActor
final class ThermalMonitor {
    typealias Handler = @MainActor @Sendable (ThermalBudgetSnapshot) -> Void

    private let logger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "ThermalBudget"
    )
    private var observers: [any NSObjectProtocol] = []
    private var powerSourceObserver: CFRunLoopSource?
    private var handler: Handler?
    private var lastSnapshot: ThermalBudgetSnapshot?

    func start(_ handler: @escaping Handler) {
        self.handler = handler
        guard observers.isEmpty, powerSourceObserver == nil else {
            publishIfChanged()
            return
        }
        let center = NotificationCenter.default
        observers = [
            center.addObserver(
                forName: ProcessInfo.thermalStateDidChangeNotification,
                object: ProcessInfo.processInfo,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in self?.publishIfChanged() }
            },
            center.addObserver(
                forName: .NSProcessInfoPowerStateDidChange,
                object: ProcessInfo.processInfo,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in self?.publishIfChanged() }
            },
        ]
        let context = Unmanaged.passUnretained(self).toOpaque()
        if let unmanaged = IOPSNotificationCreateRunLoopSource({ context in
            guard let context else { return }
            let monitor = Unmanaged<ThermalMonitor>.fromOpaque(context).takeUnretainedValue()
            Task { @MainActor in monitor.publishIfChanged() }
        }, context) {
            let source = unmanaged.takeRetainedValue()
            powerSourceObserver = source
            CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        } else {
            logger.error("power_source_notification_unavailable")
        }
        publishIfChanged()
    }

    func stop() {
        for observer in observers {
            NotificationCenter.default.removeObserver(observer)
        }
        observers.removeAll(keepingCapacity: false)
        if let powerSourceObserver {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), powerSourceObserver, .commonModes)
        }
        powerSourceObserver = nil
        handler = nil
        lastSnapshot = nil
    }

    static func currentSnapshot() -> ThermalBudgetSnapshot {
        ThermalBudgetSnapshot(
            thermalState: thermalState(ProcessInfo.processInfo.thermalState),
            lowPowerMode: ProcessInfo.processInfo.isLowPowerModeEnabled,
            powerSource: currentPowerSource()
        )
    }

    private func publishIfChanged() {
        let snapshot = Self.currentSnapshot()
        guard snapshot != lastSnapshot else { return }
        lastSnapshot = snapshot
        logger.info(
            "thermal_budget_changed state=\(snapshot.thermalState.rawValue, privacy: .public) low_power=\(snapshot.lowPowerMode, privacy: .public) source=\(snapshot.powerSource.rawValue, privacy: .public)"
        )
        handler?(snapshot)
    }

    private static func thermalState(
        _ state: ProcessInfo.ThermalState
    ) -> AudioRuntimeThermalState {
        switch state {
        case .nominal: .nominal
        case .fair: .fair
        case .serious: .serious
        case .critical: .critical
        @unknown default: .unknown
        }
    }

    private static func currentPowerSource() -> AudioRuntimePowerSource {
        guard
            let snapshot = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
            let raw = IOPSGetProvidingPowerSourceType(snapshot)?.takeUnretainedValue()
                as? String
        else { return .unknown }
        switch raw {
        case kIOPSACPowerValue: return .ac
        case kIOPSBatteryPowerValue: return .battery
        case kIOPMUPSPowerKey: return .ups
        default: return .unknown
        }
    }
}
