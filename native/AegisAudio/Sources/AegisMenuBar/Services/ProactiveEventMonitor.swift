import AegisAudioCore
import EventKit
import Foundation
import IOKit.ps
@preconcurrency import Network
import OSLog
import UserNotifications

@MainActor
final class ProactiveEventMonitor {
    private let logger = Logger(
        subsystem: "ai.aegis.menubar",
        category: "ProactiveEvents"
    )
    private let eventStore = EKEventStore()
    private var gate = ProactiveAlertGate()
    private var networkMonitor: NWPathMonitor?
    private var lastNetworkSatisfied: Bool?
    private var batterySource: CFRunLoopSource?
    private var memorySource: DispatchSourceMemoryPressure?
    private var observers: [any NSObjectProtocol] = []
    private var calendarTimer: Timer?
    private var calendarAlertedEventKeys: Set<String> = []
    private var enabled = false
    private var requestGeneration = 0

    func setEnabled(_ requested: Bool) async -> Bool {
        requestGeneration += 1
        let generation = requestGeneration
        guard requested else {
            stop()
            return false
        }
        if enabled { return true }
        guard await requestNotificationAuthorization() else {
            logger.notice("proactive_notifications_denied")
            return false
        }
        guard generation == requestGeneration else { return enabled }
        enabled = true
        startNetworkEvents()
        startBatteryEvents()
        startPerformanceEvents()
        await startCalendarEvents()
        guard generation == requestGeneration, enabled else { return false }
        logger.info("proactive_monitor_started")
        return true
    }

    private func stop() {
        guard enabled else { return }
        enabled = false
        networkMonitor?.cancel()
        networkMonitor = nil
        lastNetworkSatisfied = nil
        if let batterySource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), batterySource, .commonModes)
        }
        batterySource = nil
        memorySource?.cancel()
        memorySource = nil
        for observer in observers {
            NotificationCenter.default.removeObserver(observer)
        }
        observers.removeAll(keepingCapacity: false)
        calendarTimer?.invalidate()
        calendarTimer = nil
        calendarAlertedEventKeys.removeAll(keepingCapacity: false)
        gate.reset()
        logger.info("proactive_monitor_stopped")
    }

    private func requestNotificationAuthorization() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional:
            return true
        case .notDetermined:
            return (try? await center.requestAuthorization(options: [.alert, .sound])) == true
        case .denied, .ephemeral:
            return false
        @unknown default:
            return false
        }
    }

    private func startNetworkEvents() {
        let monitor = NWPathMonitor()
        monitor.pathUpdateHandler = { [weak self] path in
            let satisfied = path.status == .satisfied
            Task { @MainActor [weak self] in
                self?.handleNetwork(satisfied: satisfied)
            }
        }
        monitor.start(queue: DispatchQueue(label: "ai.aegis.proactive.network", qos: .utility))
        networkMonitor = monitor
    }

    private func handleNetwork(satisfied: Bool) {
        guard enabled else { return }
        defer { lastNetworkSatisfied = satisfied }
        guard let previous = lastNetworkSatisfied, previous != satisfied else { return }
        emit(
            key: satisfied ? "network-restored" : "network-offline",
            body: satisfied
                ? "La conexión de red volvió a estar disponible."
                : "El Mac perdió la conexión de red.",
            cooldown: 300
        )
    }

    private func startBatteryEvents() {
        let context = Unmanaged.passUnretained(self).toOpaque()
        guard let unmanaged = IOPSNotificationCreateRunLoopSource({ context in
            guard let context else { return }
            let monitor = Unmanaged<ProactiveEventMonitor>
                .fromOpaque(context)
                .takeUnretainedValue()
            Task { @MainActor in monitor.evaluateBattery() }
        }, context) else {
            logger.error("battery_event_source_unavailable")
            return
        }
        let source = unmanaged.takeRetainedValue()
        batterySource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        evaluateBattery()
    }

    private func evaluateBattery() {
        guard enabled, let state = Self.batteryState(), !state.charging else { return }
        if state.percent <= 10 {
            emit(
                key: "battery-critical",
                body: "Batería crítica: queda (state.percent) %. Conecta el cargador.",
                cooldown: 7_200
            )
        } else if state.percent <= 20 {
            emit(
                key: "battery-low",
                body: "Batería baja: queda (state.percent) %.",
                cooldown: 14_400
            )
        }
    }

    nonisolated private static func batteryState() -> (percent: Int, charging: Bool)? {
        guard let snapshot = IOPSCopyPowerSourcesInfo()?.takeRetainedValue() else { return nil }
        guard let sources = IOPSCopyPowerSourcesList(snapshot)?.takeRetainedValue() as? [CFTypeRef]
        else {
            return nil
        }
        for source in sources {
            guard
                let description = IOPSGetPowerSourceDescription(snapshot, source)?
                    .takeUnretainedValue() as? [String: Any],
                let current = description[kIOPSCurrentCapacityKey] as? Int,
                let maximum = description[kIOPSMaxCapacityKey] as? Int,
                maximum > 0
            else {
                continue
            }
            let percent = min(100, max(0, Int((Double(current) / Double(maximum) * 100).rounded())))
            let state = description[kIOPSPowerSourceStateKey] as? String
            return (percent, state == kIOPSACPowerValue)
        }
        return nil
    }

    private func startPerformanceEvents() {
        let source = DispatchSource.makeMemoryPressureSource(
            eventMask: [.warning, .critical],
            queue: DispatchQueue(label: "ai.aegis.proactive.memory", qos: .utility)
        )
        source.setEventHandler { [weak self, weak source] in
            let event = source?.data ?? []
            Task { @MainActor [weak self] in
                self?.handleMemoryPressure(event)
            }
        }
        source.resume()
        memorySource = source
        observers.append(
            NotificationCenter.default.addObserver(
                forName: ProcessInfo.thermalStateDidChangeNotification,
                object: ProcessInfo.processInfo,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in self?.handleThermalState() }
            }
        )
    }

    private func handleMemoryPressure(_ event: DispatchSource.MemoryPressureEvent) {
        guard enabled else { return }
        if event.contains(.critical) {
            emit(
                key: "memory-critical",
                body: "La presión de memoria es crítica. Conviene cerrar tareas pesadas.",
                cooldown: 1_800
            )
        } else if event.contains(.warning) {
            emit(
                key: "memory-warning",
                body: "El Mac está bajo presión de memoria.",
                cooldown: 1_800
            )
        }
    }

    private func handleThermalState() {
        guard enabled else { return }
        switch ProcessInfo.processInfo.thermalState {
        case .serious:
            emit(
                key: "thermal-serious",
                body: "El Mac está bajo presión térmica; Jarvis reducirá el trabajo en segundo plano.",
                cooldown: 1_800
            )
        case .critical:
            emit(
                key: "thermal-critical",
                body: "La presión térmica es crítica. Pausa las cargas intensivas.",
                cooldown: 1_800
            )
        case .nominal, .fair:
            break
        @unknown default:
            break
        }
    }

    private func startCalendarEvents() async {
        let status = EKEventStore.authorizationStatus(for: .event)
        let granted: Bool
        switch status {
        case .fullAccess:
            granted = true
        case .notDetermined:
            granted = (try? await eventStore.requestFullAccessToEvents()) == true
        case .denied, .restricted, .writeOnly:
            granted = false
        @unknown default:
            granted = false
        }
        guard granted, enabled else { return }
        observers.append(
            NotificationCenter.default.addObserver(
                forName: .EKEventStoreChanged,
                object: eventStore,
                queue: .main
            ) { [weak self] _ in
                Task { @MainActor [weak self] in self?.scheduleNextCalendarAlert() }
            }
        )
        scheduleNextCalendarAlert()
    }

    private func scheduleNextCalendarAlert() {
        calendarTimer?.invalidate()
        calendarTimer = nil
        guard enabled else { return }
        let now = Date()
        let end = now.addingTimeInterval(604_800)
        let predicate = eventStore.predicateForEvents(withStart: now, end: end, calendars: nil)
        guard let event = eventStore.events(matching: predicate)
            .filter({ !$0.isAllDay && $0.endDate > now })
            .sorted(by: { $0.startDate < $1.startDate })
            .first(where: { event in
                let key = event.eventIdentifier
                    ?? event.startDate.timeIntervalSince1970.description
                return !calendarAlertedEventKeys.contains("calendar-\(key)")
            })
        else {
            let nextRefresh = Calendar.current
                .date(byAdding: .day, value: 1, to: now) ?? now.addingTimeInterval(86_400)
            let timer = Timer(fire: nextRefresh, interval: 0, repeats: false) { [weak self] _ in
                Task { @MainActor [weak self] in self?.scheduleNextCalendarAlert() }
            }
            calendarTimer = timer
            RunLoop.main.add(timer, forMode: .common)
            return
        }
        guard calendarAlertedEventKeys.count < 512 else { return }
        let eventKey = "calendar-\(event.eventIdentifier ?? event.startDate.timeIntervalSince1970.description)"
        let alertDate = max(now, event.startDate.addingTimeInterval(-600))
        let timer = Timer(fire: alertDate, interval: 0, repeats: false) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.calendarAlertedEventKeys.insert(eventKey)
                self.emit(
                    key: eventKey,
                    body: "Tienes un evento próximo en el calendario.",
                    cooldown: 43_200
                )
                self.scheduleNextCalendarAlert()
            }
        }
        calendarTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func emit(key: String, body: String, cooldown: TimeInterval) {
        guard enabled, gate.shouldEmit(key: key, cooldown: cooldown) else { return }
        let content = UNMutableNotificationContent()
        content.title = "Jarvis"
        content.body = body
        content.sound = .default
        content.threadIdentifier = "ai.aegis.proactive"
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(
                identifier: "ai.aegis.proactive.\(UUID().uuidString)",
                content: content,
                trigger: nil
            )
        ) { [logger] error in
            if let error {
                logger.error("notification_delivery_failed error=\(error.localizedDescription, privacy: .public)")
            }
        }
    }
}
