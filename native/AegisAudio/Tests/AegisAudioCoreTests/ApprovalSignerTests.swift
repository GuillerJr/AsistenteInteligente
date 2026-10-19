import CryptoKit
import Foundation
import Testing
@testable import AegisAudioCore

private struct InMemoryApprovalSigner: ApprovalSigning {
    let key = P256.Signing.PrivateKey()

    func publicKeyX963() throws -> Data {
        key.publicKey.x963Representation
    }

    func sign(_ message: Data) throws -> Data {
        try key.signature(for: message).derRepresentation
    }
}

@Test func approvalMessageMatchesTheDaemonWireFormat() throws {
    // Same vector as tests/test_approval_signing.py::test_message_wire_format_is_stable.
    let message = try ApprovalSignatureMessage.bytes(
        method: "jobs.approve.voice",
        jobID: UUID(uuidString: "01234567-89AB-CDEF-0123-456789ABCDEF")!,
        callDigest: String(repeating: "ab", count: 32),
        approvalNonce: String(repeating: "1f", count: 16),
        issuedAtMilliseconds: 1_800_000_000_000,
        evidenceSHA256: String(repeating: "cd", count: 32)
    )
    #expect(
        String(decoding: message, as: UTF8.self)
            == [
                "jarvis-approval-v1",
                "jobs.approve.voice",
                "01234567-89ab-cdef-0123-456789abcdef",
                String(repeating: "ab", count: 32),
                String(repeating: "1f", count: 16),
                "1800000000000",
                String(repeating: "cd", count: 32),
            ].joined(separator: "\n")
    )
}

@Test func approvalMessageRejectsUnsignableFields() {
    #expect(throws: ApprovalSignerError.invalidFields) {
        try ApprovalSignatureMessage.bytes(
            method: "jobs.cancel",
            jobID: UUID(),
            callDigest: String(repeating: "a", count: 64),
            approvalNonce: String(repeating: "b", count: 32),
            issuedAtMilliseconds: 1
        )
    }
    #expect(throws: ApprovalSignerError.invalidFields) {
        try ApprovalSignatureMessage.bytes(
            method: "jobs.approve",
            jobID: UUID(),
            callDigest: String(repeating: "a", count: 64),
            approvalNonce: "short",
            issuedAtMilliseconds: 1
        )
    }
}

@Test func approvalSignatureVerifiesAgainstTheExportedPublicKey() throws {
    let signer = InMemoryApprovalSigner()
    let message = try ApprovalSignatureMessage.bytes(
        method: "jobs.approve",
        jobID: UUID(),
        callDigest: String(repeating: "a", count: 64),
        approvalNonce: String(repeating: "b", count: 32),
        issuedAtMilliseconds: 42
    )
    let publicKey = try P256.Signing.PublicKey(x963Representation: signer.publicKeyX963())
    let signature = try P256.Signing.ECDSASignature(derRepresentation: signer.sign(message))

    #expect(try signer.publicKeyX963().count == 65)
    #expect(publicKey.isValidSignature(signature, for: message))
    #expect(!publicKey.isValidSignature(signature, for: message + Data([0])))
}
