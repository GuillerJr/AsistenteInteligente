import Darwin
import DesktopCore
import Foundation

/// CI interoperability check only. It never starts an app, interpreter,
/// account, profile, Login Item, or an alternate runtime.
@main enum RuntimeCheckMain {
    static func main() {
        do {
            let args = Array(CommandLine.arguments.dropFirst())
            guard args.count == 6, args[0] == "--root", args[2] == "--manifest-sha256", args[4] == "--source-commit",
                  args[1].hasPrefix("/"), args[1].utf8.count <= 4096,
                  !args[1].unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 }),
                  args[1].split(separator: "/", omittingEmptySubsequences: false).dropFirst().allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }),
                  args[3].range(of: "^[a-f0-9]{64}\\z", options: .regularExpression) != nil,
                  args[5].range(of: "^[a-f0-9]{40}\\z", options: .regularExpression) != nil else { throw BundledRuntimeFailure.invalidManifest }
            let runtime = try BundledRuntime.resolve(root: URL(fileURLWithPath: args[1], isDirectory: true), manifestSHA256: args[3])
            guard runtime.sourceCommit == args[5] else { throw BundledRuntimeFailure.changed }
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
            let receipt = ["schema": "jarvis.bundled-runtime-check.v1", "status": "passed",
                           "source_commit": runtime.sourceCommit, "manifest_sha256": runtime.manifestSHA256]
            var output = try encoder.encode(receipt); output.append(10)
            FileHandle.standardOutput.write(output)
        } catch {
            FileHandle.standardError.write(Data("No se pudo verificar el runtime incluido de Jarvis.\n".utf8))
            exit(EXIT_FAILURE)
        }
    }
}
