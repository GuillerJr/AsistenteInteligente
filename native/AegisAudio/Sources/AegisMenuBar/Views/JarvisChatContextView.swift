import SwiftUI

struct JarvisChatContextView: View {
    let model: MenuBarModel
    var interactive = true
    let close: () -> Void
    let attach: () -> Void
    let showActions: () -> Void
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(String(localized: "Contexto del chat", bundle: .module)).font(.system(size: 13, weight: .semibold))
                Spacer()
                MenuBarIconButton(symbol: "xmark", title: String(localized: "Cerrar contexto", bundle: .module), action: close)
            }.padding(.horizontal, 14).frame(height: 52)
            JarvisSeparator()
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 10) {
                        sectionTitle(String(localized: "Proyecto", bundle: .module))
                        if chat.canChooseWorkspaceProject {
                            Picker(String(localized: "Proyecto del próximo chat", bundle: .module),
                                   selection: Binding(get: { chat.workspaceProject?.id ?? "" },
                                                      set: { chat.chooseWorkspaceProject($0.isEmpty ? nil : $0) })) {
                                Text(String(localized: "Sin proyecto", bundle: .module)).tag("")
                                ForEach(chat.projects) { project in
                                    Text(verbatim: project.name).tag(project.id)
                                }
                            }.pickerStyle(.menu).labelsHidden()
                            .accessibilityLabel(String(localized: "Proyecto del próximo chat", bundle: .module))
                            .accessibilityIdentifier("chat-context-project-picker")
                        } else if let project = chat.workspaceProject {
                            Label(project.name, systemImage: project.symbol).font(.system(size: 13, weight: .medium))
                        } else {
                            Text(chat.temporary ? String(localized: "Los chats temporales no usan proyectos.", bundle: .module)
                                 : String(localized: "Esta conversación no pertenece a un proyecto.", bundle: .module))
                                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                        }
                        if let project = chat.workspaceProject {
                            if !project.instructions.isEmpty {
                                Text(verbatim: project.instructions).font(.system(size: 12))
                                    .foregroundStyle(JarvisTheme.inkSecondary).lineLimit(5)
                            }
                            ForEach(Array(project.files.prefix(8))) { file in
                                Label { Text(verbatim: file.name).lineLimit(1).truncationMode(.middle) }
                                    icon: { Image(systemName: "doc.text") }
                                    .font(.system(size: 12)).help(file.name)
                            }
                            Button(String(localized: "Abrir proyecto", bundle: .module)) { chat.pane = .project(project.id) }
                                .buttonStyle(MenuBarSmallButtonStyle()).disabled(!interactive)
                        }
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        sectionTitle(String(localized: "Próximo mensaje", bundle: .module))
                        Label(chat.planMode ? String(localized: "Modo plan", bundle: .module) : String(localized: "Conversar", bundle: .module),
                              systemImage: chat.planMode ? "list.bullet.clipboard" : "bubble.left")
                            .font(.system(size: 12))
                        Text(chat.planMode ? String(localized: "Jarvis investigará y propondrá un plan sin actuar.", bundle: .module)
                             : String(localized: "Las acciones que lo necesiten te pedirán aprobación.", bundle: .module))
                            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                        if chat.attachments.isEmpty {
                            Text(String(localized: "Sin archivos adjuntos", bundle: .module)).font(.system(size: 12))
                                .foregroundStyle(JarvisTheme.inkSecondary)
                        } else {
                            ForEach(chat.attachments) { attachment in
                                Label { Text(verbatim: attachment.name).lineLimit(2) } icon: { Image(systemName: "paperclip") }
                                    .font(.system(size: 12))
                            }
                        }
                        Button(String(localized: "Adjuntar archivos", bundle: .module), action: attach)
                            .buttonStyle(MenuBarSmallButtonStyle()).disabled(!interactive || !model.canManageChatHistory)
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        sectionTitle(String(localized: "Privacidad y actividad", bundle: .module))
                        Label(chat.temporary ? String(localized: "Chat temporal", bundle: .module)
                              : String(localized: "Historial cifrado en este Mac", bundle: .module),
                              systemImage: chat.temporary ? "eye.slash" : "lock.shield")
                            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                        Button(String(localized: "Historial de acciones", bundle: .module), action: showActions)
                            .buttonStyle(MenuBarSmallButtonStyle()).disabled(!interactive)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(16)
            }
        }
        .foregroundStyle(JarvisTheme.ink).background(JarvisTheme.sidebar)
        .frame(width: 300)
        .accessibilityIdentifier("chat-context-panel")
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title).font(.system(size: 11, weight: .semibold)).foregroundStyle(JarvisTheme.inkSecondary)
    }
}
