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

struct JarvisCommandPalette: View {
    let commands: [JarvisPaletteCommand]
    let dismiss: () -> Void
    @State private var query = ""
    @State private var selection = 0
    @FocusState private var focused: Bool

    private var visible: [JarvisPaletteCommand] { commands.filter { $0.matches(query) } }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundStyle(JarvisTheme.inkSecondary)
                TextField(String(localized: "Busca una acción…", bundle: .module), text: $query)
                    .textFieldStyle(.plain).font(.system(size: 15))
                    .focused($focused)
                    .onSubmit(runSelected)
                    .onKeyPress(.downArrow) { move(1) }
                    .onKeyPress(.upArrow) { move(-1) }
                    .onChange(of: query) { _, _ in selection = 0 }
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
                        ForEach(Array(visible.enumerated()), id: \.element.id) { index, command in
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
                                .background(index == selection ? JarvisTheme.fillSelected : .clear,
                                            in: RoundedRectangle(cornerRadius: 7))
                                .opacity(command.enabled ? 1 : 0.4)
                            }
                            .buttonStyle(.plain).disabled(!command.enabled).id(command.id)
                        }
                    }.padding(6)
                }
                .onChange(of: selection) { _, index in
                    if visible.indices.contains(index) { proxy.scrollTo(visible[index].id) }
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

    private func move(_ step: Int) -> KeyPress.Result {
        guard !visible.isEmpty else { return .handled }
        selection = (selection + step + visible.count) % visible.count
        return .handled
    }

    private func runSelected() {
        guard visible.indices.contains(selection) else { return }
        run(visible[selection])
    }

    private func run(_ command: JarvisPaletteCommand) {
        guard command.enabled else { return }
        dismiss()
        // After the sheet closes, so an action that opens a panel or window keeps focus.
        DispatchQueue.main.async { command.run() }
    }
}
