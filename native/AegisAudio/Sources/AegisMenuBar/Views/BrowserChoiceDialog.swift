import SwiftUI

struct BrowserChoiceDialog: View {
    let model: MenuBarModel
    let selection: PendingBrowserSelection
    var interactive = true

    var body: some View {
        JarvisDialogShell(title: String(localized: "Elige un navegador", bundle: .module), symbol: "globe",
                          detail: String(localized: "Encontré varias opciones. Elige dónde continuar con tu solicitud.", bundle: .module), height: 440) {
            ForEach(selection.options, id: \.bundleIdentifier) { option in
                Button {
                    Task { await model.selectBrowser(bundleIdentifier: option.bundleIdentifier) }
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "globe").font(.system(size: 20))
                        VStack(alignment: .leading, spacing: 4) {
                            Text(option.name).font(.system(size: 14, weight: .medium))
                            Text(option.running ? String(localized: "Ya está abierto", bundle: .module) : String(localized: "Disponible en este Mac", bundle: .module))
                                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                        }
                        Spacer()
                        Image(systemName: "arrow.up.right")
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(JarvisButtonStyle()).disabled(!interactive)
            }
            Text(String(localized: "También puedes decir el nombre del navegador.", bundle: .module))
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
        } footer: {
            HStack {
                Button(String(localized: "Cancelar", bundle: .module), role: .cancel) { Task { await model.cancelInteraction() } }
                    .keyboardShortcut(.cancelAction)
                    .disabled(!interactive)
                Spacer()
            }
        }
    }
}
