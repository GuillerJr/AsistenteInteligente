import AppKit
import AVFoundation
import DesktopCore
import Foundation
import ImageIO
import PDFKit
import SwiftUI
import Darwin

/// Only a path explicitly selected in the native picker reaches this reader.
/// Reject private names before opening; no recursive enumeration or temp files.
enum SelectedMediaFile {
    static func read(_ url: URL) throws -> Data {
        guard MediaFilePolicy.selectablePath(url), MediaFilePolicy.selectablePath(url.resolvingSymlinksInPath()) else { throw RPCFailure.configuration }
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw RPCFailure.unavailable }
        defer { Darwin.close(fd) }
        var before = stat()
        guard fstat(fd, &before) == 0, before.st_mode & S_IFMT == S_IFREG,
              before.st_nlink == 1, before.st_uid == getuid(), before.st_size > 0,
              before.st_size <= 16 * 1024 * 1024 else { throw RPCFailure.oversized }
        var result = Data(count: Int(before.st_size))
        let expected = result.count
        let count = result.withUnsafeMutableBytes { buffer -> Int in
            guard let base = buffer.baseAddress else { return -1 }
            var offset = 0
            while offset < expected {
                let amount = Darwin.read(fd, base.advanced(by: offset), expected - offset)
                if amount <= 0 { return -1 }; offset += amount
            }
            return offset
        }
        var after = stat()
        guard count == expected, fstat(fd, &after) == 0, before.st_dev == after.st_dev,
              before.st_ino == after.st_ino, before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec,
              before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec,
              before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec else { throw RPCFailure.invalidResponse }
        return result
    }
}

struct AttachmentComposerView: View {
    @Bindable var store: ChatStore
    @State private var pickerError: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Button("Adjuntar…", systemImage: "paperclip") { choose() }
                    .disabled(!store.canChangeSession || !store.connected || store.attachments.busy)
                if store.attachments.busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("Actualizar adjuntos") { Task { await store.attachments.refresh() } }
                    .disabled(store.attachments.busy || store.selected == nil)
                Button("Autorizar transcripción local") {
                    Task {
                        let result = await MacLocalFileTranscriber.requestSpeechPermission()
                        pickerError = result == .authorized ? "Reconocimiento local autorizado. El micrófono no se abrió." : "No se autorizó el reconocimiento local de archivos."
                    }
                }.help("Permiso de reconocimiento de voz para archivos; no solicita el micrófono")
            }.font(.caption)
            if !store.attachments.attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(store.attachments.attachments.filter { !$0.revoked }) { file in
                            HStack {
                                Toggle(file.name, isOn: Binding(get: { store.attachments.selectedIDs.contains(file.id) }, set: { _ in store.attachments.toggle(file) }))
                                    .toggleStyle(.button).help("\(file.bytes) bytes · \(file.sha256)")
                                Button("Vista previa", systemImage: "eye") { Task { await store.attachments.openPreview(file) } }.labelStyle(.iconOnly)
                                Button("Retirar", systemImage: "xmark.circle") { Task { await store.attachments.revoke(file) } }.labelStyle(.iconOnly)
                            }.padding(6).background(.quaternary, in: .rect(cornerRadius: 8))
                        }
                    }
                }.disabled(store.attachments.busy || store.activeRunID != nil || store.talk.isActive)
                Text("Selecciona hasta ocho adjuntos. Su análisis requiere aprobación; el contenido externo no autoriza herramientas.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            if let issue = pickerError ?? store.attachments.errorMessage { Text(issue).font(.caption).foregroundStyle(.secondary) }
        }
        .task(id: store.selected?.scope) { await store.attachments.setScope(store.selected?.scope) }
        .sheet(item: Binding(get: { store.attachments.preview }, set: { store.attachments.preview = $0 })) { MediaPreviewView(preview: $0) }
    }
    private func choose() {
        guard store.canChangeSession, let scope = store.selected?.scope else { return }
        let panel = NSOpenPanel()
        panel.title = "Adjuntar documentos o medios"; panel.prompt = "Revisar archivo"
        panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.resolvesAliases = false
        panel.allowsMultipleSelection = false
        if let path = store.access.workspacePath { panel.directoryURL = URL(fileURLWithPath: path, isDirectory: true) }
        guard panel.runModal() == .OK, let url = panel.url, store.selected?.scope == scope else { return }
        do {
            let data = try SelectedMediaFile.read(url)
            Task { guard store.selected?.scope == scope else { return }; await store.attachments.importData(data, name: url.lastPathComponent) }
        } catch { pickerError = "No se pudo abrir este archivo regular o no cumple el límite de 16 MiB. No se importó contenido." }
    }
}

@MainActor @Observable private final class MediaPlayback {
    var player: AVPlayer?
    var issue: String?
    private var source: MemoryMediaAsset?
    func prepare(_ preview: MediaPreview) {
        stop()
        do {
            let source = try MemoryMediaAsset(data: preview.data, mimeType: preview.playbackMIME ?? preview.attachment.mime)
            self.source = source; player = AVPlayer(playerItem: AVPlayerItem(asset: source.asset))
        } catch { issue = "El formato no admite reproducción local en este Mac." }
    }
    func stop() { player?.pause(); player?.replaceCurrentItem(with: nil); player = nil; source?.close(); source = nil }
}

struct MediaPreviewView: View {
    let preview: MediaPreview
    @Environment(\.dismiss) private var dismiss
    @State private var playback = MediaPlayback()
    @State private var picture: NSImage?
    @State private var text: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack { Text(preview.attachment.name).font(.headline); Spacer(); Button("Cerrar") { dismiss() } }
            Text("\(preview.attachment.mime) · \(preview.data.count) bytes\nSHA-256: \(preview.attachment.sha256)")
                .font(.caption.monospaced()).textSelection(.enabled)
            Text("Vista local en memoria. El contenido sigue siendo externo y no se ejecuta.").font(.caption).foregroundStyle(.secondary)
            if let picture { Image(nsImage: picture).resizable().scaledToFit().frame(maxWidth: 600, maxHeight: 400) }
            if let text { ScrollView { Text(text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(height: 280) }
            if ["audio", "video"].contains(preview.attachment.kind) {
                if let player = playback.player {
                    MemoryMediaPlayerView(player: player).frame(height: preview.attachment.kind == "video" ? 280 : 64)
                }
                if let issue = playback.issue { Text(issue).foregroundStyle(.secondary) }
            }
            if picture == nil && text == nil && !["audio", "video"].contains(preview.attachment.kind) {
                Text("Para extraer páginas, tablas o texto, selecciona el archivo y solicita su análisis en el chat.")
            }
        }.padding(20).frame(minWidth: 600, minHeight: 220)
        .onAppear { prepare() }.onDisappear { playback.stop(); picture = nil; text = nil }
    }
    private func prepare() {
        if preview.attachment.kind == "image", let source = CGImageSourceCreateWithData(preview.data as CFData, nil),
           let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 1200] as CFDictionary) {
            picture = NSImage(cgImage: image, size: .zero)
        } else if preview.attachment.kind == "pdf", let document = PDFDocument(data: preview.data), !document.isLocked, let page = document.page(at: 0) {
            picture = page.thumbnail(of: NSSize(width: 900, height: 1000), for: .mediaBox)
            text = "Vista de la primera página; el análisis puede recorrer las páginas restantes con citas."
        } else if preview.attachment.mime.hasPrefix("text/") || preview.attachment.mime == "application/json" {
            text = String(data: preview.data.prefix(49152), encoding: .utf8).map { String($0.prefix(16000)) }
        } else if ["audio", "video"].contains(preview.attachment.kind) { playback.prepare(preview) }
    }
}

import AVKit
private struct MemoryMediaPlayerView: NSViewRepresentable {
    let player: AVPlayer
    func makeNSView(context: Context) -> AVPlayerView { let view = AVPlayerView(); view.player = player; view.controlsStyle = .inline; return view }
    func updateNSView(_ view: AVPlayerView, context: Context) { view.player = player }
    static func dismantleNSView(_ view: AVPlayerView, coordinator: ()) { view.player?.pause(); view.player = nil }
}
