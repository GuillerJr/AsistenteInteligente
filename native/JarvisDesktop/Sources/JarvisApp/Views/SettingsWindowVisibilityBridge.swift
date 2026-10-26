import AppKit
import DesktopCore
import SwiftUI

/// AppKit crosses only the retained Settings-window lifecycle boundary.
/// SwiftUI remains responsible for visibility state and selected tab.
@MainActor struct SettingsWindowVisibilityBridge: NSViewRepresentable {
    let delivery: WindowVisibilityDelivery
    let onVisibility: @MainActor (Bool) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(delivery: delivery, onVisibility: onVisibility) }
    func makeNSView(context: Context) -> ProbeView {
        let view = ProbeView()
        view.didMove = { [weak coordinator = context.coordinator] window in coordinator?.attach(window) }
        return view
    }
    func updateNSView(_ view: ProbeView, context: Context) {
        context.coordinator.onVisibility = onVisibility
        context.coordinator.attach(view.window)
    }
    static func dismantleNSView(_ view: ProbeView, coordinator: Coordinator) {
        view.didMove = nil
        coordinator.detach()
    }

    @MainActor final class Coordinator {
        var onVisibility: @MainActor (Bool) -> Void
        private let delivery: WindowVisibilityDelivery
        private let owner = UUID()
        private var active = true
        private lazy var monitor = WindowVisibilityMonitor { [weak self] value in self?.deliver(value) }
        init(delivery: WindowVisibilityDelivery, onVisibility: @escaping @MainActor (Bool) -> Void) {
            self.delivery = delivery; self.onVisibility = onVisibility
            delivery.activate(owner)
        }
        func attach(_ window: NSWindow?) {
            if !active { delivery.activate(owner); active = true }
            guard let window else { monitor.detach(); return }
            window.identifier = NSUserInterfaceItemIdentifier(OwnerDecisionWindowPolicy.settingsIdentifier)
            monitor.attach(to: window) { [weak window] in window?.isVisible ?? false }
        }
        func detach() {
            monitor.detach()
            delivery.release(owner, deliver: onVisibility)
            active = false
        }
        private func deliver(_ value: Bool) {
            delivery.publish(value, owner: owner, deliver: onVisibility)
        }
    }

    final class ProbeView: NSView {
        var didMove: (@MainActor (NSWindow?) -> Void)?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            didMove?(window)
        }
    }
}
