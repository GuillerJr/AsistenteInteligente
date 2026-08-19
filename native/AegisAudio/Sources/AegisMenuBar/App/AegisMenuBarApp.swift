import SwiftUI

@main
struct AegisMenuBarApp: App {
    @State private var model = MenuBarModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(model: model)
        } label: {
            Label("Aegis", systemImage: model.daemonState.symbol)
                .labelStyle(.iconOnly)
                .task {
                    await model.monitor()
                }
        }
        .menuBarExtraStyle(.menu)
    }
}
