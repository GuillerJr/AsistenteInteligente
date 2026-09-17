import Foundation
import Darwin

public enum JarvisTerminalCommand {
    /// Reuse the launch directory, but never repair or accept an unsafe one.
    public static func prepareDirectory(in workspace: URL, fileManager: FileManager = .default) throws -> URL {
        let directory = workspace.appendingPathComponent("Terminal", isDirectory: true)
        if !fileManager.fileExists(atPath: directory.path) {
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: false,
                                            attributes: [.posixPermissions: 0o700])
        }
        let attributes = try fileManager.attributesOfItem(atPath: directory.path)
        guard attributes[.type] as? FileAttributeType == .typeDirectory,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o700,
              !(try directory.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink ?? true)
        else { throw BundledDaemonSupervisorError.unsafeWorkspace }
        return directory
    }

    /// Quote every argument separately; shell metacharacters in paths stay literal.
    public static func script(executable: String, environment: [String: String]) -> String {
        let assignments = environment.sorted { $0.key < $1.key }
            .map { quote($0.key + "=" + $0.value) }.joined(separator: " ")
        return "#!/bin/zsh\n/bin/rm -- \"$0\"\nexec /usr/bin/env -i \(assignments) \(quote(executable)) --cli engineer\n"
    }

    private static func quote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
