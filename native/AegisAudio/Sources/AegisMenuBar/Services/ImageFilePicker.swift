import AppKit
import UniformTypeIdentifiers

@MainActor
enum ImageFilePicker {
    static func chooseImage() -> URL? {
        let panel = NSOpenPanel()
        panel.title = "Selecciona una imagen para Jarvis"
        panel.prompt = "Analizar"
        panel.allowedContentTypes = [.jpeg, .png, .webP]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        return panel.runModal() == .OK ? panel.url : nil
    }
}
