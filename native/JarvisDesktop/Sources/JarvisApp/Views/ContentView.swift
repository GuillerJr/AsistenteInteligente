import SwiftUI

struct ContentView: View {
    @Bindable var store: ChatStore

    var body: some View {
        NavigationSplitView {
            SidebarView(store: store)
                .navigationSplitViewColumnWidth(min: 200, ideal: 245, max: 320)
        } detail: {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    Circle().fill(store.connected ? .green : .secondary).frame(width: 7, height: 7)
                    Text(store.statusText).font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    if !store.connected {
                        Button("Conectar ChatGPT") { Task { await store.login() } }.disabled(!store.ready)
                    }
                }.padding(.horizontal, 24).padding(.vertical, 12)
                Divider()
                MessageListView(messages: store.messages, temporary: store.selected?.temporary ?? false,
                                streamingText: store.streamingText)
                if let error = store.errorMessage {
                    HStack(alignment: .top) {
                        Image(systemName: "exclamationmark.triangle")
                        Text(error).textSelection(.enabled)
                        Spacer()
                        Button("Ocultar") { store.errorMessage = nil }
                    }
                    .font(.callout).foregroundStyle(.red)
                    .padding(12).background(.quaternary, in: .rect(cornerRadius: 10))
                    .padding(.horizontal, 24)
                }
                ComposerView(store: store).padding(20)
            }
            .navigationTitle(store.selected?.title ?? "Jarvis")
            .toolbar {
                ToolbarItem {
                    if store.busy || store.starting { ProgressView().controlSize(.small) }
                }
            }
        }
        .frame(minWidth: 720, minHeight: 520)
    }
}
