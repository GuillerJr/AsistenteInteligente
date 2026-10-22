import Foundation

/// These IPC handlers acknowledge absence even when the entry was already absent.
/// Require their typed receipt before removing a row from the visible collection.
enum JarvisSettingsDeletionReceipt {
    private struct Revocation: Decodable { let revoked: Bool }
    private struct Deletion: Decodable { let deleted: Bool }
    private struct Memory: Decodable { let memory_id: String; let status: String }
    private enum ReceiptError: Error { case mismatch }

    static func validate(method: String, payload: [String: Any], data: Data) throws {
        switch method {
        case "jobs.grants.revoke", "jobs.app_grants.revoke":
            _ = try JSONDecoder().decode(Revocation.self, from: data)
        case "schedules.delete", "goals.delete":
            _ = try JSONDecoder().decode(Deletion.self, from: data)
        case "memory.delete":
            let receipt = try JSONDecoder().decode(Memory.self, from: data)
            guard receipt.status == "deleted", receipt.memory_id == payload["memory_id"] as? String else {
                throw ReceiptError.mismatch
            }
        default: break
        }
    }
}
