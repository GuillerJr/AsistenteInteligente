import Carbon.HIToolbox
import OSLog
import Foundation

enum JarvisVoiceShortcut: String, CaseIterable, Identifiable {
    case controlShiftSpace, optionShiftSpace
    static let defaultsKey = "ai.aegis.voice.shortcut"
    static var current: Self {
        Self(rawValue: UserDefaults.standard.string(forKey: defaultsKey) ?? "") ?? .controlShiftSpace
    }
    var id: String { rawValue }
    var title: String {
        self == .controlShiftSpace ? "⌃⇧ Espacio" : "⌥⇧ Espacio"
    }
    var modifiers: UInt32 {
        UInt32((self == .controlShiftSpace ? controlKey : optionKey) | shiftKey)
    }
}

private let voiceHotKeyHandler: EventHandlerUPP = { _, event, context in
    guard let context, let event else {
        return OSStatus(eventNotHandledErr)
    }
    let controller = Unmanaged<VoiceHotKeyController>
        .fromOpaque(context)
        .takeUnretainedValue()
    var hotKeyID = EventHotKeyID()
    GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                      nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
    let released = GetEventKind(event) == UInt32(kEventHotKeyReleased)
    let identifier = hotKeyID.id
    Task { @MainActor in
        if identifier == VoiceHotKeyController.quickEntryID {
            if !released { controller.triggerQuickEntry() }
        } else if released {
            controller.released()
        } else {
            controller.trigger()
        }
    }
    return noErr
}

@MainActor
final class VoiceHotKeyController {
    static let shared = VoiceHotKeyController()

    private static let signature: OSType = 0x4145_4753 // AEGS
    static let quickEntryID: UInt32 = 2
    /// Holding the voice key this long makes it push-to-talk instead of a tap.
    static let holdThreshold: Duration = .milliseconds(400)
    private var eventHandler: EventHandlerRef?
    private var hotKey: EventHotKeyRef?
    private var quickEntryHotKey: EventHotKeyRef?
    private var action: (@MainActor () -> Void)?
    private var holdStarted: (@MainActor () -> Void)?
    private var holdEnded: (@MainActor () -> Void)?
    private var quickEntry: (@MainActor () -> Void)?
    private var pressedAt: ContinuousClock.Instant?
    private var holdTask: Task<Void, Never>?
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "VoiceHotKey")

    private init() {}

    /// Push-to-talk callbacks: the key held past the threshold, and then released.
    func installHold(started: @escaping @MainActor () -> Void,
                     ended: @escaping @MainActor () -> Void) {
        holdStarted = started
        holdEnded = ended
    }

    /// ⌃⇧J opens the quick text entry from anywhere (Claude's Quick Entry).
    @discardableResult
    func installQuickEntry(action: @escaping @MainActor () -> Void) -> Bool {
        quickEntry = action
        guard eventHandler != nil, quickEntryHotKey == nil else { return quickEntryHotKey != nil }
        let status = RegisterEventHotKey(
            UInt32(kVK_ANSI_J), UInt32(controlKey | shiftKey),
            EventHotKeyID(signature: Self.signature, id: Self.quickEntryID),
            GetApplicationEventTarget(), 0, &quickEntryHotKey
        )
        if status != noErr { logger.error("quick_entry_hotkey_registration_failed code=\(status)") }
        return status == noErr
    }

    func install(action: @escaping @MainActor () -> Void) -> Bool {
        self.action = action
        if hotKey != nil {
            return true
        }

        var eventTypes = [
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                          eventKind: UInt32(kEventHotKeyPressed)),
            EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                          eventKind: UInt32(kEventHotKeyReleased)),
        ]
        let handlerStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            voiceHotKeyHandler,
            eventTypes.count,
            &eventTypes,
            Unmanaged.passUnretained(self).toOpaque(),
            &eventHandler
        )
        guard handlerStatus == noErr else {
            logger.error("voice_hotkey_registration_failed stage=handler code=\(handlerStatus)")
            return false
        }

        let hotKeyID = EventHotKeyID(signature: Self.signature, id: 1)
        let hotKeyStatus = RegisterEventHotKey(
            UInt32(kVK_Space),
            JarvisVoiceShortcut.current.modifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &hotKey
        )
        guard hotKeyStatus == noErr else {
            if let eventHandler {
                RemoveEventHandler(eventHandler)
            }
            eventHandler = nil
            logger.error("voice_hotkey_registration_failed stage=shortcut code=\(hotKeyStatus)")
            return false
        }

        logger.info("voice_hotkey_registered")
        return true
    }

    fileprivate func trigger() {
        logger.info("voice_hotkey_triggered")
        pressedAt = .now
        holdTask?.cancel()
        holdTask = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: Self.holdThreshold) } catch { return }
            guard let self, self.pressedAt != nil else { return }
            self.logger.info("voice_hotkey_held")
            self.holdStarted?()
        }
        action?()
    }

    fileprivate func released() {
        guard let pressedAt else { return }
        self.pressedAt = nil
        holdTask?.cancel()
        holdTask = nil
        if ContinuousClock.now - pressedAt >= Self.holdThreshold {
            logger.info("voice_hotkey_released_after_hold")
            holdEnded?()
        }
    }

    fileprivate func triggerQuickEntry() {
        logger.info("quick_entry_hotkey_triggered")
        quickEntry?()
    }

    func setShortcut(_ shortcut: JarvisVoiceShortcut) -> Bool {
        guard let action else { return false }
        let previous = JarvisVoiceShortcut.current
        if let hotKey { UnregisterEventHotKey(hotKey) }
        hotKey = nil
        if let quickEntryHotKey { UnregisterEventHotKey(quickEntryHotKey) }
        quickEntryHotKey = nil
        if let eventHandler { RemoveEventHandler(eventHandler) }
        eventHandler = nil
        UserDefaults.standard.set(shortcut.rawValue, forKey: JarvisVoiceShortcut.defaultsKey)
        if install(action: action) {
            if let quickEntry { installQuickEntry(action: quickEntry) }
            return true
        }
        UserDefaults.standard.set(previous.rawValue, forKey: JarvisVoiceShortcut.defaultsKey)
        _ = install(action: action)
        return false
    }
}
