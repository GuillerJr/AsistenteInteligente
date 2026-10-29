import Foundation
import XCTest
@testable import DesktopCore

private actor NativeMediaRPCFixture {
    let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
    var data: Data
    var playback: Data?
    var manifest: RPCValue = .null
    var methods: [String] = []
    var uploadID = ""
    var received = 0
    var failCommitReply = false
    var corruptPreview = false
    var holdChunk = false
    var held: CheckedContinuation<Void, Never>?
    var cancelled = false
    init(_ data: Data) { self.data = data }
    func configure(failCommit: Bool = false, corrupt: Bool = false, hold: Bool = false) {
        failCommitReply = failCommit; corruptPreview = corrupt; holdChunk = hold
    }
    func existing(kind: String = "document", mime: String = "text/plain", playback: Data? = nil) -> RPCValue {
        self.playback = playback
        manifest = row(id: String(repeating: "b", count: 32), name: "fixture.txt", kind: kind, mime: mime)
        return manifest
    }
    private func row(id: String, name: String, kind: String = "document", mime: String = "text/plain", revoked: Bool = false) -> RPCValue {
        .object(["format": .string("jarvis-attachment/1"), "attachment_id": .string(id),
            "session_id": .string(scope.sessionID), "workspace_id": .string(String(repeating: "c", count: 64)),
            "temporary": .bool(true), "name": .string(name), "mime": .string(mime), "kind": .string(kind),
            "byte_length": .number(Double(data.count)), "sha256": .string(MediaFilePolicy.digest(data)),
            "source": .string("composer"), "revoked": .bool(revoked)])
    }
    func call(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        methods.append(method)
        switch method {
        case "media.list":
            return .object(["attachments": .array(manifest == .null ? [] : [manifest]),
                "workspace_files_included_in_profile_backup": .bool(false), "temporary_storage": .string("memory_only")])
        case "media.upload.begin":
            uploadID = params["upload_id"]!.string!; received = 0
            manifest = row(id: uploadID, name: params["name"]!.string!)
            return .object(["upload_id": .string(uploadID), "status": .string("receiving"), "received": .number(0)])
        case "media.upload.chunk":
            if holdChunk { await withCheckedContinuation { held = $0 } }
            guard !cancelled, params["offset"]?.integer == received,
                  let part = params["data_base64"]?.string.flatMap({ Data(base64Encoded: $0) }) else { throw RPCFailure.invalidResponse }
            received += part.count
            return .object(["status": .string("receiving"), "received": .number(Double(received))])
        case "media.upload.commit":
            if failCommitReply { throw RPCFailure.disconnected }
            return .object(["status": .string("complete"), "attachment": manifest])
        case "media.upload.status": return .object(["status": .string(cancelled ? "cancelled" : "complete"), "attachment": manifest])
        case "media.upload.cancel": cancelled = true; return .object(["status": .string("cancelled")])
        case "media.read", "media.playback.read":
            let payload = method == "media.playback.read" ? playback ?? data : data
            let offset = params["offset"]!.integer!, end = min(offset + 131072, payload.count)
            var result: [String: RPCValue] = ["attachment": manifest, "offset": .number(Double(offset)),
                "next_offset": .number(Double(end)), "complete": .bool(end == payload.count),
                "data_base64": .string(payload.subdata(in: offset..<end).base64EncodedString())]
            if corruptPreview { result["next_offset"] = .number(Double(end + 1)) }
            if method == "media.playback.read" {
                result["payload_sha256"] = .string(MediaFilePolicy.digest(payload))
                result["payload_mime"] = .string(manifest["mime"] == .string("audio/ogg") ? "audio/wav" : manifest["mime"].string!)
                result["byte_length"] = .number(Double(payload.count))
            }
            return .object(result)
        case "media.revoke":
            var row = manifest.object!; row["revoked"] = .bool(true); manifest = .object(row)
            return .object(["attachment": manifest])
        default: throw RPCFailure.invalidResponse
        }
    }
    func snapshot() -> ([String], Bool, Bool) { (methods, held != nil, cancelled) }
    func replaceManifest(_ value: RPCValue) { manifest = value }
    func release() { held?.resume(); held = nil }
}

final class NativeMediaAttachmentsTests: XCTestCase {
    func testDerivedOggPreviewMetadataNeverMixesSourceIdentityWithWaveSize() async throws {
        let original = Data("OggS SYNTHETIC SOURCE METADATA".utf8), wave = NativeMediaFixtures.wav()
        let fixture = NativeMediaRPCFixture(original)
        let attachment = try MediaAttachment(await fixture.existing(kind: "audio", mime: "audio/ogg"))
        let preview = MediaPreview(attachment: attachment, data: wave, playbackMIME: "audio/wav", playbackSHA: MediaFilePolicy.digest(wave))
        let metadata = try MediaPreviewMetadata(preview)
        XCTAssertTrue(metadata.isDerived); XCTAssertEqual(metadata.sourceMIME, "audio/ogg")
        XCTAssertEqual(metadata.sourceBytes, original.count); XCTAssertEqual(metadata.sourceSHA256, MediaFilePolicy.digest(original))
        XCTAssertEqual(metadata.playbackMIME, "audio/wav"); XCTAssertEqual(metadata.playbackBytes, wave.count)
        XCTAssertEqual(metadata.playbackSHA256, MediaFilePolicy.digest(wave))
        XCTAssertNotEqual(metadata.sourceBytes, metadata.playbackBytes); XCTAssertNotEqual(metadata.sourceSHA256, metadata.playbackSHA256)
        XCTAssertThrowsError(try MediaPreviewMetadata(MediaPreview(attachment: attachment, data: wave, playbackMIME: "audio/wav", playbackSHA: attachment.sha256)))
        XCTAssertThrowsError(try MediaPreviewMetadata(MediaPreview(attachment: attachment, data: wave, playbackMIME: "audio/wav")))
    }
    @MainActor private func store(_ fixture: NativeMediaRPCFixture, allow: Bool = true) -> MediaAttachmentsStore {
        MediaAttachmentsStore(request: { try await fixture.call($0, $1) }, isActive: { true },
            confirmReview: { _, _, _, current in allow && current() })
    }
    @MainActor func testImportIsChunkedAndLostCommitReplyIsResolvedWithoutResubmission() async throws {
        let data = Data(repeating: 65, count: 280000), fixture = NativeMediaRPCFixture(data)
        await fixture.configure(failCommit: true)
        let host = store(fixture); await host.setScope(await fixture.scope)
        await host.importData(data, name: "fixture.txt")
        XCTAssertEqual(host.attachments.count, 1); XCTAssertEqual(host.selectedIDs.count, 1); XCTAssertNil(host.errorMessage)
        let methods = await fixture.snapshot().0
        XCTAssertEqual(methods.filter { $0 == "media.upload.chunk" }.count, 3)
        XCTAssertEqual(methods.filter { $0 == "media.upload.commit" }.count, 1)
        XCTAssertEqual(methods.filter { $0 == "media.upload.status" }.count, 1)
        XCTAssertFalse(methods.contains("media.upload.cancel"))
    }
    @MainActor func testMissingReviewOrRefusalNeverBeginsUpload() async {
        let fixture = NativeMediaRPCFixture(Data("fixture".utf8))
        let host = MediaAttachmentsStore(request: { try await fixture.call($0, $1) }, isActive: { true })
        await host.setScope(await fixture.scope); await host.importData(Data("fixture".utf8), name: "fixture.txt")
        let initial = await fixture.snapshot().0
        XCTAssertFalse(initial.contains("media.upload.begin"))
        let denied = store(fixture, allow: false)
        await denied.setScope(await fixture.scope); await denied.importData(Data("fixture".utf8), name: "fixture.txt")
        let after = await fixture.snapshot().0
        XCTAssertFalse(after.contains("media.upload.begin"))
    }
    @MainActor func testPreviewChecksEveryChunkAndRevokeClearsSelectionAndRAMPreview() async throws {
        let data = Data(repeating: 66, count: 150000), fixture = NativeMediaRPCFixture(data)
        let attachment = try MediaAttachment(await fixture.existing())
        let host = store(fixture); await host.setScope(await fixture.scope); host.toggle(attachment)
        await host.openPreview(attachment); XCTAssertEqual(host.preview?.data, data)
        await host.revoke(attachment)
        XCTAssertEqual(host.attachments.first?.revoked, true); XCTAssertTrue(host.selectedIDs.isEmpty); XCTAssertNil(host.preview)
        await host.openPreview(host.attachments[0]); XCTAssertNil(host.preview)
        let methods = await fixture.snapshot().0
        XCTAssertEqual(methods.filter { $0 == "media.read" }.count, 2)
        XCTAssertEqual(methods.filter { $0 == "media.revoke" }.count, 1)
    }
    @MainActor func testOggPlaybackPinsDerivedWaveWhilePreservingOriginalManifestIdentity() async throws {
        let original = Data("SYNTHETIC OGG DESCRIPTOR".utf8), wave = NativeMediaFixtures.wav()
        let fixture = NativeMediaRPCFixture(original)
        let attachment = try MediaAttachment(await fixture.existing(kind: "audio", mime: "audio/ogg", playback: wave))
        let host = store(fixture); await host.setScope(await fixture.scope); await host.openPreview(attachment)
        XCTAssertEqual(host.preview?.attachment.sha256, MediaFilePolicy.digest(original))
        XCTAssertEqual(host.preview?.data, wave); XCTAssertEqual(host.preview?.playbackMIME, "audio/wav")
        XCTAssertEqual(host.preview?.playbackSHA, MediaFilePolicy.digest(wave))
        let methods = await fixture.snapshot().0
        XCTAssertFalse(methods.contains("media.read"))
    }
    @MainActor func testRefreshRetiresPreviewAndSelectionRevokedByAnotherWindow() async throws {
        let fixture = NativeMediaRPCFixture(Data("READ ONLY FIXTURE".utf8))
        let attachment = try MediaAttachment(await fixture.existing())
        let host = store(fixture); await host.setScope(await fixture.scope)
        host.toggle(attachment); await host.openPreview(attachment)
        XCTAssertNotNil(host.preview); XCTAssertEqual(host.selectedIDs, [attachment.id])
        _ = try await fixture.call("media.revoke", [:])
        await host.refresh()
        XCTAssertNil(host.preview); XCTAssertTrue(host.selectedIDs.isEmpty)
        XCTAssertEqual(host.attachments.first?.revoked, true)
    }
    @MainActor func testRefreshCannotKeepRAMPreviewWhenManifestDisappearsOrIdentityChanges() async throws {
        for field in ["missing", "sha256", "workspace_id"] {
            let fixture = NativeMediaRPCFixture(Data("SOURCE FIXTURE".utf8))
            let original = await fixture.existing(), attachment = try MediaAttachment(original)
            let host = store(fixture); await host.setScope(await fixture.scope)
            host.toggle(attachment); await host.openPreview(attachment)
            var changed = original.object!
            changed[field] = .string(String(repeating: "d", count: 64))
            await fixture.replaceManifest(field == "missing" ? .null : .object(changed))
            await host.refresh()
            XCTAssertNil(host.preview, field); XCTAssertTrue(host.selectedIDs.isEmpty, field)
        }
    }
    @MainActor func testSameSessionContextRevisionRetiresPreviewAndSelectionButSameRevisionPreservesThem() async throws {
        let fixture = NativeMediaRPCFixture(Data("CONTEXT FIXTURE".utf8))
        let attachment = try MediaAttachment(await fixture.existing()), scope = await fixture.scope
        let host = store(fixture); await host.setContext(scope: scope, revision: "workspace-a:agent-1")
        host.toggle(attachment); await host.openPreview(attachment)
        await host.setContext(scope: scope, revision: "workspace-a:agent-1")
        XCTAssertNotNil(host.preview); XCTAssertEqual(host.selectedIDs, [attachment.id])
        await host.setContext(scope: scope, revision: "workspace-b:agent-2")
        XCTAssertEqual(host.scope, scope); XCTAssertNil(host.preview); XCTAssertTrue(host.selectedIDs.isEmpty)
    }
    @MainActor func testLateOldContextCannotRestoreScopeAfterNewerContextJoinsUploadCleanup() async throws {
        let data = Data("HELD CONTEXT".utf8), fixture = NativeMediaRPCFixture(data), host = store(fixture)
        let original = await fixture.scope
        let latest = SessionScope(sessionID: String(repeating: "e", count: 32), temporary: true)
        await host.setContext(scope: original, revision: "original")
        await fixture.configure(hold: true)
        let upload = Task { @MainActor in await host.importData(data, name: "fixture.txt") }
        for _ in 0..<100 {
            if await fixture.snapshot().1 { break }; await Task.yield()
        }
        let held = await fixture.snapshot().1; XCTAssertTrue(held)
        let old = Task { @MainActor in await host.setContext(scope: original, revision: "older") }
        for _ in 0..<100 {
            if await fixture.snapshot().2 { break }; await Task.yield()
        }
        let replacement = Task { @MainActor in await host.setContext(scope: latest, revision: "newer") }
        for _ in 0..<10 { await Task.yield() }
        await fixture.release(); await upload.value; await old.value; await replacement.value
        XCTAssertEqual(host.scope, latest); XCTAssertNil(host.preview); XCTAssertTrue(host.selectedIDs.isEmpty)
    }
    @MainActor func testMalformedPreviewOffsetIsRejectedAndCloseJoinsHeldUpload() async throws {
        let data = Data("fixture".utf8), fixture = NativeMediaRPCFixture(data)
        let attachment = try MediaAttachment(await fixture.existing())
        await fixture.configure(corrupt: true)
        let host = store(fixture); await host.setScope(await fixture.scope); await host.openPreview(attachment)
        XCTAssertNil(host.preview); XCTAssertNotNil(host.errorMessage)
        await fixture.configure(hold: true)
        let importing = Task { @MainActor in await host.importData(data, name: "fixture.txt") }
        var entered = false
        for _ in 0..<100 {
            if await fixture.snapshot().1 { entered = true; break }; await Task.yield()
        }
        XCTAssertTrue(entered)
        var closed = false
        let closing = Task { @MainActor in await host.close(); closed = true }
        for _ in 0..<10 { await Task.yield() }
        let cancelled = await fixture.snapshot().2
        XCTAssertFalse(closed); XCTAssertTrue(cancelled)
        await fixture.release(); await importing.value; await closing.value
        XCTAssertTrue(closed); XCTAssertNil(host.scope); XCTAssertTrue(host.attachments.isEmpty)
    }
    func testDescriptorsAndPrivateSourcePolicyRejectCrossScopeOrCredentialNames() throws {
        for path in ["/tmp/.codex/auth.json", "/tmp/.env.local", "/tmp/.jarvis-attachments/file.png", "/tmp/.ssh/key"] {
            XCTAssertFalse(MediaFilePolicy.selectablePath(URL(fileURLWithPath: path)))
        }
        XCTAssertTrue(MediaFilePolicy.selectablePath(URL(fileURLWithPath: "/tmp/jarvis-fixture.png")))
        XCTAssertFalse(MediaFilePolicy.safeName("../file.png"))
        XCTAssertThrowsError(try MediaAttachment(.object(["format": .string("jarvis-attachment/1")])))
    }
}
