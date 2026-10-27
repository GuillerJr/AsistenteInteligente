import Observation
import SwiftUI

enum MemoryPanelPage: Hashable { case notes, intelligence }

/// Window-local presentation, independent of service data and permissions.
@MainActor @Observable
final class InspectorPresentation {
    var isPresented: Bool
    var selection: String
    var memoryPage: MemoryPanelPage
    private var storedWidth: CGFloat = 420
    var width: CGFloat {
        get { storedWidth }
        set { storedWidth = newValue.isFinite ? min(560, max(320, newValue)) : 420 }
    }
    init(isPresented: Bool = false, selection: String = "tools", memoryPage: MemoryPanelPage = .notes) {
        self.isPresented = isPresented; self.selection = selection; self.memoryPage = memoryPage
    }
}

/// A SwiftUI divider, not another NSSplitView/inspector hosting child.
struct InspectorResizeHandle: View {
    let presentation: InspectorPresentation
    @State private var dragWidth: CGFloat?
    var body: some View {
        Rectangle().fill(.clear)
            .frame(width: 8)
            .overlay { Rectangle().fill(Color.secondary.opacity(0.25)).frame(width: 1) }
            .contentShape(Rectangle())
            .help("Arrastra para cambiar el ancho del panel")
            .gesture(DragGesture(minimumDistance: 0, coordinateSpace: .named("jarvis.chat.layout"))
                .onChanged { value in
                    let start = dragWidth ?? presentation.width
                    dragWidth = start
                    presentation.width = start - value.translation.width
                }
                .onEnded { _ in dragWidth = nil })
    }
}

/// The column owns its size proposal. Child forms/lists cannot change the
/// hosting split child's minimum/ideal size as asynchronous data arrives.
struct InspectorViewport<Content: View>: View {
    private let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        GeometryReader { geometry in
            content
                .frame(width: geometry.size.width, height: geometry.size.height, alignment: .topLeading)
        }
        .frame(minWidth: 0, idealWidth: 420, maxWidth: .infinity,
               minHeight: 0, maxHeight: .infinity, alignment: .topLeading)
    }
}
