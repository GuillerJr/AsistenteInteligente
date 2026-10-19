import SwiftUI

/// Binary settings share the surface used by navigation and appearance selectors.
struct JarvisToggleControlStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        JarvisSegmentedPicker(title: "Estado", selection: configuration.$isOn, options: [
            .init(value: true, title: "Activado"),
            .init(value: false, title: "Desactivado"),
        ], optionWidth: 76)
        .accessibilityRepresentation {
            Toggle(isOn: configuration.$isOn) { configuration.label }.toggleStyle(.switch)
        }
    }
}
