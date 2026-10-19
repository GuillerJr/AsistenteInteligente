import Foundation
import PDFKit
import UniformTypeIdentifiers

public struct ChatDocumentAttachment: Codable, Equatable, Sendable {
    public let name: String
    public let content: String
}

public struct ChatAttachment: Identifiable, Equatable, Sendable {
    public enum Content: Equatable, Sendable {
        case document(ChatDocumentAttachment)
        case image(LocalImageAttachment)
    }
    public let id = UUID()
    public let name: String
    public let content: Content

    public var isImage: Bool { if case .image = content { return true }; return false }
    public var document: ChatDocumentAttachment? {
        if case .document(let document) = content { return document }; return nil
    }
    public var image: LocalImageAttachment? {
        if case .image(let image) = content { return image }; return nil
    }
}

public enum ChatAttachmentError: LocalizedError {
    case unsupported, unsafeFile, tooLarge, noText, tooMany, duplicate
    public var errorDescription: String? {
        switch self {
        case .unsupported: "Ese formato no está disponible. Usa texto, código, CSV, JSON, PDF o imágenes."
        case .unsafeFile: "No pude abrir ese archivo. Selecciona un archivo local disponible."
        case .tooLarge: "El archivo supera el límite: 10 MB de origen y 32 KB de texto por documento."
        case .noText: "El documento no contiene texto legible. Para un PDF escaneado, adjunta una imagen de la página."
        case .tooMany: "Puedes adjuntar hasta cuatro archivos, incluida una imagen, y 64 KB de texto por mensaje."
        case .duplicate: "Ya hay un archivo con ese nombre en este mensaje."
        }
    }
}

public enum ChatAttachmentLoader {
    public static var allowedTypes: [UTType] {
        [.text, .pdf, .image, .json, .commaSeparatedText]
            + textExtensions.sorted().compactMap { UTType(filenameExtension: $0) }
    }
    private static let textExtensions: Set<String> = [
        "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "xml", "html", "htm",
        "log", "swift", "py", "js", "jsx", "ts", "tsx", "css", "scss", "sql", "sh", "bash",
        "zsh", "c", "h", "cpp", "hpp", "java", "go", "rs", "rb", "php", "toml", "ini", "conf"
    ]

    public static func load(_ url: URL) throws -> ChatAttachment {
        guard url.isFileURL else { throw ChatAttachmentError.unsafeFile }
        let accessed = url.startAccessingSecurityScopedResource()
        defer { if accessed { url.stopAccessingSecurityScopedResource() } }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey, .contentTypeKey])
        guard values.isRegularFile == true else { throw ChatAttachmentError.unsafeFile }
        guard let size = values.fileSize, size > 0, size <= 10_000_000 else {
            throw ChatAttachmentError.tooLarge
        }
        let name = url.lastPathComponent
        guard name.count <= 255, !name.contains("\n"), !name.contains("\r"), !name.contains("\\")
        else { throw ChatAttachmentError.unsafeFile }
        if values.contentType?.conforms(to: .image) == true {
            return ChatAttachment(name: name, content: .image(try LocalImageEncoder.encodeFile(at: url)))
        }
        let text: String
        if url.pathExtension.lowercased() == "pdf" {
            guard let pdf = PDFDocument(url: url), !pdf.isLocked, pdf.pageCount <= 100 else {
                throw ChatAttachmentError.unsafeFile
            }
            var pages: [String] = []
            var bytes = 0
            for index in 0..<pdf.pageCount {
                let page = pdf.page(at: index)?.string ?? ""
                bytes += page.utf8.count + 1
                guard bytes <= 32_768 else { throw ChatAttachmentError.tooLarge }
                pages.append(page)
            }
            text = pages.joined(separator: "\n")
        } else {
            guard textExtensions.contains(url.pathExtension.lowercased())
                    || values.contentType?.conforms(to: .plainText) == true
                    || values.contentType?.conforms(to: .sourceCode) == true else {
                throw ChatAttachmentError.unsupported
            }
            guard size <= 131_072 else { throw ChatAttachmentError.tooLarge }
            let data = try Data(contentsOf: url)
            guard let decoded = String(data: data, encoding: .utf8)
                    ?? String(data: data, encoding: .utf16), !decoded.contains("\0") else {
                throw ChatAttachmentError.noText
            }
            text = decoded
        }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw ChatAttachmentError.noText
        }
        guard text.utf8.count <= 32_768 else { throw ChatAttachmentError.tooLarge }
        return ChatAttachment(name: name, content: .document(.init(name: name, content: text)))
    }

    public static func validate(_ attachments: [ChatAttachment]) throws {
        guard attachments.count <= 4, attachments.filter(\.isImage).count <= 1,
              attachments.compactMap(\.document).reduce(0, { $0 + $1.content.utf8.count }) <= 65_536
        else { throw ChatAttachmentError.tooMany }
        guard Set(attachments.map(\.name)).count == attachments.count else {
            throw ChatAttachmentError.duplicate
        }
    }
}
