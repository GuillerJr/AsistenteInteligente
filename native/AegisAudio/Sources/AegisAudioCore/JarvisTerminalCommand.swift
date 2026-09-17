import Foundation

public enum JarvisTerminalCommand {
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
