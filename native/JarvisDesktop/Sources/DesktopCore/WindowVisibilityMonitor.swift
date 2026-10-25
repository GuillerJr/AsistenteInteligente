import AppKit
import Foundation

/// Identity-scoped window events only. The bridge supplies the actual NSWindow;
/// tests can use identity objects and a visibility probe without WindowServer.
@MainActor
public final class WindowVisibilityMonitor {
    private weak var target: AnyObject?
    private let center: NotificationCenter
    private let onVisibility: @MainActor (Bool) -> Void
    private var visibleProbe: (@MainActor () -> Bool)?
    private var observers: [NSObjectProtocol] = []
    private var generation = 0
    private var lastValue: Bool?

    public init(center: NotificationCenter = .default,
                onVisibility: @escaping @MainActor (Bool) -> Void) {
        self.center = center; self.onVisibility = onVisibility
    }

    public func attach(to object: AnyObject, isVisible: @escaping @MainActor () -> Bool) {
        if let target, target === object {
            // A SwiftUI update while willClose is being delivered must not
            // resurrect visibility from NSWindow.isVisible's old value.
            visibleProbe = isVisible
            return
        }
        detach()
        target = object; visibleProbe = isVisible
        let ticket = generation
        for (name, closing) in [(NSWindow.willCloseNotification, true),
                                (NSWindow.didBecomeKeyNotification, false)] {
            // object:nil avoids NotificationCenter retaining the window. Only
            // the exact weak target identity is accepted when delivery occurs.
            let observer = center.addObserver(forName: name, object: nil, queue: nil) { [weak self] notification in
                guard let object = notification.object as AnyObject? else { return }
                let identity = ObjectIdentifier(object)
                if Thread.isMainThread {
                    MainActor.assumeIsolated { self?.receive(identity, closing: closing, ticket: ticket) }
                } else {
                    Task { @MainActor [weak self] in self?.receive(identity, closing: closing, ticket: ticket) }
                }
            }
            observers.append(observer)
        }
        publish(isVisible())
    }

    public func detach() {
        generation += 1
        for observer in observers { center.removeObserver(observer) }
        observers.removeAll(); target = nil; visibleProbe = nil
        publish(false)
    }

    private func receive(_ identity: ObjectIdentifier, closing: Bool, ticket: Int) {
        guard ticket == generation, let target, ObjectIdentifier(target) == identity else { return }
        publish(closing ? false : (visibleProbe?() ?? false))
    }

    private func publish(_ value: Bool) {
        guard lastValue != value else { return }
        lastValue = value; onVisibility(value)
    }

    deinit {
        for observer in observers { center.removeObserver(observer) }
    }
}

/// Delivers outside a SwiftUI update, retaining the final withdrawal until it
/// runs. A replacement coordinator invalidates the previous owner's callbacks.
@MainActor
public final class WindowVisibilityDelivery {
    private var owner: UUID?
    private var revision = 0
    public init() {}
    public func activate(_ value: UUID) {
        guard owner != value else { return }
        revision += 1; owner = value
    }
    public func publish(_ value: Bool, owner: UUID,
                        deliver: @escaping @MainActor (Bool) -> Void) {
        guard self.owner == owner else { return }
        enqueue(value, deliver: deliver)
    }
    public func release(_ owner: UUID, deliver: @escaping @MainActor (Bool) -> Void) {
        guard self.owner == owner else { return }
        self.owner = nil
        enqueue(false, deliver: deliver)
    }
    private func enqueue(_ value: Bool, deliver: @escaping @MainActor (Bool) -> Void) {
        revision += 1
        let ticket = revision
        Task { @MainActor [self] in
            guard revision == ticket else { return }
            deliver(value)
        }
    }
}
