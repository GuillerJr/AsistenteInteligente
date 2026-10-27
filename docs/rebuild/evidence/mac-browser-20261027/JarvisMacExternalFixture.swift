import AppKit
import Darwin
import Foundation

/// Independent test target. It does not import Jarvis, use AX, request TCC,
/// open documents, capture images, read credentials or make network requests.
@MainActor
final class JarvisExternalFixture: NSObject, NSApplicationDelegate, NSWindowDelegate, NSTextViewDelegate {
    static let root = "/private/tmp/jarvis-mac-external-fixture-20261027"
    static let bundleID = "com.jarvis.fixture.macexternal.20261027"
    private let editor = NSTextView(frame: NSRect(x: 20, y: 110, width: 520, height: 240))
    private let counter = NSTextField(labelWithString: "CONTADOR 0")
    private var window: NSWindow?
    private var timer: Timer?
    private var count = 0
    private var revision = 0
    private var closing = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let window = NSWindow(contentRect: NSRect(x: 180, y: 200, width: 560, height: 380),
            styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Documento externo sintético Jarvis"
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.fixture.external.window")
        window.isReleasedWhenClosed = false
        window.delegate = self
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 560, height: 380))

        editor.isRichText = false
        editor.string = "JARVIS EXTERNO BASE"
        editor.isAutomaticQuoteSubstitutionEnabled = false
        editor.isAutomaticDashSubstitutionEnabled = false
        editor.setAccessibilityLabel("Editor externo Jarvis")
        editor.setAccessibilityIdentifier("jarvis.fixture.external.editor")
        editor.delegate = self
        editor.setSelectedRange(NSRange(location: (editor.string as NSString).length, length: 0))
        content.addSubview(editor)

        counter.frame = NSRect(x: 20, y: 66, width: 210, height: 24)
        counter.setAccessibilityIdentifier("jarvis.fixture.external.counter")
        content.addSubview(counter)
        let button = NSButton(title: "Sumar marcador externo", target: self, action: #selector(increment))
        button.frame = NSRect(x: 250, y: 60, width: 280, height: 34)
        button.setAccessibilityIdentifier("jarvis.fixture.external.increment")
        content.addSubview(button)

        let secure = NSSecureTextField(frame: NSRect(x: 20, y: 20, width: 300, height: 26))
        secure.stringValue = ""
        secure.placeholderString = "Campo protegido sintético"
        secure.setAccessibilityLabel("Campo protegido sintético")
        secure.setAccessibilityIdentifier("jarvis.fixture.external.secure")
        content.addSubview(secure)
        window.contentView = content
        self.window = window

        let main = NSMenu()
        let item = NSMenuItem(); main.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Salir de Jarvis Fixture Externo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        NSApp.mainMenu = main
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(editor)
        _ = NSRunningApplication.current.activate(options: [])
        publish()
        let timer = Timer(timeInterval: 0.25, target: self, selector: #selector(tick), userInfo: nil, repeats: true)
        self.timer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    @objc private func increment() { count += 1; counter.stringValue = "CONTADOR \(count)"; publish() }
    @objc private func tick() { publish() }
    func textDidChange(_ notification: Notification) { publish() }
    func textViewDidChangeSelection(_ notification: Notification) { publish() }
    func windowWillClose(_ notification: Notification) {
        closing = true; timer?.invalidate(); timer = nil
        publish()
        NSApp.terminate(nil)
    }
    func applicationWillTerminate(_ notification: Notification) {
        closing = true; timer?.invalidate(); timer = nil
        publish()
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func publish() {
        guard let window else { return }
        revision += 1
        let range = editor.selectedRange()
        // The secure text field is deliberately never read by this oracle.
        let state: [String: Any] = ["format": "jarvis-mac-external-fixture/1", "synthetic_only": true,
            "bundle_id": Self.bundleID, "pid": getpid(), "window_number": window.windowNumber,
            "editor_value": editor.string, "editor_utf16_length": (editor.string as NSString).length,
            "counter": count, "counter_value": counter.stringValue,
            "selected_range": [range.location, range.length], "revision": revision,
            "state_status": closing ? "closed" : "running", "secure_value_recorded": false,
            "source": "independent_AppKit_objects_not_Jarvis_receipt"]
        do {
            let data = try JSONSerialization.data(withJSONObject: state, options: [.sortedKeys, .withoutEscapingSlashes])
            try writePrivateAtomically(data)
        } catch {
            fputs("jarvis_external_fixture_state_write_failed\n", stderr)
            exit(2)
        }
    }

    private func writePrivateAtomically(_ data: Data) throws {
        let parent = open(Self.root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)
        guard parent >= 0 else { throw CocoaError(.fileWriteUnknown) }
        defer { close(parent) }
        var info = stat()
        guard fstat(parent, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o777 == 0o700 else {
            throw CocoaError(.fileWriteNoPermission)
        }
        let staging = ".state-" + UUID().uuidString
        let fd = openat(parent, staging, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw CocoaError(.fileWriteUnknown) }
        defer { close(fd); unlinkat(parent, staging, 0) }
        guard fchmod(fd, 0o600) == 0 else { throw CocoaError(.fileWriteNoPermission) }
        try data.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                let written = write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset)
                if written < 0 && errno == EINTR { continue }
                guard written > 0 else { throw CocoaError(.fileWriteUnknown) }
                offset += written
            }
        }
        guard fsync(fd) == 0, renameat(parent, staging, parent, "state.json") == 0,
              fsync(parent) == 0 else { throw CocoaError(.fileWriteUnknown) }
    }
}

@main
struct JarvisMacExternalFixtureMain {
    @MainActor static func main() {
        umask(0o077)
        let app = NSApplication.shared
        let delegate = JarvisExternalFixture()
        app.delegate = delegate
        _ = app.setActivationPolicy(.regular)
        app.run()
        withExtendedLifetime(delegate) {}
    }
}
