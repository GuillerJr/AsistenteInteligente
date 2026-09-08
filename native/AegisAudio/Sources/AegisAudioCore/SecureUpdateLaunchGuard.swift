import Darwin
import Foundation

public enum SecureUpdateLaunchGuardError: Error, Equatable, Sendable {
    case invalidJournal
    case uncommittedUpdate
    case invalidProbe
}

public enum SecureUpdateLaunchGuard {
    private static let maximumJournalBytes = 8_192

    private struct Record: Decodable {
        let schemaVersion: String
        let transactionID: UUID
        let state: String
        let previousRevision: String
        let targetRevision: String
        let installerPID: Int32

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case transactionID = "transaction_id"
            case state
            case previousRevision = "previous_revision"
            case targetRevision = "target_revision"
            case installerPID = "installer_pid"
        }
    }

    /// Prevents an update interrupted between the APFS swap and its health proof
    /// from booting as if it were already trusted. The updater alone receives a
    /// one-use transaction identifier through `--update-probe`.
    public static func authorizeLaunch(
        arguments: [String],
        buildRevision: String,
        stateDirectory: URL,
        fileManager: FileManager = .default,
        processIsAlive: (Int32) -> Bool = { pid in
            guard pid > 1 else { return false }
            return kill(pid, 0) == 0 || errno == EPERM
        }
    ) throws {
        let journal = stateDirectory.appending(
            path: "update-transaction.json",
            directoryHint: .notDirectory
        )
        guard fileManager.fileExists(atPath: journal.path) else { return }
        let values = try journal.resourceValues(
            forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]
        )
        let attributes = try fileManager.attributesOfItem(atPath: journal.path)
        guard
            values.isRegularFile == true,
            values.isSymbolicLink != true,
            let size = values.fileSize,
            (1 ... maximumJournalBytes).contains(size),
            let owner = attributes[.ownerAccountID] as? NSNumber,
            owner.uint32Value == getuid(),
            let permissions = attributes[.posixPermissions] as? NSNumber,
            permissions.intValue & 0o077 == 0
        else {
            throw SecureUpdateLaunchGuardError.invalidJournal
        }
        let record: Record
        do {
            let data = try Data(contentsOf: journal, options: [.mappedIfSafe])
            let raw = try JSONSerialization.jsonObject(with: data)
            let expectedKeys: Set<String> = [
                "schema_version",
                "transaction_id",
                "state",
                "previous_revision",
                "target_revision",
                "installer_pid",
            ]
            guard
                let dictionary = raw as? [String: Any],
                Set(dictionary.keys) == expectedKeys
            else {
                throw SecureUpdateLaunchGuardError.invalidJournal
            }
            record = try JSONDecoder().decode(Record.self, from: data)
        } catch {
            throw SecureUpdateLaunchGuardError.invalidJournal
        }
        guard
            record.schemaVersion == "1.0",
            JarvisBuildIdentity.isValid(record.previousRevision),
            JarvisBuildIdentity.isValid(record.targetRevision),
            record.previousRevision != JarvisBuildIdentity.development,
            record.targetRevision != JarvisBuildIdentity.development,
            ["prepared", "swapped", "committed"].contains(record.state)
        else {
            throw SecureUpdateLaunchGuardError.invalidJournal
        }
        if record.state == "committed" {
            guard buildRevision == record.targetRevision else {
                throw SecureUpdateLaunchGuardError.invalidJournal
            }
            return
        }
        guard record.state == "swapped" else {
            throw SecureUpdateLaunchGuardError.uncommittedUpdate
        }
        guard
            buildRevision == record.targetRevision,
            processIsAlive(record.installerPID),
            probeIdentifier(in: arguments) == record.transactionID
        else {
            throw SecureUpdateLaunchGuardError.invalidProbe
        }
    }

    private static func probeIdentifier(in arguments: [String]) -> UUID? {
        guard
            let index = arguments.firstIndex(of: "--update-probe"),
            arguments.indices.contains(index + 1)
        else {
            return nil
        }
        return UUID(uuidString: arguments[index + 1])
    }
}
