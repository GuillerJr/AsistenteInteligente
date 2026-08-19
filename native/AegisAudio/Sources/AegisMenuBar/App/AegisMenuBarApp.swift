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
                    let arguments = ProcessInfo.processInfo.arguments
                    if arguments.contains("--request-permissions") {
                        await model.requestUndeterminedPermissions()
                    }
                    if arguments.contains("--voice-turn") {
                        await model.startVoiceTurn()
                    }
                    await model.monitor()
                }
        }
        .menuBarExtraStyle(.menu)
    }
}
