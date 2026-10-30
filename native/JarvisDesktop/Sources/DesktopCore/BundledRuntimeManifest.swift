import Foundation

public enum BundledRuntimeFailure: Error, LocalizedError, Equatable, Sendable {
    case missing, invalidManifest, unsupported, insecureTree, changed, signature, externalArguments, validationTimeout
    public var errorDescription: String? {
        switch self {
        case .missing: "Faltan componentes del paquete de Jarvis. Reinstala una copia completa; no se buscarán runtimes externos."
        case .invalidManifest: "El inventario del runtime incluido no es válido. No se inició ningún runtime alternativo."
        case .unsupported: "Este paquete no corresponde a una versión o arquitectura admitida de macOS."
        case .insecureTree: "El paquete contiene archivos, permisos o enlaces no admitidos. No se inició el servicio."
        case .changed: "Los archivos del paquete cambiaron o no coinciden con su inventario. No se inició el servicio."
        case .signature: "No se pudo verificar la integridad de la firma de esta app. No se inició el servicio."
        case .externalArguments: "Un paquete autocontenido no acepta rutas de desarrollo externas. Ábrelo sin argumentos."
        case .validationTimeout: "La verificación del paquete superó su plazo. No se inició el servicio ni se repetirá el intento automáticamente."
        }
    }
}

struct BundledRuntimeManifest: Sendable {
    static let maximumManifestBytes = 8 * 1024 * 1024
    static let maximumFiles = 32_768
    static let maximumFileBytes = 512 * 1024 * 1024
    static let maximumTreeBytes = 2 * 1024 * 1024 * 1024
    struct File: Sendable {
        let path: String
        let bytes: Int
        let sha256: String
        let executable: Bool
    }
    let sourceCommit: String
    let entrypoints: [String: String]
    let files: [String: File]

    init(_ data: Data) throws {
        guard !data.isEmpty, data.count <= Self.maximumManifestBytes,
              let value = try? JSONDecoder().decode(RPCValue.self, from: data),
              Set(value.object?.keys.map { $0 } ?? []) == Set(["schema", "source_commit", "target", "minimum_macos", "python_version", "codex_version", "entrypoints", "files", "components"]),
              value["schema"].string == "jarvis.bundled-runtime.v1",
              let commit = value["source_commit"].string, Self.hex(commit, count: 40),
              value["target"].string == "aarch64-apple-darwin",
              value["minimum_macos"].string == "14.0",
              value["python_version"].string == "3.11.18",
              value["codex_version"].string == "0.163.0",
              let rawEntries = value["entrypoints"].object,
              Set(rawEntries.keys) == Set(["python", "source", "dependencies", "codex"])
                || Set(rawEntries.keys) == Set(["python", "source", "dependencies", "codex", "model"]) else {
            throw BundledRuntimeFailure.invalidManifest
        }
        let canonical = JSONEncoder(); canonical.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        guard (try? canonical.encode(value)) == data else { throw BundledRuntimeFailure.invalidManifest }
        let fixed = ["python": "python/bin/python3.11", "source": "core", "dependencies": "site-packages", "codex": "codex/bin/codex", "model": "models/minilm"]
        var entries: [String: String] = [:]
        for (key, raw) in rawEntries {
            guard let path = raw.string, fixed[key] == path else { throw BundledRuntimeFailure.invalidManifest }
            entries[key] = path
        }
        guard let rows = value["files"].array, !rows.isEmpty, rows.count <= Self.maximumFiles else {
            throw BundledRuntimeFailure.invalidManifest
        }
        var inventory: [String: File] = [:]
        var foldedPaths: Set<String> = []
        var total = 0
        for row in rows {
            guard Set(row.object?.keys.map { $0 } ?? []) == Set(["path", "bytes", "sha256", "executable"]),
                  let path = row["path"].string, Self.relativePath(path), path != "runtime-manifest.json",
                  inventory[path] == nil, foldedPaths.insert(path.folding(options: .caseInsensitive, locale: Locale(identifier: "en_US_POSIX"))).inserted,
                  let bytes = row["bytes"].integer, bytes >= 0,
                  bytes <= Self.maximumFileBytes, let digest = row["sha256"].string, Self.hex(digest, count: 64),
                  let executable = row["executable"].bool else { throw BundledRuntimeFailure.invalidManifest }
            total += bytes
            guard total <= Self.maximumTreeBytes else { throw BundledRuntimeFailure.invalidManifest }
            inventory[path] = File(path: path, bytes: bytes, sha256: digest, executable: executable)
        }
        guard rows.compactMap({ $0["path"].string }) == inventory.keys.sorted() else { throw BundledRuntimeFailure.invalidManifest }
        for key in ["python", "codex"] {
            guard let path = entries[key], inventory[path]?.executable == true else { throw BundledRuntimeFailure.invalidManifest }
        }
        guard inventory["core/jarvis_core/__init__.py"] != nil, (inventory["certs/cacert.pem"]?.bytes ?? 0) > 0,
              inventory.keys.contains(where: { $0.hasPrefix("site-packages/") }) else {
            throw BundledRuntimeFailure.invalidManifest
        }
        if entries["model"] != nil {
            for path in ["manifest.json", "tokenizer.json", "onnx/model_qint8_arm64.onnx", "LICENSE.Apache-2.0.txt"] {
                guard inventory["models/minilm/" + path] != nil else { throw BundledRuntimeFailure.invalidManifest }
            }
        }
        guard let components = value["components"].array, components.count >= 3, components.count <= 128 else {
            throw BundledRuntimeFailure.invalidManifest
        }
        var componentIDs: Set<String> = []
        for row in components {
            guard Set(row.object?.keys.map { $0 } ?? []) == Set(["id", "version", "origin", "archive_sha256", "license_paths"]),
                  let id = row["id"].string, id.range(of: "^[a-z0-9][a-z0-9._-]{0,95}\\z", options: .regularExpression) != nil,
                  componentIDs.insert(id).inserted, let version = row["version"].string,
                  !version.isEmpty, version.utf8.count <= 128, !version.contains("\0"),
                  let origin = row["origin"].string, origin.utf8.count <= 2048,
                  let url = URLComponents(string: origin), url.scheme == "https", url.host?.isEmpty == false,
                  url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
                  let archiveHash = row["archive_sha256"].string, Self.hex(archiveHash, count: 64),
                  let licenses = row["license_paths"].array, !licenses.isEmpty, licenses.count <= 256 else {
                throw BundledRuntimeFailure.invalidManifest
            }
            var distinct: Set<String> = []
            for raw in licenses {
                guard let path = raw.string, Self.relativePath(path), (inventory[path]?.bytes ?? 0) > 0,
                      distinct.insert(path).inserted else { throw BundledRuntimeFailure.invalidManifest }
            }
        }
        guard Set(["python", "codex", "jarvis-core"]).isSubset(of: componentIDs) else { throw BundledRuntimeFailure.invalidManifest }
        sourceCommit = commit; entrypoints = entries; files = inventory
    }

    static func relativePath(_ value: String) -> Bool {
        guard !value.isEmpty, value.utf8.count <= 1024, !value.hasPrefix("/"),
              !value.hasSuffix("/"), !value.contains("\0"), !value.contains("\\"),
              value.precomposedStringWithCanonicalMapping == value,
              !value.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }) else { return false }
        return value.split(separator: "/", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
    }
    static func hex(_ value: String, count: Int) -> Bool {
        value.utf8.count == count && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
