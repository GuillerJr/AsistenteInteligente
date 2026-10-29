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
        guard receiptURL.isFileURL, receiptURL.path == receiptURL.standardizedFileURL.path,
              receiptURL.path.hasPrefix("/private/tmp/"),
              receiptURL.lastPathComponent.range(of: #"^[A-Za-z0-9._-]{1,100}\.json$"#, options: .regularExpression) != nil
        else { throw QALaunchIssue.path }
        var info = stat()
        guard lstat(receiptURL.path, &info) != 0, errno == ENOENT else { throw QALaunchIssue.receiptExists }
        let data = try read(requestURL, limit: 65_536)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == ["schema", "app_path", "expected_bundle_id", "expected_executable_sha256", "arguments", "environment"],
              let request = try? JSONDecoder().decode(QALaunchRequest.self, from: data)
        else { throw QALaunchIssue.request }
        let app = URL(fileURLWithPath: request.appPath, isDirectory: true)
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
        try syntheticPath(app, directory: true)
        let plistURL = app.appendingPathComponent("Contents/Info.plist")
        try syntheticPath(plistURL, directory: false); try syntheticPath(executable, directory: false)
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
        try validateArguments(request.arguments)
        guard Set(request.environment.keys) == ["HOME", "TMPDIR", "PATH"],
              request.environment["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        else { throw QALaunchIssue.arguments }
        for key in ["HOME", "TMPDIR"] {
            guard let path = request.environment[key], path.utf8.count <= 4096 else { throw QALaunchIssue.arguments }
            try syntheticPath(URL(fileURLWithPath: path, isDirectory: true), directory: true)
        }
        return plistData
    }

    private static func validateArguments(_ arguments: [String]) throws {
        let allowed: Set<String> = ["--python", "--source", "--dependencies", "--state", "--codex", "--provider-state"]
        guard arguments.count == allowed.count * 2, arguments.reduce(0, { $0 + $1.utf8.count }) <= 16_384 else { throw QALaunchIssue.arguments }
        var seen = Set<String>()
        for offset in stride(from: 0, to: arguments.count, by: 2) {
            let flag = arguments[offset], path = arguments[offset + 1]
            guard allowed.contains(flag), seen.insert(flag).inserted, path.hasPrefix("/"), path.utf8.count <= 4096,
                  !path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 })
            else { throw QALaunchIssue.arguments }
            let url = URL(fileURLWithPath: path)
            if flag == "--python" || flag == "--codex" {
                var info = stat()
                guard stat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, access(path, X_OK) == 0 else { throw QALaunchIssue.arguments }
            } else { try syntheticPath(url, directory: true) }
        }
    }

    static func syntheticPath(_ url: URL, directory: Bool) throws {
        let path = url.path
        guard url.isFileURL, path.hasPrefix("/private/tmp/"), path == url.standardizedFileURL.path,
              path == url.resolvingSymlinksInPath().path else { throw QALaunchIssue.path }
        let components = url.pathComponents
        var cursor = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
        for component in components.dropFirst(3) {
            cursor.appendPathComponent(component)
            var info = stat()
            guard lstat(cursor.path, &info) == 0, info.st_uid == geteuid(), (info.st_mode & 0o022) == 0,
                  (info.st_mode & S_IFMT) != S_IFLNK else { throw QALaunchIssue.ownership }
        }
        var info = stat()
        guard lstat(path, &info) == 0,
              (info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG) else { throw QALaunchIssue.path }
    }

    static func read(_ url: URL, limit: Int) throws -> Data {
        let descriptor = open(url.path, O_RDONLY | O_NOFOLLOW)
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
