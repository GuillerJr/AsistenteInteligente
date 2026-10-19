import CryptoKit
import Foundation
import Security

/// Signs approvals so the daemon can tell Jarvis.app apart from any other process that
/// can read the shared IPC key. The private key never leaves this app's Keychain ACL.
public protocol ApprovalSigning {
    /// Uncompressed X9.63 P-256 public key (65 bytes, leading 0x04).
    func publicKeyX963() throws -> Data
    /// DER-encoded ECDSA P-256/SHA-256 signature over `message`.
    func sign(_ message: Data) throws -> Data
}

public enum ApprovalSignerError: Error, Equatable {
    case keyUnavailable
    case signingFailed
    case invalidFields
}

public enum ApprovalSignatureMessage {
    public static let context = "jarvis-approval-v1"

    /// Mirrors `aegis_core.approval_signing.approval_signature_message`.
    public static func bytes(
        method: String,
        jobID: UUID,
        callDigest: String,
        approvalNonce: String,
        issuedAtMilliseconds: Int64,
        evidenceSHA256: String? = nil
    ) throws -> Data {
        let hex64 = #"^[0-9a-f]{64}$"#
        guard
            ["jobs.approve", "jobs.approve.voice"].contains(method),
            callDigest.range(of: hex64, options: .regularExpression) != nil,
            approvalNonce.range(of: #"^[0-9a-f]{32}$"#, options: .regularExpression) != nil,
            issuedAtMilliseconds >= 0,
            evidenceSHA256.map({ $0.range(of: hex64, options: .regularExpression) != nil })
                ?? true
        else {
            throw ApprovalSignerError.invalidFields
        }
        let lines = [
            context,
            method,
            jobID.uuidString.lowercased(),
            callDigest,
            approvalNonce,
            String(issuedAtMilliseconds),
            evidenceSHA256 ?? "-",
        ]
        return Data(lines.joined(separator: "\n").utf8)
    }

    public static func sha256Hex(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

/// P-256 key stored in the login Keychain. Items created through SecKey default to an
/// ACL that trusts only the creating application, so other processes are prompted.
public final class KeychainApprovalSigner: ApprovalSigning {
    private let applicationTag: Data
    private let lock = NSLock()
    private var cachedKey: SecKey?

    public init(applicationTag: String = "ai.aegis.approval-signer.v1") {
        self.applicationTag = Data(applicationTag.utf8)
    }

    public func publicKeyX963() throws -> Data {
        guard
            let publicKey = SecKeyCopyPublicKey(try privateKey()),
            let raw = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?,
            raw.count == 65,
            raw.first == 0x04
        else {
            throw ApprovalSignerError.keyUnavailable
        }
        return raw
    }

    public func sign(_ message: Data) throws -> Data {
        var error: Unmanaged<CFError>?
        guard
            let signature = SecKeyCreateSignature(
                try privateKey(),
                .ecdsaSignatureMessageX962SHA256,
                message as CFData,
                &error
            ) as Data?
        else {
            error?.release()
            throw ApprovalSignerError.signingFailed
        }
        return signature
    }

    private func privateKey() throws -> SecKey {
        lock.lock()
        defer { lock.unlock() }
        if let cachedKey {
            return cachedKey
        }
        let key = try loadKey() ?? createKey()
        cachedKey = key
        return key
    }

    private func loadKey() throws -> SecKey? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassKey,
            kSecAttrApplicationTag as String: applicationTag,
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeyClass as String: kSecAttrKeyClassPrivate,
            kSecReturnRef as String: true,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        switch status {
        case errSecSuccess:
            guard let item, CFGetTypeID(item) == SecKeyGetTypeID() else {
                throw ApprovalSignerError.keyUnavailable
            }
            return (item as! SecKey)
        case errSecItemNotFound:
            return nil
        default:
            throw ApprovalSignerError.keyUnavailable
        }
    }

    private func createKey() throws -> SecKey {
        let attributes: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecPrivateKeyAttrs as String: [
                kSecAttrIsPermanent as String: true,
                kSecAttrApplicationTag as String: applicationTag,
                kSecAttrLabel as String: "Jarvis approval signer",
            ],
        ]
        var error: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attributes as CFDictionary, &error) else {
            error?.release()
            throw ApprovalSignerError.keyUnavailable
        }
        return key
    }
}
