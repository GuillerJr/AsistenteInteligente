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

@Test func perAppApprovalsAreSignedUnderTheirOwnMethods() throws {
    for method in ["jobs.approve.app.conversation", "jobs.approve.app.always"] {
        let message = try ApprovalSignatureMessage.bytes(
            method: method, jobID: UUID(), callDigest: String(repeating: "a", count: 64),
            approvalNonce: String(repeating: "b", count: 32), issuedAtMilliseconds: 1)
        #expect(String(decoding: message, as: UTF8.self).split(separator: "\n")[1] == Substring(method))
    }
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

@Test func voiceEvidenceDigestMatchesTheDaemon() {
    // Same vectors as tests/test_approval_signing.py::test_voice_evidence_digest_is_stable.
    let pcm = Data(repeating: 0, count: 16).enumerated().reduce(into: Data()) { data, item in
        data.append(item.offset.isMultiple(of: 2) ? 1 : 0)
    }
    #expect(
        ApprovalSignatureMessage.voiceEvidenceSHA256(pcmS16LE: pcm, transcript: nil)
            == "278abfb53cf32e306305c34f6ad5469b82453fa58f29fd339bf1615e7ffb4331"
    )
    #expect(
        ApprovalSignatureMessage.voiceEvidenceSHA256(pcmS16LE: pcm, transcript: "sí")
            == "fcdc8615ad6f1079e8f7c9edc688529e0fb55a17fbcc51eb8a7dec2d60ad0468"
    )
}

@Test func alwaysAllowIsSignedUnderItsOwnMethod() throws {
    let fields = (
        jobID: UUID(), digest: String(repeating: "ab", count: 32),
        nonce: String(repeating: "1f", count: 16)
    )
    let standing = try ApprovalSignatureMessage.bytes(
        method: "jobs.approve.standing", jobID: fields.jobID, callDigest: fields.digest,
        approvalNonce: fields.nonce, issuedAtMilliseconds: 1
    )
    let once = try ApprovalSignatureMessage.bytes(
        method: "jobs.approve", jobID: fields.jobID, callDigest: fields.digest,
        approvalNonce: fields.nonce, issuedAtMilliseconds: 1
    )
    // A one-time approval signature can never be replayed as "always allow".
    #expect(standing != once)
}

@Test func pendingConfirmationReadsTheReadOnlyFlag() throws {
    let base: [String: Any] = [
        "call_digest": String(repeating: "a", count: 64),
        "tool_name": "mcp_google_workspace__search_gmail_messages",
        "summary": "Buscar correos",
        "expires_at": "2026-10-20T12:00:00Z",
    ]
    var readOnly = base
    readOnly["standing_grant_available"] = true
    #expect(IPCPendingConfirmation(object: readOnly)?.standingGrantAvailable == true)
    #expect(IPCPendingConfirmation(object: base)?.standingGrantAvailable == false)
}
