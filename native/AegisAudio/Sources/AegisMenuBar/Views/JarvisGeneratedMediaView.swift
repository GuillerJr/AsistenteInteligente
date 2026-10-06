import AegisAudioCore
import AVKit
import ImageIO
import SwiftUI
import UniformTypeIdentifiers

private struct GeneratedMediaDocument: FileDocument {
    static var readableContentTypes: [UTType] { [.jpeg, .mpeg4Movie] }
    let data: Data
    init(data: Data) { self.data = data }
    init(configuration: ReadConfiguration) throws {
        guard let data = configuration.file.regularFileContents else { throw CocoaError(.fileReadCorruptFile) }
        self.data = data
    }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        FileWrapper(regularFileWithContents: data)
    }
}

struct JarvisGeneratedMediaView: View {
    let token: String
    let model: MenuBarModel
    @State private var artifact: GeneratedMediaArtifact?
    @State private var picture: CGImage?
    @State private var player: AVPlayer?
    @State private var error: String?
    @State private var loading = true
    @State private var preparingExport = false
    @State private var exporting = false
    @State private var document: GeneratedMediaDocument?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if loading { ProgressView("Recuperando archivo generado…").controlSize(.small) }
            if let artifact {
                HStack(spacing: 8) {
                    Label(artifact.mediaType == "image/jpeg" ? "Imagen generada" : "Video generado",
                          systemImage: artifact.mediaType == "image/jpeg" ? "photo" : "film")
                        .font(.system(size: 13, weight: .semibold))
                    Spacer()
                    Text(ByteCountFormatter.string(fromByteCount: Int64(artifact.byteCount), countStyle: .file))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                if let picture {
                    Image(decorative: picture, scale: 1).resizable().scaledToFit()
                        .frame(maxHeight: 360).clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityLabel("Imagen generada por NVIDIA Cosmos3 Nano")
                }
                if let player {
                    VideoPlayer(player: player).frame(height: 260)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                HStack {
                    Text("Cosmos3 Nano · Original con marca de procedencia")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                    Spacer()
                    Button("Guardar…", systemImage: "square.and.arrow.down") { Task { await export(artifact) } }
                        .buttonStyle(MenuBarSmallButtonStyle()).disabled(preparingExport)
                }
                if artifact.mediaType == "video/mp4" {
                    Text("Si el reproductor no admite este MP4, guarda el original y ábrelo en un reproductor compatible con VP9.")
                        .font(.system(size: 10)).foregroundStyle(.secondary)
                }
            }
            if let error { Label(error, systemImage: "exclamationmark.circle").font(.system(size: 11)) }
        }
        .padding(14).background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
        .task(id: token) { await load() }
        .onDisappear { player?.pause() }
        .fileExporter(isPresented: $exporting, document: document,
                      contentType: artifact?.mediaType == "video/mp4" ? .mpeg4Movie : .jpeg,
                      defaultFilename: artifact?.fileName) { result in
            if case .failure(let failure) = result, (failure as NSError).code != NSUserCancelledError {
                error = "No pude guardar el archivo."
            }
            document = nil
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        guard let metadata = await model.performChatIPC("artifacts.get", payload: ["token": token]) else {
            error = "Este archivo no está disponible en el Mac."; return
        }
        let token = token
        let result = await Task.detached(priority: .utility) { () -> (GeneratedMediaArtifact, CGImage?)? in
            guard let artifact = try? GeneratedMediaArtifact(metadata, token: token),
                  let data = try? artifact.read() else { return nil }
            var picture: CGImage?
            if artifact.mediaType == "image/jpeg", let source = CGImageSourceCreateWithData(data as CFData, nil) {
                picture = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                    kCGImageSourceCreateThumbnailFromImageAlways: true,
                    kCGImageSourceThumbnailMaxPixelSize: 1024,
                    kCGImageSourceCreateThumbnailWithTransform: true,
                    kCGImageSourceShouldCacheImmediately: true,
                ] as CFDictionary)
            }
            return (artifact, picture)
        }.value
        guard !Task.isCancelled else { return }
        guard let result else { error = "No pude verificar el archivo generado."; return }
        artifact = result.0; picture = result.1
        if result.0.mediaType == "video/mp4" { player = AVPlayer(url: result.0.url) }
    }

    private func export(_ artifact: GeneratedMediaArtifact) async {
        preparingExport = true
        defer { preparingExport = false }
        guard let data = await Task.detached(priority: .userInitiated, operation: { try? artifact.read() }).value else {
            error = "No pude verificar el original antes de guardarlo."; return
        }
        document = GeneratedMediaDocument(data: data); exporting = true
    }
}
