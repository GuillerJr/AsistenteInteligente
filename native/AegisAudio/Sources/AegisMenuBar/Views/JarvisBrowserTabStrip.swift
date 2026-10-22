import SwiftUI

struct JarvisBrowserTabStrip: View {
    let tabs: JarvisBrowserTabs

    var body: some View {
        HStack(spacing: 4) {
            ScrollViewReader { proxy in
                ScrollView(.horizontal) {
                    HStack(spacing: 4) {
                        ForEach(tabs.pages, id: \.id) { page in tab(page) }
                    }.padding(5)
                }
                .scrollIndicators(.hidden)
                .onChange(of: tabs.selectedID) { _, id in proxy.scrollTo(id) }
            }
            Button { _ = tabs.newTab() } label: { Image(systemName: "plus") }
                .buttonStyle(JarvisIconButtonStyle())
                .help(tabs.canAddTab ? String(localized: "Nueva pestaña (⌘T)", bundle: .module)
                    : String(localized: "Límite de 8 pestañas. Cierra una para abrir otra.", bundle: .module))
                .accessibilityLabel(String(localized: "Nueva pestaña", bundle: .module))
                .keyboardShortcut("t", modifiers: .command)
                .disabled(!tabs.canAddTab)
            Menu {
                Button(String(localized: "Pestaña anterior", bundle: .module)) { tabs.selectAdjacent(-1) }
                    .keyboardShortcut(.tab, modifiers: [.control, .shift])
                    .disabled(tabs.pages.count < 2)
                Button(String(localized: "Pestaña siguiente", bundle: .module)) { tabs.selectAdjacent(1) }
                    .keyboardShortcut(.tab, modifiers: .control)
                    .disabled(tabs.pages.count < 2)
                Divider()
                Button(String(localized: "Cerrar pestaña actual", bundle: .module)) { tabs.close(tabs.selectedID) }
            } label: { Image(systemName: "ellipsis") }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .accessibilityLabel(String(localized: "Opciones de pestañas", bundle: .module))
                .help(String(localized: "Opciones de pestañas", bundle: .module))
        }
        .padding(.trailing, 8)
        .frame(height: 38)
        .background {
            // Menu items are built lazily. Keep their key equivalents available
            // while this panel is visible, even before the menu has been opened.
            VStack {
                Button(String(localized: "Pestaña anterior", bundle: .module)) { tabs.selectAdjacent(-1) }
                    .keyboardShortcut(.tab, modifiers: [.control, .shift])
                Button(String(localized: "Pestaña siguiente", bundle: .module)) { tabs.selectAdjacent(1) }
                    .keyboardShortcut(.tab, modifiers: .control)
            }
            .disabled(tabs.pages.count < 2)
            .hidden().accessibilityHidden(true)
        }
    }

    private func tab(_ page: JarvisBrowserStore) -> some View {
        let selected = page.id == tabs.selectedID
        let name = label(page)
        return HStack(spacing: 0) {
            Button { tabs.select(page.id) } label: {
                HStack(spacing: 5) {
                    Image(systemName: page.loading ? "arrow.clockwise" : "globe")
                        .font(.system(size: 10))
                    Text(name).font(.system(size: 11.5)).lineLimit(1)
                }
                .padding(.leading, 8).padding(.trailing, 4).frame(height: 28)
                .frame(minWidth: 60, maxWidth: 140)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(String(localized: "Pestaña: \(name)", bundle: .module))
            .accessibilityValue(selected ? String(localized: "Seleccionada", bundle: .module) : "")
            .help(name)
            Button { tabs.close(page.id) } label: {
                Image(systemName: "xmark").font(.system(size: 9, weight: .semibold))
                    .frame(width: 24, height: 28).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(String(localized: "Cerrar pestaña: \(name)", bundle: .module))
            .help(String(localized: "Cerrar pestaña", bundle: .module))
        }
        .background(selected ? JarvisTheme.raised : JarvisTheme.fill,
                    in: RoundedRectangle(cornerRadius: 6))
        .id(page.id)
    }

    private func label(_ page: JarvisBrowserStore) -> String {
        let title = page.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !title.isEmpty { return String(title.prefix(80)) }
        if let host = URL(string: page.address)?.host { return String(host.prefix(80)) }
        return String(localized: "Nueva pestaña", bundle: .module)
    }
}
