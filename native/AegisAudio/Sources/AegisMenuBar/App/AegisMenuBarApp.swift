import SwiftUI

@main
struct AegisMenuBarApp: App {
    @State private var model = MenuBarModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(model: model)
        } label: {
            Label("Aegis", systemImage: model.menuBarSymbol)
                .labelStyle(.iconOnly)
                .task {
                    if ProcessInfo.processInfo.arguments.contains("--request-permissions") {
                        await model.requestUndeterminedPermissions()
                    }
                    await model.monitor()
                }
        }
        .menuBarExtraStyle(.menu)
    }
}
