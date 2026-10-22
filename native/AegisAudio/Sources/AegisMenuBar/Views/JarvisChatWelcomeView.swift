import SwiftUI

struct JarvisChatWelcomeView: View {
    let chat: JarvisChatStore
    let choose: (JarvisChatStarter) -> Void

    var body: some View {
        VStack(spacing: 22) {
            VStack(spacing: 10) {
                Image(systemName: chat.temporary ? "eye.slash" : "sparkle")
                    .font(.system(size: 23, weight: .medium)).foregroundStyle(JarvisTheme.clay)
                    .accessibilityHidden(true)
                Text(String(localized: "¿Qué quieres hacer?", bundle: .module))
                    .font(.system(size: 27, weight: .semibold)).multilineTextAlignment(.center)
                Text(chat.workspaceProject.map {
                    String(localized: "Trabajando en \($0.name)", bundle: .module)
                } ?? (chat.temporary
                    ? String(localized: "Un espacio temporal, sin historial ni memoria.", bundle: .module)
                    : String(localized: "Pregunta, prepara un plan o empieza una tarea.", bundle: .module)))
                    .font(.system(size: 13)).foregroundStyle(JarvisTheme.inkSecondary)
                    .multilineTextAlignment(.center)
            }
            if (chat.composerDraft ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                    ForEach(JarvisChatStarter.allCases) { starter in
                        Button { choose(starter) } label: {
                            Label(starter.title, systemImage: starter.symbol)
                                .font(.system(size: 12)).frame(maxWidth: .infinity, minHeight: 34, alignment: .leading)
                                .padding(.horizontal, 10)
                                .background(JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain).disabled(chat.isEditing)
                        .help(String(localized: "Prepara un borrador que puedes editar antes de enviar", bundle: .module))
                        .accessibilityIdentifier("chat-starter-" + starter.id)
                    }
                }.frame(maxWidth: 480)
            }
        }
        .foregroundStyle(JarvisTheme.ink)
        .frame(maxWidth: 700).frame(maxWidth: .infinity)
        .padding(.horizontal, 24).padding(.vertical, 16)
    }
}
