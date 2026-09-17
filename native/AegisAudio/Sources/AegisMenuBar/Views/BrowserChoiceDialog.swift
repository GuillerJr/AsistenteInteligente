import SwiftUI

struct BrowserChoiceDialog: View {
    let model: MenuBarModel
    let selection: PendingBrowserSelection
    var interactive = true

    var body: some View {
        JarvisDialogShell(title: "Elige un navegador", symbol: "globe",
                          detail: "Encontré varias opciones. Elige dónde continuar con tu solicitud.", height: 440) {
            ForEach(selection.options, id: \.bundleIdentifier) { option in
                Button {
                    Task { await model.selectBrowser(bundleIdentifier: option.bundleIdentifier) }
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "globe").font(.system(size: 20))
                        VStack(alignment: .leading, spacing: 4) {
                            Text(option.name).font(.system(size: 14, weight: .medium))
                            Text(option.running ? "Ya está abierto" : "Disponible en este Mac")
                                .font(.system(size: 11)).foregroundStyle(Color.primary.opacity(0.55))
                        }
                        Spacer()
                        Image(systemName: "arrow.up.right")
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.buttonStyle(JarvisButtonStyle()).disabled(!interactive)
            }
            Text("También puedes decir el nombre del navegador.")
                .font(.system(size: 12)).foregroundStyle(Color.primary.opacity(0.6))
        } footer: {
            HStack {
                Button("Cancelar", role: .cancel) { Task { await model.cancelInteraction() } }
                    .keyboardShortcut(.cancelAction)
                    .disabled(!interactive)
                Spacer()
            }
        }
    }
}
