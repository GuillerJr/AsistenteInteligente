import Foundation

/// Identidad pública de la compilación que produjo el proceso actual.
///
/// No contiene datos del usuario. Su única finalidad es impedir que una
/// certificación nueva reutilice evidencia operativa generada por otro binario.
public enum JarvisBuildIdentity {
    public static let development = "development"

    public static func current(bundle: Bundle = .main) -> String {
        guard
            let rawValue = bundle.object(
                forInfoDictionaryKey: "AegisBuildRevision"
            ) as? String
        else {
            return development
        }
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        return isValid(value) ? value : development
    }

    public static func isValid(_ value: String) -> Bool {
        if value == development {
            return true
        }
        let bytes = value.utf8
        guard bytes.count == 40 else { return false }
        return bytes.allSatisfy { byte in
            (48 ... 57).contains(byte) || (97 ... 102).contains(byte)
        }
    }

    public static func short(_ value: String) -> String {
        String(value.prefix(12))
    }
}
