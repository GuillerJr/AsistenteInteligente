import SwiftUI

/// Custom closed surface; the opened menu retains native selection and keyboard behavior.
struct JarvisMenuPicker<Value: Hashable>: View {
    let title: String
    @Binding var selection: Value
    let options: [JarvisSelectionOption<Value>]
    @FocusState private var focused: Bool
    private var selectedTitle: String { options.first(where: { $0.value == selection })?.title ?? "Seleccionar…" }

    var body: some View {
        Menu {
            Picker(title, selection: $selection) {
                ForEach(options) { Text(verbatim: $0.title).tag($0.value) }
            }.pickerStyle(.inline)
        } label: {
            HStack(spacing: 12) {
                Text(verbatim: selectedTitle).lineLimit(1).truncationMode(.middle)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary).accessibilityHidden(true)
            }.font(.system(size: 12)).foregroundStyle(JarvisInterfaceStyle.text)
                .padding(.horizontal, 12).padding(.vertical, 10)
                .frame(minWidth: 140)
                .modifier(JarvisControlSurface(focused: focused))
        }
        .menuStyle(.borderlessButton).menuIndicator(.hidden).buttonStyle(.plain)
        .focused($focused).accessibilityLabel(title).accessibilityValue(selectedTitle)
        .help(selectedTitle).disabled(options.isEmpty)
    }
}
