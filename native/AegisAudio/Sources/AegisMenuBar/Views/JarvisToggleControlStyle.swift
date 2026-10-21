import SwiftUI

/// Binary settings use a compact native switch, tinted with the window's accent.
struct JarvisToggleControlStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Toggle(isOn: configuration.$isOn) { configuration.label }
            .toggleStyle(.switch).controlSize(.small).labelsHidden()
    }
}
