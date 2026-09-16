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

private let voiceHotKeyHandler: EventHandlerUPP = { _, _, context in
    guard let context else {
        return OSStatus(eventNotHandledErr)
    }
    let controller = Unmanaged<VoiceHotKeyController>
        .fromOpaque(context)
        .takeUnretainedValue()
    Task { @MainActor in
        controller.trigger()
    }
    return noErr
}

@MainActor
final class VoiceHotKeyController {
    static let shared = VoiceHotKeyController()

    private static let signature: OSType = 0x4145_4753 // AEGS
    private var eventHandler: EventHandlerRef?
    private var hotKey: EventHotKeyRef?
    private var action: (@MainActor () -> Void)?
    private let logger = Logger(subsystem: "ai.aegis.menubar", category: "VoiceHotKey")

    private init() {}

    func install(action: @escaping @MainActor () -> Void) -> Bool {
        self.action = action
        if hotKey != nil {
            return true
        }

        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let handlerStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            voiceHotKeyHandler,
            1,
            &eventType,
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
        action?()
    }

    func setShortcut(_ shortcut: JarvisVoiceShortcut) -> Bool {
        guard let action else { return false }
        let previous = JarvisVoiceShortcut.current
        if let hotKey { UnregisterEventHotKey(hotKey) }
        hotKey = nil
        if let eventHandler { RemoveEventHandler(eventHandler) }
        eventHandler = nil
        UserDefaults.standard.set(shortcut.rawValue, forKey: JarvisVoiceShortcut.defaultsKey)
        if install(action: action) { return true }
        UserDefaults.standard.set(previous.rawValue, forKey: JarvisVoiceShortcut.defaultsKey)
        _ = install(action: action)
        return false
    }
}
