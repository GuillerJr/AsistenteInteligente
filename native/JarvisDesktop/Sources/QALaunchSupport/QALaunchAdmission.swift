// Kept outside product DesktopCore: this contract belongs only to the opt-in QA helper.
import CryptoKit
import Darwin
import Foundation

public enum QALaunchIssue: String, Error, Equatable, LocalizedError {
    case arguments, request, path, ownership, file, bundle, executable, usageDescription
    case changed, receiptExists, receiptWrite, launchServices, timeout, returnedApplication, processIdentity
    public var errorDescription: String? {
        switch self {
        case .receiptExists: "El recibo ya existe; no se repetirá el lanzamiento."
        case .receiptWrite: "No se pudo conservar el recibo; el resultado requiere revisión."
        case .launchServices, .timeout: "Launch Services no confirmó el arranque. No se repetirá automáticamente."
        case .returnedApplication, .processIdentity: "La identidad de la app iniciada no quedó confirmada."
        case .usageDescription: "El bundle de prueba carece de las descripciones de permisos de voz."
        case .executable: "La app de prueba debe conservar Jarvis como ejecutable Mach-O nativo."
        default: "La solicitud no cumple el contrato de lanzamiento sintético de Jarvis."
        }
    }
}

public struct QALaunchRequest: Codable, Equatable, Sendable {
    public let schema: String
    public let appPath: String
    public let expectedBundleID: String
    public let expectedExecutableSHA256: String
    public let arguments: [String]
    public let environment: [String: String]
    enum CodingKeys: String, CodingKey {
        case schema, arguments, environment
        case appPath = "app_path", expectedBundleID = "expected_bundle_id"
        case expectedExecutableSHA256 = "expected_executable_sha256"
    }
    public init(appPath: String, expectedBundleID: String, expectedExecutableSHA256: String,
                arguments: [String], environment: [String: String]) {
        schema = "jarvis.qa-launch-request.v1"; self.appPath = appPath
        self.expectedBundleID = expectedBundleID; self.expectedExecutableSHA256 = expectedExecutableSHA256
        self.arguments = arguments; self.environment = environment
    }
}

public struct QALaunchAdmission: Sendable {
    public let request: QALaunchRequest
    public let requestURL: URL
    public let receiptURL: URL
    public let appURL: URL
    public let executableURL: URL
    public let requestSHA256: String
    public let argumentsSHA256: String
    public let environmentSHA256: String
    let infoSHA256: String

    public static func admit(requestURL: URL, receiptURL: URL) throws -> Self {
        try syntheticPath(requestURL, directory: false)
        try syntheticPath(receiptURL.deletingLastPathComponent(), directory: true)
        _ = try fileURL(from: receiptURL.path, isDirectory: false)
        guard receiptURL.isFileURL,
              receiptURL.lastPathComponent.range(of: #"^[A-Za-z0-9._-]{1,100}\.json$"#, options: .regularExpression) != nil
        else { throw QALaunchIssue.path }
        var info = stat()
        guard lstat(receiptURL.path, &info) != 0, errno == ENOENT else { throw QALaunchIssue.receiptExists }
        let data = try read(requestURL, limit: 65_536)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == ["schema", "app_path", "expected_bundle_id", "expected_executable_sha256", "arguments", "environment"],
              let request = try? JSONDecoder().decode(QALaunchRequest.self, from: data)
        else { throw QALaunchIssue.request }
        let app = try applicationPath(from: request.appPath, realUIDHome: realUIDHome())
        let executable = app.appendingPathComponent("Contents/MacOS/Jarvis")
        let infoData = try validate(request, app: app, executable: executable)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return Self(request: request, requestURL: requestURL, receiptURL: receiptURL, appURL: app,
                    executableURL: executable, requestSHA256: digest(data),
                    argumentsSHA256: digest(try encoder.encode(request.arguments)),
                    environmentSHA256: digest(try encoder.encode(request.environment)), infoSHA256: digest(infoData))
    }

    /// Recheck the exact request, Info.plist and executable immediately before Launch Services.
    public func revalidate() throws {
        try Self.syntheticPath(requestURL, directory: false)
        guard Self.digest(try Self.read(requestURL, limit: 65_536)) == requestSHA256,
              Self.digest(try Self.validate(request, app: appURL, executable: executableURL)) == infoSHA256
        else { throw QALaunchIssue.changed }
    }

    private static func validate(_ request: QALaunchRequest, app: URL, executable: URL) throws -> Data {
        guard request.schema == "jarvis.qa-launch-request.v1",
              request.expectedBundleID.range(of: #"^local\.jarvis\.[A-Za-z0-9.-]{1,120}$"#, options: .regularExpression) != nil,
              request.expectedExecutableSHA256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
              app.path == request.appPath, app.pathExtension == "app"
        else { throw QALaunchIssue.request }
        try applicationFile(app, app: app, directory: true)
        if !app.path.hasPrefix("/private/tmp/") && !request.arguments.isEmpty { throw QALaunchIssue.arguments }
        let plistURL = app.appendingPathComponent("Contents/Info.plist")
        try applicationFile(plistURL, app: app, directory: false)
        try applicationFile(executable, app: app, directory: false)
        let plistData = try read(plistURL, limit: 65_536)
        guard let plist = try? PropertyListSerialization.propertyList(from: plistData, options: [], format: nil) as? [String: Any],
              plist["CFBundleIdentifier"] as? String == request.expectedBundleID,
              plist["CFBundlePackageType"] as? String == "APPL"
        else { throw QALaunchIssue.bundle }
        guard plist["CFBundleExecutable"] as? String == "Jarvis" else { throw QALaunchIssue.executable }
        for key in ["NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription"] {
            guard let text = plist[key] as? String, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  text.utf8.count <= 2048 else { throw QALaunchIssue.usageDescription }
        }
        let binary = try read(executable, limit: 128 * 1024 * 1024)
        // The supported remote artifact is an arm64 MH_EXECUTE, not a script/launcher wrapper.
        guard binary.count >= 32, Array(binary.prefix(4)) == [0xcf, 0xfa, 0xed, 0xfe],
              Array(binary[4..<8]) == [0x0c, 0x00, 0x00, 0x01],
              Array(binary[12..<16]) == [0x02, 0x00, 0x00, 0x00],
              access(executable.path, X_OK) == 0,
              digest(binary) == request.expectedExecutableSHA256
        else { throw QALaunchIssue.executable }
        try validateArguments(request.arguments, plist: plist, app: app)
        guard Set(request.environment.keys) == ["HOME", "TMPDIR", "PATH"],
              request.environment["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        else { throw QALaunchIssue.arguments }
        for key in ["HOME", "TMPDIR"] {
            guard let path = request.environment[key], path.utf8.count <= 4096 else { throw QALaunchIssue.arguments }
            try syntheticPath(fileURL(from: path, isDirectory: true), directory: true)
        }
        return plistData
    }

    private static func validateArguments(_ arguments: [String], plist: [String: Any], app: URL) throws {
        let channel = plist["JarvisDistributionChannel"] as? String
        if arguments.isEmpty {
            guard channel == "self-contained-qualification",
                  (plist["CFBundleIdentifier"] as? String)?.hasPrefix("local.jarvis.qa.") == true,
                  let configPin = plist["JarvisQualificationConfigurationSHA256"] as? String,
                  configPin.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
                  let runtimePin = plist["JarvisRuntimeManifestSHA256"] as? String,
                  runtimePin.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil else { throw QALaunchIssue.arguments }
            let config = app.appendingPathComponent("Contents/Resources/JarvisQualification.json")
            try applicationFile(config, app: app, directory: false)
            let data = try read(config, limit: 16_384)
            guard digest(data) == configPin,
                  let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  Set(raw.keys) == Set(["format", "state"]) || Set(raw.keys) == Set(["format", "state", "provider_state"]),
                  raw["format"] as? String == "jarvis.qualification-profile.v1",
                  (try? JSONSerialization.data(withJSONObject: raw, options: [.sortedKeys, .withoutEscapingSlashes])) == data else { throw QALaunchIssue.arguments }
            for key in ["state", "provider_state"] where raw[key] != nil {
                guard let path = raw[key] as? String else { throw QALaunchIssue.arguments }
                let url = try fileURL(from: path, isDirectory: true)
                guard url.path == path else { throw QALaunchIssue.path }
                try syntheticPath(url, directory: true)
                var current = path
                while current != "/private/tmp" {
                    var info = stat()
                    guard lstat(current, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
                          info.st_mode & 0o077 == 0 else { throw QALaunchIssue.ownership }
                    current = URL(fileURLWithPath: current).deletingLastPathComponent().path
                }
            }
            let manifest = app.appendingPathComponent("Contents/Resources/JarvisRuntime/runtime-manifest.json")
            try applicationFile(manifest, app: app, directory: false)
            guard digest(try read(manifest, limit: 8 * 1024 * 1024)) == runtimePin else { throw QALaunchIssue.changed }
            return
        }
        guard channel == nil || channel == "development",
              plist["JarvisRuntimeManifestSHA256"] == nil,
              !FileManager.default.fileExists(atPath: app.appendingPathComponent("Contents/Resources/JarvisRuntime").path) else {
            throw QALaunchIssue.arguments
        }
        let allowed: Set<String> = ["--python", "--source", "--dependencies", "--state", "--codex", "--provider-state"]
        guard arguments.count == allowed.count * 2, arguments.reduce(0, { $0 + $1.utf8.count }) <= 16_384 else { throw QALaunchIssue.arguments }
        var seen = Set<String>()
        for offset in stride(from: 0, to: arguments.count, by: 2) {
            let flag = arguments[offset], path = arguments[offset + 1]
            guard allowed.contains(flag), seen.insert(flag).inserted, path.hasPrefix("/"), path.utf8.count <= 4096,
                  !path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 })
            else { throw QALaunchIssue.arguments }
            if flag == "--python" || flag == "--codex" {
                var info = stat()
                guard stat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, access(path, X_OK) == 0 else { throw QALaunchIssue.arguments }
            } else { try syntheticPath(fileURL(from: path, isDirectory: true), directory: true) }
        }
    }

    /// Lexical admission intentionally does not use Foundation's private-prefix normalization.
    /// Only explicit /private/tmp paths are accepted; OS lstat checks each component separately.
    public static func fileURL(from path: String, isDirectory: Bool) throws -> URL {
        try anchoredURL(from: path, isDirectory: isDirectory, anchor: "/private/tmp")
    }

    /// Internal lexical policy for this helper only. Production obtains home
    /// from getpwuid(getuid()); environment HOME is never a path authority.
    static func applicationPath(from path: String, realUIDHome home: String) throws -> URL {
        if path.hasPrefix("/private/tmp/") { return try fileURL(from: path, isDirectory: true) }
        let homeParts = home.split(separator: "/", omittingEmptySubsequences: false)
        guard home.hasPrefix("/"), home != "/", !home.hasSuffix("/"),
              !home.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              homeParts.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else { throw QALaunchIssue.path }
        let anchor = home + "/Library/Application Support/JarvisQualification"
        let url = try anchoredURL(from: path, isDirectory: true, anchor: anchor)
        let suffix = String(path.dropFirst(anchor.count + 1)).split(separator: "/", omittingEmptySubsequences: false)
        guard suffix.count == 2, suffix[1] == "Jarvis.app", suffix[0].count == 32,
              suffix[0].allSatisfy({ "0123456789abcdef".contains($0) }) else { throw QALaunchIssue.path }
        return url
    }

    private static func realUIDHome() throws -> String {
        guard let entry = getpwuid(getuid()), let directory = entry.pointee.pw_dir else { throw QALaunchIssue.path }
        return String(cString: directory)
    }

    private static func applicationFile(_ url: URL, app: URL, directory: Bool) throws {
        let home = try realUIDHome()
        _ = try applicationPath(from: app.path, realUIDHome: home)
        let allowed = directory ? [app.path] : [
            app.appendingPathComponent("Contents/Info.plist").path,
            app.appendingPathComponent("Contents/MacOS/Jarvis").path,
            app.appendingPathComponent("Contents/Resources/JarvisQualification.json").path,
            app.appendingPathComponent("Contents/Resources/JarvisRuntime/runtime-manifest.json").path,
        ]
        guard allowed.contains(url.path) else { throw QALaunchIssue.path }
        if app.path.hasPrefix("/private/tmp/") { try syntheticPath(url, directory: directory); return }
        // Check root-owned system ancestors without allowing them to substitute
        // an app component. From the UID's home onward, every inode is its own.
        var cursor = ""
        for part in home.split(separator: "/") {
            cursor += "/" + String(part)
            try component(cursor, allowSystemOwner: cursor != home)
        }
        try anchoredPath(url, directory: directory, anchor: home)
    }

    private static func anchoredURL(from path: String, isDirectory: Bool, anchor: String) throws -> URL {
        let canonical = isDirectory && path.hasSuffix("/") ? String(path.dropLast()) : path
        let components = canonical.split(separator: "/", omittingEmptySubsequences: false)
        guard canonical.hasPrefix(anchor + "/"), canonical.utf8.count <= 4096,
              !canonical.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
              components.dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." })
        else { throw QALaunchIssue.path }
        return URL(fileURLWithPath: canonical, isDirectory: isDirectory)
    }

    static func syntheticPath(_ url: URL, directory: Bool) throws {
        try anchoredPath(url, directory: directory, anchor: "/private/tmp")
    }

    static func ownershipAllowed(uid: uid_t, mode: mode_t, allowSystemOwner: Bool = false) -> Bool {
        (uid == geteuid() || (allowSystemOwner && uid == 0)) && (mode & 0o022) == 0
    }

    private static func component(_ path: String, allowSystemOwner: Bool = false) throws {
        var info = stat()
        guard lstat(path, &info) == 0 else { throw QALaunchIssue.ownership }
        guard (info.st_mode & S_IFMT) != S_IFLNK else { throw QALaunchIssue.path }
        guard ownershipAllowed(uid: info.st_uid, mode: info.st_mode, allowSystemOwner: allowSystemOwner)
        else { throw QALaunchIssue.ownership }
    }

    private static func anchoredPath(_ url: URL, directory: Bool, anchor: String) throws {
        let path = url.path
        guard url.isFileURL else { throw QALaunchIssue.path }
        _ = try anchoredURL(from: path, isDirectory: directory, anchor: anchor)
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        var cursor = anchor
        for part in components.dropFirst(anchor.split(separator: "/").count + 1) {
            cursor += "/" + String(part)
            try component(cursor)
        }
        var info = stat()
        guard lstat(path, &info) == 0,
              (info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG) else { throw QALaunchIssue.path }
    }

    static func read(_ url: URL, limit: Int) throws -> Data {
        let descriptor = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw QALaunchIssue.file }
        defer { close(descriptor) }
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == geteuid(), info.st_nlink == 1, info.st_size >= 0, info.st_size <= limit
        else { throw QALaunchIssue.file }
        var result = Data(); var buffer = [UInt8](repeating: 0, count: 65_536)
        while true {
            let count = Darwin.read(descriptor, &buffer, buffer.count)
            if count == 0 { break }
            guard count > 0, result.count + count <= limit else { throw QALaunchIssue.file }
            result.append(contentsOf: buffer.prefix(count))
        }
        guard result.count == Int(info.st_size) else { throw QALaunchIssue.changed }
        return result
    }
    public static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
}
