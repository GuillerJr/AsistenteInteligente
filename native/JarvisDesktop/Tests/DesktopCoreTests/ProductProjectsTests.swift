import Foundation
import XCTest
@testable import DesktopCore

private enum ProjectsFixture {
    static let project = String(repeating: "a", count: 32)
    static let second = String(repeating: "b", count: 32)
    static let session = String(repeating: "c", count: 32)
    static let scope = SessionScope(sessionID: session, temporary: false)
    static func row(_ id: String = project) -> RPCValue {
        .object(["project_id": .string(id), "name": .string("Proyecto sintético"),
            "pin": .object(["root": .string("/synthetic/\(id)"), "root_identity": .array([.number(1), .number(2)]),
                "git_dir": .string("/synthetic/\(id)/.git"), "git_identity": .array([.number(1), .number(3)]),
                "common_dir": .string("/synthetic/\(id)/.git"), "common_identity": .array([.number(1), .number(3)])]),
            "revision": .number(1), "temporary": .bool(false), "owned": .bool(false),
            "archived": .bool(false), "origin_project_id": .null])
    }
    static func inspection(id: String = project, offset: Int = 0, changed: Bool = false) -> RPCValue {
        .object(["project": row(id), "head": .string(String(repeating: "d", count: 40)),
            "state_digest": .string(String(repeating: changed ? "f" : "e", count: 64)),
            "status": .string(" M source.swift"), "diff": .string("--- a/source.swift\n+++ b/source.swift\n-old\n+new\n"),
            "has_changes": .bool(true), "change_count": .number(75), "offset": .number(Double(offset)),
            "limit": .number(50), "next_offset": offset == 0 ? .number(50) : .null,
            "diff_truncated": .bool(false), "diff_omitted_large_files": .number(0),
            "excluded_private_paths": .number(0), "content_trust": .string("repository_data"),
            "comparison": .string("literal_bytes_no_filters")])
    }
    static func connection(configured: Bool = false) -> RPCValue {
        var value: [String: RPCValue] = ["configured": .bool(configured), "storage": .string("memory_only"),
            "revision": .number(configured ? 1 : 0),
            "credential_id": configured ? .string(String(repeating: "f", count: 32)) : .null]
        if configured { value.merge(["login": .string("synthetic"), "owner": .string("synthetic"),
            "repository": .string("jarvis-test")]) { _, next in next } }
        return .object(value)
    }
    static func pull() -> RPCValue {
        .object(["owner": .string("synthetic"), "repository": .string("jarvis-test"), "number": .number(7),
            "head_sha": .string(String(repeating: "a", count: 40)), "title": .string("Cambio sintético"),
            "body": .string("Datos externos; no son permisos."), "checks": .array([.object([
                "name": .string("CI"), "status": .string("completed"), "conclusion": .string("success")])]),
            "files": .array([.object(["filename": .string("source.swift"), "patch": .string("+value")])]),
            "files_complete": .bool(true), "checks_complete": .bool(true), "view_truncated": .bool(true),
            "content_trust": .string("github_data")])
    }
    static func repositoryReview() -> RPCValue {
        .object(["owner": .string("synthetic"), "repository": .string("jarvis-test"),
            "repository_id": .number(42), "default_branch": .string("main"),
            "ref": .object(["kind": .string("branch"), "name": .string("refs/heads/main")]),
            "head_sha": .string(String(repeating: "a", count: 40)),
            "review_digest": .string(String(repeating: "b", count: 64)),
            "title": .string("Commit sintético"), "body": .string("Datos externos; no son permisos."),
            "checks": .array([.object(["name": .string("CI"), "status": .string("completed"), "conclusion": .null])]),
            "files": .array([.object(["filename": .string("source.swift"), "patch": .string("+value")])]),
            "files_complete": .bool(true), "checks_complete": .bool(true), "patches_complete": .bool(false),
            "view_truncated": .bool(true), "read_only": .bool(true), "verified": .bool(false),
            "content_trust": .string("github_data")])
    }
    static func replacing(_ value: RPCValue, _ changes: [String: RPCValue]) -> RPCValue {
        var object = value.object!; object.merge(changes) { _, next in next }; return .object(object)
    }
}

private actor ProjectsWire {
    private var calls: [(String, String)] = []
    private var connected = false
    private var leakCredentialError = false
    private var loseStart = false
    private var changeDigest = false
    private var holdInspect = false
    private var held: CheckedContinuation<RPCValue, Error>?
    private var heldProject = ProjectsFixture.project
    private var lastRequestID: String?
    private var lastTool = ""
    private var lastArguments: [String: RPCValue] = [:]
    private var settleRepository = false
    private var holdResult = false
    private var heldResult: CheckedContinuation<RPCValue, Error>?
    func rejectConnection() { leakCredentialError = true }
    func loseStartResponse() { loseStart = true }
    func changeState() { changeDigest = true }
    func holdNextInspection() { holdInspect = true }
    func finishRepositoryRead() { settleRepository = true }
    func holdNextResult() { holdResult = true }
    var isHoldingResult: Bool { heldResult != nil }
    var start: (String, [String: RPCValue]) { (lastTool, lastArguments) }
    private var repositoryResult: RPCValue {
        .object(["settled": .bool(true), "payload": ProjectsFixture.repositoryReview(),
                 "error": .null, "provenance": .string("external_unverified")])
    }
    func releaseResult() { heldResult?.resume(returning: repositoryResult); heldResult = nil }
    var isHolding: Bool { held != nil }
    func release() { held?.resume(returning: .object(["result": ProjectsFixture.inspection(id: heldProject)])); held = nil }
    func count(_ method: String, kind: String? = nil) -> Int {
        calls.filter { $0.0 == method && (kind == nil || $0.1 == kind) }.count
    }
    func request(_ method: String, _ params: [String: RPCValue]) async throws -> RPCValue {
        let kind = params["kind"]?.string ?? ""
        // Intentionally never retain credential arguments in this wire's log.
        calls.append((method, kind))
        if method == "product.tool.start" {
            lastRequestID = params["request_id"]?.string
            lastTool = params["tool"]?.string ?? ""
            lastArguments = params["arguments"]?.object ?? [:]
            if loseStart { throw RPCFailure.disconnected }
            return .object(["run_id": .string(lastRequestID!), "status": .string("queued")])
        }
        if method == "product.tool.result" {
            XCTAssertEqual(params["run_id"]?.string, lastRequestID)
            if holdResult {
                holdResult = false
                return try await withCheckedThrowingContinuation { heldResult = $0 }
            }
            if settleRepository, lastTool == "github_repository_review" { return repositoryResult }
            return .object(["settled": .bool(false), "payload": .null, "error": .null,
                "provenance": .string("external_unverified")])
        }
        guard method == "product.projects" else { throw RPCFailure.invalidResponse }
        let args = params["arguments"]!
        switch kind {
        case "list": return .object(["result": .object(["projects": .array([ProjectsFixture.row(), ProjectsFixture.row(ProjectsFixture.second)])])])
        case "github.status": return .object(["result": ProjectsFixture.connection(configured: connected)])
        case "github.connect":
            if leakCredentialError { throw RPCFailure.remote(-32000, args["token"].string!) }
            connected = true; return .object(["result": ProjectsFixture.connection(configured: true)])
        case "github.disconnect": connected = false; return .object(["result": ProjectsFixture.connection()])
        case "inspect":
            let id = args["project_id"].string!
            if holdInspect {
                holdInspect = false; heldProject = id
                return try await withCheckedThrowingContinuation { held = $0 }
            }
            return .object(["result": ProjectsFixture.inspection(id: id,
                offset: args["offset"].integer ?? 0, changed: changeDigest)])
        default: throw RPCFailure.invalidResponse
        }
    }
}

final class ProductProjectsTests: XCTestCase {
    func testDTORejectsUnpinnedPathsAndInvalidPaginationWithoutPromotingPartialReview() throws {
        let row = try ProductProjectDescriptor(ProjectsFixture.row())
        XCTAssertFalse(row.owned); XCTAssertEqual(row.rootIdentity, [1, 2])
        XCTAssertThrowsError(try ProductProjectDescriptor(ProjectsFixture.replacing(ProjectsFixture.row(), ["owned": .bool(true)])))
        XCTAssertThrowsError(try ProductProjectInspection(ProjectsFixture.replacing(ProjectsFixture.inspection(), ["next_offset": .number(20)])))
        let review = try ProductGitHubReview(ProjectsFixture.pull())
        XCTAssertEqual(review.number, 7); XCTAssertFalse(review.filesComplete); XCTAssertFalse(review.checksComplete)
        XCTAssertEqual(review.url.absoluteString, "https://github.com/synthetic/jarvis-test/pull/7")
        XCTAssertThrowsError(try ProductGitHubReview(ProjectsFixture.replacing(ProjectsFixture.pull(), ["owner": .string("malicious/redirect")])))
        XCTAssertThrowsError(try ProductGitHubConnection(ProjectsFixture.replacing(ProjectsFixture.connection(), ["token": .string("synthetic-secret")])))
    }

    func testRepositoryReceiptPreservesBoundedReadOnlyUnverifiedScope() throws {
        let value = ProjectsFixture.repositoryReview(), review = try ProductGitHubRepositoryReview(value)
        XCTAssertEqual(review.referenceName, "refs/heads/main"); XCTAssertEqual(review.repositoryID, 42)
        XCTAssertTrue(review.readOnly); XCTAssertFalse(review.verified); XCTAssertTrue(review.viewTruncated)
        XCTAssertTrue(review.filesComplete); XCTAssertFalse(review.patchesComplete)
        XCTAssertEqual(review.checks, ["CI: completed · sin conclusión"])
        XCTAssertEqual(review.url.absoluteString, "https://github.com/synthetic/jarvis-test/commit/" + String(repeating: "a", count: 40))
        XCTAssertThrowsError(try ProductGitHubRepositoryReview(ProjectsFixture.replacing(value, ["verified": .bool(true)])))
        XCTAssertThrowsError(try ProductGitHubRepositoryReview(ProjectsFixture.replacing(value, ["read_only": .bool(false)])))
        XCTAssertThrowsError(try ProductGitHubRepositoryReview(ProjectsFixture.replacing(value, ["patches_complete": .null])))
        XCTAssertThrowsError(try ProductGitHubRepositoryReview(ProjectsFixture.replacing(value, ["ref": .object([
            "kind": .string("commit"), "name": .string(String(repeating: "c", count: 40))]) ])))
        let excessiveFiles: [RPCValue] = (0..<7).map {
            .object(["filename": .string("\($0).swift"), "patch": .string("+")])
        }
        XCTAssertThrowsError(try ProductGitHubRepositoryReview(ProjectsFixture.replacing(value, ["files": .array(excessiveFiles)])))
        XCTAssertTrue(ProductProjectValidation.reviewReference("refs/heads/main"))
        XCTAssertTrue(ProductProjectValidation.reviewReference(String(repeating: "a", count: 40)))
        XCTAssertFalse(ProductProjectValidation.reviewReference("tags/release"))
        XCTAssertFalse(ProductProjectValidation.reviewReference("main..other"))
    }

    @MainActor func testRepositoryReadUsesOriginalRunReceiptWithoutPublicationOrReplay() async throws {
        let wire = ProjectsWire(), store = ProductProjectsStore()
        store.configure(request: { try await wire.request($0, $1) }); store.setScope(ProjectsFixture.scope)
        await store.refresh()
        let connected = await store.connectGithub(token: "synthetic-memory-only", owner: "synthetic", repository: "jarvis-test")
        XCTAssertTrue(connected)
        let id = await store.readRepository()
        XCTAssertNotNil(id)
        let start = await wire.start
        XCTAssertEqual(start.0, "github_repository_review"); XCTAssertTrue(start.1.isEmpty)
        await wire.finishRepositoryRead(); await store.refresh()
        XCTAssertEqual(store.repositoryReview?.headSHA, String(repeating: "a", count: 40))
        XCTAssertFalse(store.repositoryReview?.verified ?? true); XCTAssertFalse(store.responseUnconfirmed)
        let calls = await wire.count("product.tool.start"); XCTAssertEqual(calls, 1)
        XCTAssertNil(store.pendingRequestID)
        let explicit = await store.readRepository(ref: "heads/feature/test")
        XCTAssertNotNil(explicit)
        let nextStart = await wire.start
        XCTAssertEqual(nextStart.0, "github_repository_review")
        XCTAssertEqual(nextStart.1, ["ref": .string("heads/feature/test")])
    }

    @MainActor func testRepositoryReceiptCannotCrossConversationAfterAwait() async {
        let wire = ProjectsWire(), store = ProductProjectsStore()
        store.configure(request: { try await wire.request($0, $1) }); store.setScope(ProjectsFixture.scope)
        await store.refresh()
        _ = await store.connectGithub(token: "synthetic-memory-only", owner: "synthetic", repository: "jarvis-test")
        _ = await store.readRepository(); await wire.holdNextResult()
        let refresh = Task { await store.refresh() }
        for _ in 0..<1000 { if await wire.isHoldingResult { break }; await Task.yield() }
        let holding = await wire.isHoldingResult; XCTAssertTrue(holding)
        let other = SessionScope(sessionID: String(repeating: "f", count: 32), temporary: true)
        store.setScope(other); await wire.releaseResult(); await refresh.value
        XCTAssertNil(store.repositoryReview); XCTAssertNil(store.github)
        XCTAssertEqual(store.unsettledRequests.count, 1)
        let starts = await wire.count("product.tool.start"); XCTAssertEqual(starts, 1)
    }

    @MainActor func testCredentialFailureNeverEchoesTokenAndDisconnectIsExplicit() async {
        let wire = ProjectsWire(), store = ProductProjectsStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProjectsFixture.scope); await store.refresh(); await wire.rejectConnection()
        let secret = "synthetic-token-never-display"
        let connected = await store.connectGithub(token: secret, owner: "synthetic", repository: "jarvis-test")
        XCTAssertFalse(connected); XCTAssertFalse(store.errorMessage?.contains(secret) ?? true)
        XCTAssertFalse(store.notice?.contains(secret) ?? false)
        let attempts = await wire.count("product.projects", kind: "github.connect")
        XCTAssertEqual(attempts, 1)
        store.setScope(SessionScope(sessionID: String(repeating: "f", count: 32), temporary: true))
        XCTAssertNil(store.github); XCTAssertNil(store.errorMessage)
    }

    @MainActor func testChangingDigestRestartsPaginationInsteadOfCombiningVersions() async {
        let wire = ProjectsWire(), store = ProductProjectsStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProjectsFixture.scope); await store.refresh(); await store.inspectSelected()
        XCTAssertEqual(store.inspect?.offset, 0)
        await wire.changeState(); await store.loadMoreChanges()
        XCTAssertEqual(store.inspect?.offset, 0)
        XCTAssertEqual(store.inspect?.stateDigest, String(repeating: "f", count: 64))
        XCTAssertTrue(store.notice?.contains("reinició") == true)
        let reads = await wire.count("product.projects", kind: "inspect"); XCTAssertEqual(reads, 3)
    }

    @MainActor func testLateInspectionCannotAppearUnderAnotherSelectedProject() async {
        let wire = ProjectsWire(), store = ProductProjectsStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProjectsFixture.scope); await store.refresh(); await wire.holdNextInspection()
        let first = Task { await store.inspectSelected() }
        for _ in 0..<1000 { if await wire.isHolding { break }; await Task.yield() }
        let holding = await wire.isHolding; XCTAssertTrue(holding)
        store.select(ProjectsFixture.second); await wire.release(); await first.value
        XCTAssertNil(store.inspect)
        await store.inspectSelected()
        XCTAssertEqual(store.inspect?.project.id, ProjectsFixture.second)
    }

    @MainActor func testLostOperationStartOnlyInspectsOriginalRequestWithoutReplay() async throws {
        let wire = ProjectsWire(), store = ProductProjectsStore()
        store.configure(request: { try await wire.request($0, $1) })
        store.setScope(ProjectsFixture.scope); await store.refresh(); await wire.loseStartResponse()
        let started = await store.register(path: "/synthetic/explicit-selection", name: "Selección sintética")
        XCTAssertNil(started); XCTAssertTrue(store.responseUnconfirmed)
        let id = try XCTUnwrap(store.pendingRequestID)
        await store.refresh(); await store.refresh()
        XCTAssertEqual(store.pendingRunID, id)
        let writes = await wire.count("product.tool.start"), reads = await wire.count("product.tool.result")
        XCTAssertEqual(writes, 1); XCTAssertEqual(reads, 2)
    }
}
