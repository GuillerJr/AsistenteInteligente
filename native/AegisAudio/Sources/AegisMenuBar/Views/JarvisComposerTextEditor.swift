import AppKit
import SwiftUI

/// SwiftUI owns the draft. AppKit only provides the text system, IME and return-key behavior.
struct JarvisComposerTextEditor: NSViewRepresentable {
    @Binding var text: String
    @Binding var focused: Bool
    var draftIdentity: UUID
    var interactive: Bool
    var onSubmit: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.drawsBackground = false
        scroll.borderType = .noBorder
        scroll.hasVerticalScroller = true
        scroll.autohidesScrollers = true
        let editor = JarvisComposerNativeTextView(frame: .zero)
        editor.isRichText = false
        editor.importsGraphics = false
        editor.allowsUndo = true
        editor.drawsBackground = false
        editor.font = .systemFont(ofSize: 14)
        editor.textColor = .labelColor
        editor.insertionPointColor = .labelColor
        editor.textContainerInset = NSSize(width: 0, height: 5)
        editor.textContainer?.lineFragmentPadding = 0
        editor.isVerticallyResizable = true
        editor.isHorizontallyResizable = false
        editor.autoresizingMask = [.width]
        editor.textContainer?.widthTracksTextView = true
        editor.textContainer?.containerSize = NSSize(width: 0, height: CGFloat.greatestFiniteMagnitude)
        editor.setAccessibilityLabel(String(localized: "Mensaje para Jarvis", bundle: .module))
        editor.setAccessibilityHelp(String(localized: "Intro para enviar. Mayús-Intro para una nueva línea.", bundle: .module))
        editor.delegate = context.coordinator
        scroll.documentView = editor
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let editor = scroll.documentView as? JarvisComposerNativeTextView else { return }
        context.coordinator.update(editor, using: self)
    }

    static func dismantleNSView(_ scroll: NSScrollView, coordinator: Coordinator) {
        guard let editor = scroll.documentView as? JarvisComposerNativeTextView else { return }
        editor.delegate = nil
        editor.onSubmit = nil
        editor.onFocusChange = nil
    }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: JarvisComposerTextEditor
        private var lastRequestedFocus: Bool?
        private var lastDraftIdentity: UUID?
        init(_ parent: JarvisComposerTextEditor) { self.parent = parent }

        func update(_ editor: JarvisComposerNativeTextView, using value: JarvisComposerTextEditor) {
            parent = value
            editor.isEditable = parent.interactive
            editor.onSubmit = parent.interactive ? parent.onSubmit : nil
            editor.onFocusChange = { [weak self] value in self?.parent.focused = value }
            if editor.string != parent.text || lastDraftIdentity != parent.draftIdentity {
                // Even identical drafts belong to different undo histories after a
                // conversation switch. Unmark so an IME cannot restore the old draft.
                editor.unmarkText()
                editor.string = parent.text
                editor.setSelectedRange(NSRange(location: (parent.text as NSString).length, length: 0))
                editor.scrollRangeToVisible(editor.selectedRange())
                editor.undoManager?.removeAllActions()
            }
            lastDraftIdentity = parent.draftIdentity
            editor.wantsFocus = parent.interactive && parent.focused
            // Native responder changes publish their binding asynchronously. An unrelated
            // render in that gap must not reclaim focus from a search field or panel.
            guard lastRequestedFocus != editor.wantsFocus else { return }
            lastRequestedFocus = editor.wantsFocus
            editor.pendingFocusOnAttachment = editor.wantsFocus && editor.window == nil
            if editor.wantsFocus {
                if let window = editor.window, window.firstResponder !== editor {
                    window.makeFirstResponder(editor)
                }
            } else if editor.window?.firstResponder === editor {
                editor.window?.makeFirstResponder(nil)
            }
        }

        func textDidChange(_ notification: Notification) {
            guard let editor = notification.object as? NSTextView else { return }
            parent.text = editor.string
        }
    }
}

final class JarvisComposerNativeTextView: NSTextView {
    private let draftUndoManager = UndoManager()
    override var undoManager: UndoManager? { draftUndoManager }
    var onSubmit: (() -> Void)?
    var onFocusChange: ((Bool) -> Void)?
    var wantsFocus = false
    var pendingFocusOnAttachment = false

    override func keyDown(with event: NSEvent) {
        guard isEditable else { return }
        let modifiers = event.modifierFlags.intersection([.shift, .control, .option, .command])
        if event.keyCode == 48, !hasMarkedText(), modifiers.isEmpty || modifiers == .shift {
            if modifiers == .shift { window?.selectPreviousKeyView(nil) }
            else { window?.selectNextKeyView(nil) }
            return
        }
        if (event.keyCode == 36 || event.keyCode == 76), !hasMarkedText(),
           modifiers.isEmpty || modifiers == .command {
            // A return used to confirm an IME candidate always goes through AppKit.
            // Do not turn a held key into multiple submissions.
            if !event.isARepeat { onSubmit?() }
            return
        }
        if (event.keyCode == 36 || event.keyCode == 76), !hasMarkedText(), modifiers == .shift {
            insertNewlineIgnoringFieldEditor(nil)
            return
        }
        super.keyDown(with: event)
    }

    override func becomeFirstResponder() -> Bool {
        let accepted = super.becomeFirstResponder()
        if accepted { reportFocus(true) }
        return accepted
    }

    override func resignFirstResponder() -> Bool {
        let accepted = super.resignFirstResponder()
        if accepted { pendingFocusOnAttachment = false; reportFocus(false) }
        return accepted
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        guard pendingFocusOnAttachment else { return }
        Task { @MainActor [weak self] in
            guard let self, self.pendingFocusOnAttachment, self.wantsFocus,
                  self.isEditable, let window = self.window else { return }
            self.pendingFocusOnAttachment = false
            window.makeFirstResponder(self)
        }
    }

    private func reportFocus(_ value: Bool) {
        // Delegate calls can happen during updateNSView; publish on the next turn and
        // discard a notification if focus moved again in the meantime.
        Task { @MainActor [weak self] in
            guard let self, let window = self.window,
                  (window.firstResponder === self) == value else { return }
            self.onFocusChange?(value)
        }
    }
}
