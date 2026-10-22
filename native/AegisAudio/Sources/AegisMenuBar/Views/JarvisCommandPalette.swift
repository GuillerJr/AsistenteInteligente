import AegisAudioCore
import SwiftUI

/// ⌘K in the chat window: find any action by name (Claude's and Codex's command
/// palette). It only lists actions the window already offers, each with its own guard.
struct JarvisPaletteCommand: Identifiable {
    let id: String
    let title: String
    let symbol: String
    var shortcut: String?
    var keywords: [String] = []
    var enabled = true
    let run: () -> Void

    func matches(_ query: String) -> Bool {
        let folded = Self.fold(query)
        guard !folded.isEmpty else { return true }
        let haystack = Self.fold(([title] + keywords).joined(separator: " "))
        return folded.split(separator: " ").allSatisfy { haystack.contains($0) }
    }

    static func fold(_ text: String) -> String {
        text.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "es"))
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

enum JarvisPaletteSelection {
    static func resolved(_ selection: String?, in commands: [JarvisPaletteCommand]) -> String? {
        commands.first(where: { $0.id == selection && $0.enabled })?.id
            ?? commands.first(where: \.enabled)?.id
    }

    static func moved(_ selection: String?, in commands: [JarvisPaletteCommand],
                      forward: Bool) -> String? {
        guard let selectedID = resolved(selection, in: commands),
              let current = commands.firstIndex(where: { $0.id == selectedID }) else { return nil }
        let direction = forward ? 1 : -1
        // At most one traversal; a palette with no enabled commands cannot loop.
        for distance in 1...commands.count {
            let index = (current + direction * distance + commands.count) % commands.count
            if commands[index].enabled { return commands[index].id }
        }
        return nil
    }
}

struct JarvisCommandPalette: View {
    let commands: [JarvisPaletteCommand]
    let dismiss: () -> Void
    @State private var query = ""
    @State private var selection: String?
    @FocusState private var focused: Bool

    private var visible: [JarvisPaletteCommand] { commands.filter { $0.matches(query) } }
    private var selectedID: String? { JarvisPaletteSelection.resolved(selection, in: visible) }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundStyle(JarvisTheme.inkSecondary)
                TextField(String(localized: "Busca una acción…", bundle: .module), text: $query)
                    .textFieldStyle(.plain).font(.system(size: 15))
                    .focused($focused)
                    .onSubmit(runSelected)
                    .onKeyPress(.downArrow) { move(forward: true) }
                    .onKeyPress(.upArrow) { move(forward: false) }
                    .onChange(of: query) { _, _ in selection = nil }
            }
            .padding(14)
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(spacing: 2) {
                        if visible.isEmpty {
                            Text(String(localized: "Ninguna acción coincide.", bundle: .module)).font(.system(size: 12))
                                .foregroundStyle(JarvisTheme.inkSecondary).padding(16)
                        }
                        ForEach(visible) { command in
                            Button { run(command) } label: {
                                HStack(spacing: 10) {
                                    Image(systemName: command.symbol).frame(width: 18)
                                    Text(command.title).font(.system(size: 13))
                                    Spacer()
                                    if let shortcut = command.shortcut {
                                        Text(shortcut).font(.system(size: 11, design: .rounded))
                                            .foregroundStyle(JarvisTheme.inkSecondary)
                                    }
                                }
                                .padding(.horizontal, 12).padding(.vertical, 7)
                                .contentShape(Rectangle())
                                .background(command.id == selectedID ? JarvisTheme.fillSelected : .clear,
                                            in: RoundedRectangle(cornerRadius: 7))
                                .opacity(command.enabled ? 1 : 0.4)
                            }
                            .buttonStyle(.plain).disabled(!command.enabled).id(command.id)
                        }
                    }.padding(6)
                }
                .onChange(of: selectedID) { _, id in
                    if let id { proxy.scrollTo(id) }
                }
            }
            .frame(height: 300)
        }
        .frame(width: 460)
        .background(JarvisTheme.canvas)
        .foregroundStyle(JarvisTheme.ink)
        .onAppear { focused = true }
        .onExitCommand(perform: dismiss)
    }

    private func move(forward: Bool) -> KeyPress.Result {
        selection = JarvisPaletteSelection.moved(selection, in: visible, forward: forward)
        return .handled
    }

    private func runSelected() {
        guard let command = visible.first(where: { $0.id == selectedID && $0.enabled }) else { return }
        run(command)
    }

    private func run(_ command: JarvisPaletteCommand) {
        guard command.enabled else { return }
        dismiss()
        // After the sheet closes, so an action that opens a panel or window keeps focus.
        DispatchQueue.main.async { command.run() }
    }
}
