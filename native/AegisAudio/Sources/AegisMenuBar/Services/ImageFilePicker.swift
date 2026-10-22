import AppKit
import UniformTypeIdentifiers

@MainActor
enum ImageFilePicker {
    static func chooseImage() -> URL? {
        let panel = NSOpenPanel()
        panel.title = String(localized: "Selecciona una imagen para Jarvis", bundle: .module)
        panel.prompt = String(localized: "Analizar", bundle: .module)
        panel.allowedContentTypes = [.jpeg, .png, .webP]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        return panel.runModal() == .OK ? panel.url : nil
    }
}
