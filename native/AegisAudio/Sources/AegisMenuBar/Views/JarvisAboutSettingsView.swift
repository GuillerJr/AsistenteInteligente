import AppKit
import SwiftUI

struct JarvisBuildInfo: Equatable {
    let version: String
    let build: String
    let revision: String

    init(bundle: Bundle = .main) {
        version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0"
        build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "desarrollo"
        revision = bundle.object(forInfoDictionaryKey: "AegisBuildRevision") as? String ?? "desarrollo"
    }

    @MainActor static func showAbout() {
        let info = Self()
        let credits = NSMutableAttributedString(string: String(localized: "Compilación \(info.build) · Revisión \(info.revision.prefix(12))\n\n", bundle: .module))
        credits.append(NSAttributedString(string: String(localized: "Jarvis utiliza Silero VAD, ONNX Runtime y componentes Python de código abierto.\n", bundle: .module)))
        if let licenses = Bundle.main.url(forResource: "ThirdPartyNotices", withExtension: "txt") {
            credits.append(NSAttributedString(string: String(localized: "Ver licencias y avisos de terceros", bundle: .module), attributes: [.link: licenses]))
        }
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: String(localized: "Jarvis", bundle: .module), .applicationVersion: info.version,
            .version: info.build, .credits: credits
        ])
    }
}

struct JarvisAboutSettingsView: View {
    private let info = JarvisBuildInfo()
    var body: some View {
        JarvisSettingsCard(title: String(localized: "Acerca de y actualizaciones", bundle: .module)) {
            LabeledContent(String(localized: "Versión", bundle: .module), value: info.version)
            LabeledContent(String(localized: "Compilación", bundle: .module), value: info.build)
            LabeledContent(String(localized: "Revisión", bundle: .module), value: String(info.revision.prefix(12)))
            Button(String(localized: "Licencias de terceros", bundle: .module)) {
                if let url = Bundle.main.url(forResource: "ThirdPartyNotices", withExtension: "txt") {
                    NSWorkspace.shared.open(url)
                }
            }
            JarvisUpdateSettingsView()
        }
    }
}

struct JarvisUpdateSettingsView: View {
    @State private var updates = JarvisUpdateController.shared
    @State private var confirm = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let message = updates.message { Text(message).font(.callout).textSelection(.enabled) }
            HStack {
                Button(String(localized: "Buscar actualizaciones", bundle: .module)) { Task { await updates.check() } }
                    .disabled(updates.busy)
                if updates.available != nil {
                    Button(String(localized: "Instalar y reiniciar", bundle: .module)) { confirm = true }.disabled(updates.busy)
                }
                if updates.busy { ProgressView().controlSize(.small) }
            }
        }
        .confirmationDialog(String(localized: "¿Instalar la actualización y reiniciar Jarvis?", bundle: .module), isPresented: $confirm, titleVisibility: .visible) {
            Button(String(localized: "Instalar y reiniciar", bundle: .module)) { Task { await updates.installConfirmed() } }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: {
            Text(String(localized: "Jarvis verificará la firma de la actualización y conservará tus datos. Termina las tareas en curso antes de continuar.", bundle: .module))
        }
    }
}
