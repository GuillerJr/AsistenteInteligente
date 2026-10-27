import Observation
import SwiftUI

enum MemoryPanelPage: Hashable { case notes, intelligence }

/// Window-local presentation, independent of service data and permissions.
@MainActor @Observable
final class InspectorPresentation {
    var isPresented: Bool
    var selection: String
    var memoryPage: MemoryPanelPage
    init(isPresented: Bool = false, selection: String = "tools", memoryPage: MemoryPanelPage = .notes) {
        self.isPresented = isPresented; self.selection = selection; self.memoryPage = memoryPage
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
