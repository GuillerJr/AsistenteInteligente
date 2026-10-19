/// Transient presentation only. Expanding the notch never starts an assistant action.
public struct NotchInteractionState: Equatable, Sendable {
    public private(set) var isExpanded = false
    public private(set) var isPinned = false
    public private(set) var isHovered = false

    public init() {}

    public mutating func setHovered(_ hovered: Bool) {
        isHovered = hovered
        if hovered { isExpanded = true }
    }

    public mutating func togglePinned() {
        isPinned.toggle()
        if isPinned { isExpanded = true }
        else { collapseIfUnhovered() }
    }

    public mutating func collapseIfUnhovered() {
        if !isHovered && !isPinned { isExpanded = false }
    }

    public mutating func dismiss() {
        isExpanded = false
        isPinned = false
        isHovered = false
    }
}
