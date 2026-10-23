import AppKit
import SwiftUI
import XCTest
@testable import AegisMenuBar

final class ChatComposerAvailabilityTests: XCTestCase {
    func testDraftUsesUTF8BudgetAndAllowsAttachmentOnlyMessages() {
        XCTAssertFalse(JarvisComposerAvailability.validDraft(" \n\t", hasAttachments: false))
        XCTAssertTrue(JarvisComposerAvailability.validDraft("", hasAttachments: true))
        XCTAssertTrue(JarvisComposerAvailability.validDraft(String(repeating: "é", count: 2_048), hasAttachments: false))
        XCTAssertFalse(JarvisComposerAvailability.validDraft(String(repeating: "é", count: 2_049), hasAttachments: true))
    }

    func testReturnUsesQueueOnlyDuringRunningResponseAndHonorsAllBlockingStates() {
        func action(running: Bool = false, cancelling: Bool = false, canSend: Bool = true,
                    canQueue: Bool = true, attachments: Bool = false,
                    interactive: Bool = true) -> JarvisComposerAvailability.Submission? {
            JarvisComposerAvailability.submission("Mensaje sintético", hasAttachments: attachments,
                running: running, cancelling: cancelling, canSend: canSend,
                canQueue: canQueue, interactive: interactive)
        }
        XCTAssertEqual(action(), .send)
        XCTAssertEqual(action(running: true, canSend: false), .queue)
        XCTAssertNil(action(canSend: false))
        XCTAssertNil(action(running: true, cancelling: true))
        XCTAssertNil(action(running: true, canQueue: false))
        XCTAssertNil(action(running: true, attachments: true))
        XCTAssertNil(action(interactive: false))
        XCTAssertNil(action(running: true, interactive: false))
    }

    func testPlaceholderExplainsCurrentSubmissionContext() {
        XCTAssertEqual(JarvisComposerAvailability.placeholder(running: false, planning: false, inProject: false),
                       "Pregúntale a Jarvis…")
        XCTAssertEqual(JarvisComposerAvailability.placeholder(running: false, planning: false, inProject: true),
                       "Describe lo que necesitas para este proyecto…")
        XCTAssertEqual(JarvisComposerAvailability.placeholder(running: false, planning: true, inProject: true),
                       "¿Qué quieres planificar?")
        XCTAssertEqual(JarvisComposerAvailability.placeholder(running: true, planning: true, inProject: true),
                       "Añade una indicación para después…")
    }
}

@MainActor
final class ChatComposerKeyboardTests: XCTestCase {
    private func waitUntil(_ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(2)
        while !condition(), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(20))
        }
    }

    private func descendants(_ view: NSView) -> [NSView] {
        [view] + view.subviews.flatMap(descendants)
    }

    private func editor() -> (NSWindow, JarvisComposerNativeTextView) {
        _ = NSApplication.shared
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 160),
            styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.title = "Jarvis · editor sintético"
        let editor = JarvisComposerNativeTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 160))
        editor.isRichText = false
        editor.isEditable = true
        window.contentView = editor
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(editor)
        return (window, editor)
    }

    private func key(_ editor: JarvisComposerNativeTextView, modifiers: NSEvent.ModifierFlags = [],
                     code: UInt16 = 36, repeatKey: Bool = false) throws {
        let event = try XCTUnwrap(NSEvent.keyEvent(with: .keyDown, location: .zero,
            modifierFlags: modifiers, timestamp: ProcessInfo.processInfo.systemUptime,
            windowNumber: editor.window?.windowNumber ?? 0, context: nil, characters: "\r",
            charactersIgnoringModifiers: "\r", isARepeat: repeatKey, keyCode: code))
        editor.keyDown(with: event)
    }

    func testReturnAndCommandReturnSubmitWithoutAddingNewlines() throws {
        let (window, editor) = editor()
        defer { window.close() }
        var submissions = 0
        editor.string = "Texto sintético"
        editor.onSubmit = { submissions += 1 }
        try key(editor)
        try key(editor, modifiers: .command)
        try key(editor, code: 76)
        XCTAssertEqual(submissions, 3)
        XCTAssertEqual(editor.string, "Texto sintético")
    }

    func testShiftReturnInsertsNewlineAndDoesNotSubmit() throws {
        let (window, editor) = editor()
        defer { window.close() }
        var submissions = 0
        editor.string = "Texto sintético"
        editor.setSelectedRange(NSRange(location: (editor.string as NSString).length, length: 0))
        editor.onSubmit = { submissions += 1 }
        try key(editor, modifiers: .shift)
        XCTAssertEqual(submissions, 0)
        XCTAssertEqual(editor.string, "Texto sintético\n")
    }

    func testReturnDoesNotSubmitWhileIMECompositionIsMarked() throws {
        let (window, editor) = editor()
        defer { window.close() }
        var submissions = 0
        editor.onSubmit = { submissions += 1 }
        editor.setMarkedText("に", selectedRange: NSRange(location: 1, length: 0),
                             replacementRange: NSRange(location: NSNotFound, length: 0))
        XCTAssertTrue(editor.hasMarkedText())
        try key(editor)
        XCTAssertEqual(submissions, 0)
    }

    func testDisabledEditorAndHeldReturnNeverSubmit() throws {
        let (window, editor) = editor()
        defer { window.close() }
        var submissions = 0
        editor.onSubmit = { submissions += 1 }
        try key(editor, repeatKey: true)
        editor.isEditable = false
        try key(editor)
        try key(editor, modifiers: .command)
        XCTAssertEqual(submissions, 0)
    }

    func testDraftBindingAndConversationReplacementClearPreviousUndo() async throws {
        let (window, unusedEditor) = editor()
        defer { window.close() }
        unusedEditor.onSubmit = nil
        var draft = "Primer borrador sintético"
        var focused = true
        let view = JarvisComposerTextEditor(
            text: Binding(get: { draft }, set: { draft = $0 }),
            focused: Binding(get: { focused }, set: { focused = $0 }),
            draftIdentity: UUID(),
            interactive: true, onSubmit: {})
        let host = NSHostingView(rootView: view)
        window.contentView = host
        window.setContentSize(NSSize(width: 400, height: 100))
        // SwiftUI applies updates on its own schedule; a fixed 150 ms wait failed under
        // the full parallel suite, so wait for the change itself (bounded).
        try await waitUntil {
            self.descendants(host).contains { $0 is JarvisComposerNativeTextView }
        }
        let editor = try XCTUnwrap(descendants(host).compactMap { $0 as? JarvisComposerNativeTextView }.first)
        XCTAssertEqual(editor.string, draft)
        XCTAssertGreaterThan(editor.bounds.width, 200)
        window.makeFirstResponder(editor)
        editor.insertText(" añadido", replacementRange: NSRange(location: (draft as NSString).length, length: 0))
        XCTAssertEqual(draft, "Primer borrador sintético añadido")
        XCTAssertTrue(editor.undoManager?.canUndo == true)
        draft = "Segundo chat sintético"
        // A conversation switch renders the composer with the new chat's draft identity.
        // Reassigning the identical view left the update to SwiftUI's discretion, which
        // made this test fail about one run in three.
        host.rootView = JarvisComposerTextEditor(
            text: Binding(get: { draft }, set: { draft = $0 }),
            focused: Binding(get: { focused }, set: { focused = $0 }),
            draftIdentity: UUID(),
            interactive: true, onSubmit: {})
        try await waitUntil { editor.string == "Segundo chat sintético" }
        XCTAssertEqual(editor.string, "Segundo chat sintético")
        XCTAssertFalse(editor.undoManager?.canUndo == true, "Deshacer no debe recuperar texto de otro chat")
        XCTAssertEqual(editor.selectedRange().location, (draft as NSString).length)
    }

    func testRenderBetweenNativeFocusLossAndBindingUpdateDoesNotReclaimFocus() async throws {
        let (window, editor) = editor()
        defer { window.close() }
        let container = NSView(frame: window.contentView!.bounds)
        let search = NSTextField(frame: NSRect(x: 0, y: 120, width: 300, height: 24))
        window.contentView = container
        container.addSubview(editor)
        container.addSubview(search)
        editor.frame = NSRect(x: 0, y: 0, width: 400, height: 100)
        var focused = true
        let view = JarvisComposerTextEditor(text: .constant("Borrador sintético"),
            focused: Binding(get: { focused }, set: { focused = $0 }),
            draftIdentity: UUID(), interactive: true, onSubmit: {})
        let coordinator = view.makeCoordinator()
        coordinator.update(editor, using: view)
        XCTAssertTrue(window.firstResponder === editor)
        window.makeFirstResponder(search)
        XCTAssertNotNil(search.currentEditor())
        XCTAssertTrue(focused, "El callback nativo aún no ha publicado la pérdida de foco")
        // The exact production synchronization used by updateNSView, before yielding
        // to the Task that propagates the AppKit focus notification.
        coordinator.update(editor, using: view)
        XCTAssertNotNil(search.currentEditor(), "Un render no debe robar foco a la búsqueda")
        try await Task.sleep(for: .milliseconds(30))
        XCTAssertFalse(focused)
        coordinator.update(editor, using: view)
        XCTAssertNotNil(search.currentEditor())
    }

    func testRenderBetweenNativeFocusGainAndBindingUpdateDoesNotDismissFocus() async throws {
        let (window, editor) = editor()
        defer { window.close() }
        var focused = false
        let view = JarvisComposerTextEditor(text: .constant("Borrador sintético"),
            focused: Binding(get: { focused }, set: { focused = $0 }),
            draftIdentity: UUID(), interactive: true, onSubmit: {})
        let coordinator = view.makeCoordinator()
        coordinator.update(editor, using: view)
        XCTAssertFalse(window.firstResponder === editor)
        window.makeFirstResponder(editor)
        XCTAssertFalse(focused)
        coordinator.update(editor, using: view)
        XCTAssertTrue(window.firstResponder === editor)
        try await Task.sleep(for: .milliseconds(30))
        XCTAssertTrue(focused)
    }

    func testIdenticalDraftInAnotherConversationCannotUndoPreviousConversationEdits() throws {
        let (window, editor) = editor()
        defer { window.close() }
        editor.allowsUndo = true
        var draft = "Borrador idéntico"
        let first = JarvisComposerTextEditor(text: Binding(get: { draft }, set: { draft = $0 }),
            focused: .constant(true), draftIdentity: UUID(), interactive: true, onSubmit: {})
        let coordinator = first.makeCoordinator()
        editor.delegate = coordinator
        coordinator.update(editor, using: first)
        editor.insertText("X", replacementRange: NSRange(location: 0, length: 0))
        editor.insertText("", replacementRange: NSRange(location: 0, length: 1))
        XCTAssertEqual(draft, "Borrador idéntico")
        XCTAssertTrue(editor.undoManager?.canUndo == true)
        coordinator.update(editor, using: first)
        XCTAssertTrue(editor.undoManager?.canUndo == true, "Un render del mismo chat conserva deshacer")
        var second = first
        second.draftIdentity = UUID()
        coordinator.update(editor, using: second)
        XCTAssertEqual(editor.string, "Borrador idéntico")
        XCTAssertFalse(editor.undoManager?.canUndo == true)
    }
}
