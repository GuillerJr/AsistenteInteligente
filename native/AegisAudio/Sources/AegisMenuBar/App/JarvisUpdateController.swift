import Foundation
import Observation

@MainActor @Observable
final class JarvisUpdateController {
    static let shared = JarvisUpdateController()
    struct Release: Decodable, Equatable { let version: String; let build: Int; let revision: String }
    private struct Response: Decodable { let status: String; let release: Release? }
    private(set) var available: Release?
    private(set) var busy = false
    private(set) var message: String?

    func check() async {
        guard !busy else { return }
        busy = true
        available = nil
        message = String(localized: "Buscando una actualización firmada…", bundle: .module)
        defer { busy = false }
        do {
            let response = try await Self.run(command: "secure-update-check", timeout: 45)
            if response.status == "available", let release = response.release {
                available = release
                message = String(localized: "Disponible: Jarvis \(release.version) (compilación \(release.build)).", bundle: .module)
            } else if response.status == "current" {
                message = String(localized: "Ya tienes la versión publicada más reciente.", bundle: .module)
            } else if response.status == "unpublished" {
                message = String(localized: "Todavía no hay una actualización publicada en el canal estable.", bundle: .module)
            } else { throw UpdateError.failed }
        } catch {
            message = String(localized: "No se pudo verificar el canal de actualizaciones. Comprueba la conexión e inténtalo de nuevo.", bundle: .module)
        }
    }

    func installConfirmed() async {
        guard !busy, let selected = available else { return }
        busy = true
        message = String(localized: "Descargando y verificando la actualización…", bundle: .module)
        var keepBusy = false
        defer { if !keepBusy { busy = false } }
        do {
            let prepared = try await Self.run(command: "secure-update-prepare", revision: selected.revision, timeout: 660)
            guard prepared.status == "prepared", prepared.release == selected else { throw UpdateError.failed }
            message = String(localized: "Instalando la actualización; Jarvis se reiniciará…", bundle: .module)
            let installed = try await Self.run(command: "secure-update-apply", revision: selected.revision, timeout: 900)
            if installed.status == "installing" {
                keepBusy = true
                available = nil
                message = String(localized: "La actualización sigue verificándose. Jarvis continuará y se reiniciará cuando termine; no cierres el proceso de actualización.", bundle: .module)
                return
            }
            guard installed.status == "installed" else { throw UpdateError.failed }
        } catch {
            message = String(localized: "La actualización no se instaló. Se necesita una versión publicada con firma Developer ID y notarización válidas, y Jarvis debe estar sin tareas en curso. La versión actual se conserva.", bundle: .module)
        }
    }

    private enum UpdateError: Error { case failed }

    nonisolated static func waitForExit(_ process: Process, mayTerminate: Bool, timeout: TimeInterval) async throws -> Bool {
        let deadline = ContinuousClock.now.advanced(by: .seconds(timeout))
        while process.isRunning, ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(50))
        }
        guard process.isRunning else { return true }
        guard mayTerminate else { return false }
        process.terminate()
        let grace = ContinuousClock.now.advanced(by: .seconds(2))
        while process.isRunning, ContinuousClock.now < grace {
            try await Task.sleep(for: .milliseconds(50))
        }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
        throw UpdateError.failed
    }

    private static func run(command: String, revision: String? = nil, timeout: TimeInterval) async throws -> Response {
        let bundleURL = Bundle.main.bundleURL
        guard bundleURL.pathExtension == "app" else { throw UpdateError.failed }
        let executable = bundleURL.appendingPathComponent("Contents/Resources/Daemon/jarvis-daemon")
        guard executable.resolvingSymlinksInPath() == executable.standardizedFileURL,
              FileManager.default.isExecutableFile(atPath: executable.path) else { throw UpdateError.failed }
        let data = try await Task.detached(priority: .utility) {
            let process = Process()
            let output = Pipe()
            process.executableURL = executable
            process.arguments = [command] + (revision.map { ["--confirm-install", $0] } ?? [])
            process.environment = [
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                "LANG": "en_US.UTF-8", "AEGIS_BUNDLED_RUNTIME": "1"
            ]
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            try process.run()
            if !(try await waitForExit(process, mayTerminate: command != "secure-update-apply", timeout: timeout)) {
                // Keep ownership of the pipes until the installer finishes. Never
                // send a termination signal while it may be swapping application files.
                Task.detached {
                    while process.isRunning { try? await Task.sleep(for: .seconds(1)) }
                    _ = output.fileHandleForReading.readDataToEndOfFile()
                }
                return Data("{\"status\":\"installing\"}".utf8)
            }
            let bytes = output.fileHandleForReading.readDataToEndOfFile()
            guard process.terminationStatus == 0, bytes.count <= 65_536 else { throw UpdateError.failed }
            return bytes
        }.value
        return try JSONDecoder().decode(Response.self, from: data)
    }
}
