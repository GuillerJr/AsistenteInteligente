import AppKit
import SwiftUI

/// A single normal-level window for decisions required by the current task.
struct ApprovalView: View {
    let model: MenuBarModel
    var interactive = true
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        Group {
            if let approval = model.pendingApproval {
                ActionReviewView(model: model, approval: approval, interactive: interactive)
            } else if let selection = model.pendingBrowserSelection {
                BrowserChoiceDialog(model: model, selection: selection, interactive: interactive)
            } else if let input = model.pendingInput {
                TaskInputDialog(model: model, input: input, interactive: interactive)
            } else if let recovery = model.pendingRecovery {
                RecoveryDialog(model: model, recovery: recovery, interactive: interactive)
            } else {
                ContentUnavailableView(String(localized: "Sin decisiones pendientes", bundle: .module), systemImage: "checkmark.shield")
                    .frame(width: 560, height: 400)
            }
        }
        .onChange(of: model.interactionID, initial: true) { _, id in
            if id == nil, interactive {
                RequestedWindowFocus.close("approval") { dismissWindow(id: "approval") }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.willCloseNotification)) { notification in
            guard let window = notification.object as? NSWindow,
                  window.identifier?.rawValue == "approval" else { return }
            guard let id = model.interactionID else { return }
            model.pauseApprovalForCorrection()
            Task { await model.cancelInteraction(expectedID: id) }
        }
    }
}
