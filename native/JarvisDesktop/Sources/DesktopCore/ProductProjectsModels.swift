import Foundation

public enum ProductProjectValidation {
    public static func oid(_ value: String) -> Bool {
        value.range(of: "^(?:[a-f0-9]{40}|[a-f0-9]{64})\\z", options: .regularExpression) != nil
    }
    public static func repository(owner: String, name: String) -> Bool {
        owner.range(of: "^[A-Za-z0-9][A-Za-z0-9-]{0,38}\\z", options: .regularExpression) != nil
            && name.range(of: "^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\\z", options: .regularExpression) != nil
            && name != "." && name != ".."
    }
    public static func branch(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}\\z", options: .regularExpression) != nil
            && !value.contains("..") && !value.contains("//") && !value.hasSuffix("/")
            && !value.hasSuffix(".") && !value.hasSuffix(".lock")
            && !value.split(separator: "/").contains { $0.hasPrefix(".") }
    }
    public static func reviewReference(_ value: String) -> Bool {
        if value.count == 40, oid(value) { return true }
        if value.hasPrefix("refs/heads/") { return branch(String(value.dropFirst(11))) }
        if value.hasPrefix("heads/") { return branch(String(value.dropFirst(6))) }
        return !value.hasPrefix("refs/") && !value.hasPrefix("tags/") && branch(value)
    }
    static func text(_ value: RPCValue, limit: Int, empty: Bool = false) throws -> String {
        try ProductInteractionValidation.text(value, limit: limit, empty: empty)
    }
    static func path(_ value: RPCValue) throws -> String {
        let value = try text(value, limit: 4096)
        guard value.hasPrefix("/"), !value.split(separator: "/").contains("..") else {
            throw RPCFailure.invalidResponse
        }
        return value
    }
    static func pair(_ value: RPCValue) throws -> [Int] {
        guard let array = value.array, array.count == 2, let first = array[0].integer,
              let second = array[1].integer, first >= 0, second >= 0 else {
            throw RPCFailure.invalidResponse
        }
        return [first, second]
    }
}

public struct ProductProjectDescriptor: Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let root: String
    public let rootIdentity: [Int]
    public let revision: Int
    public let temporary: Bool
    public let owned: Bool
    public let archived: Bool
    public let originID: String?
    public init(_ value: RPCValue) throws {
        id = try ProductCoordinationValidation.id(value["project_id"])
        name = try ProductProjectValidation.text(value["name"], limit: 256)
        root = try ProductProjectValidation.path(value["pin"]["root"])
        rootIdentity = try ProductProjectValidation.pair(value["pin"]["root_identity"])
        _ = try ProductProjectValidation.path(value["pin"]["git_dir"])
        _ = try ProductProjectValidation.path(value["pin"]["common_dir"])
        _ = try ProductProjectValidation.pair(value["pin"]["git_identity"])
        _ = try ProductProjectValidation.pair(value["pin"]["common_identity"])
        revision = try ProductCoordinationValidation.revision(value["revision"])
        guard let temporary = value["temporary"].bool, let owned = value["owned"].bool,
              let archived = value["archived"].bool else { throw RPCFailure.invalidResponse }
        self.temporary = temporary; self.owned = owned; self.archived = archived
        originID = value["origin_project_id"] == .null ? nil
            : try ProductCoordinationValidation.id(value["origin_project_id"])
        guard originID != id, !owned || originID != nil else { throw RPCFailure.invalidResponse }
    }
}

public struct ProductProjectInspection: Equatable, Sendable {
    public let project: ProductProjectDescriptor
    public let head: String
    public let stateDigest: String
    public let status: String
    public let diff: String
    public let hasChanges: Bool
    public let changeCount: Int
    public let offset: Int
    public let limit: Int
    public let nextOffset: Int?
    public let diffTruncated: Bool
    public let omittedLargeFiles: Int
    public let excludedPrivatePaths: Int
    public init(_ value: RPCValue) throws {
        try ProductInteractionValidation.bounded(value)
        project = try ProductProjectDescriptor(value["project"])
        head = try ProductProjectValidation.text(value["head"], limit: 64)
        stateDigest = try ProductProjectValidation.text(value["state_digest"], limit: 64)
        status = try ProductProjectValidation.text(value["status"], limit: 262144, empty: true)
        diff = try ProductProjectValidation.text(value["diff"], limit: 65540, empty: true)
        guard ProductProjectValidation.oid(head), ManagementValidation.hash(stateDigest),
              let hasChanges = value["has_changes"].bool,
              let count = value["change_count"].integer, (0...8192).contains(count),
              let offset = value["offset"].integer, offset >= 0,
              let limit = value["limit"].integer, (1...50).contains(limit),
              let truncated = value["diff_truncated"].bool,
              let omitted = value["diff_omitted_large_files"].integer, omitted >= 0,
              let excluded = value["excluded_private_paths"].integer, excluded >= 0,
              value["content_trust"].string == "repository_data",
              value["comparison"].string == "literal_bytes_no_filters",
              hasChanges == (count > 0) else { throw RPCFailure.invalidResponse }
        self.hasChanges = hasChanges; changeCount = count; self.offset = offset; self.limit = limit
        nextOffset = value["next_offset"] == .null ? nil : value["next_offset"].integer
        guard value["next_offset"] == .null || nextOffset == offset + limit,
              nextOffset == nil || nextOffset! < count else { throw RPCFailure.invalidResponse }
        diffTruncated = truncated; omittedLargeFiles = omitted; excludedPrivatePaths = excluded
    }
}

public struct ProductGitHubConnection: Equatable, Sendable {
    public let configured: Bool
    public let login: String?
    public let owner: String?
    public let repository: String?
    public var destination: String? {
        guard let owner, let repository else { return nil }; return "\(owner)/\(repository)"
    }
    public init(_ value: RPCValue) throws {
        guard let object = value.object, let configured = value["configured"].bool,
              Set(object.keys).isSubset(of: ["configured", "login", "owner", "repository",
                "credential_storage", "storage", "credential_id", "revision", "service", "id"]),
              object["token"] == nil else {
            throw RPCFailure.invalidResponse
        }
        self.configured = configured
        login = value["login"] == .null ? nil : try ProductProjectValidation.text(value["login"], limit: 128)
        owner = value["owner"] == .null ? nil : try ProductProjectValidation.text(value["owner"], limit: 39)
        repository = value["repository"] == .null ? nil
            : try ProductProjectValidation.text(value["repository"], limit: 100)
        if let owner, let repository, !ProductProjectValidation.repository(owner: owner, name: repository) {
            throw RPCFailure.invalidResponse
        }
        if value["credential_id"] != .null { _ = try ProductCoordinationValidation.id(value["credential_id"]) }
        if value["revision"] != .null { _ = try ProductCoordinationValidation.revision(value["revision"], zero: true) }
        guard value["credential_storage"] == .null || value["credential_storage"].string == "memory_only",
              value["storage"] == .null || value["storage"].string == "memory_only" else {
            throw RPCFailure.invalidResponse
        }
    }
}

public struct ProductGitHubFile: Identifiable, Equatable, Sendable {
    public let path: String
    public var id: String { path }
    public let status: String
    public let patch: String?
    public init(_ value: RPCValue) throws {
        path = try ProductProjectValidation.text(value["filename"], limit: 4096)
        status = value["status"] == .null ? "Parche consultado"
            : try ProductProjectValidation.text(value["status"], limit: 32)
        patch = value["patch"] == .null ? nil
            : try ProductProjectValidation.text(value["patch"], limit: 262144, empty: true)
    }
}

public struct ProductGitHubReview: Equatable, Sendable {
    public let owner: String
    public let repository: String
    public let number: Int
    public let headSHA: String
    public let title: String
    public let body: String
    public let state: String
    public let url: URL
    public let files: [ProductGitHubFile]
    public let checks: [String]
    public let filesComplete: Bool
    public let checksComplete: Bool
    public init(_ value: RPCValue) throws {
        try ProductInteractionValidation.bounded(value)
        owner = try ProductProjectValidation.text(value["owner"], limit: 39)
        repository = try ProductProjectValidation.text(value["repository"], limit: 100)
        headSHA = try ProductProjectValidation.text(value["head_sha"], limit: 40)
        guard let number = value["number"].integer, (1..<2_147_483_648).contains(number),
              ProductProjectValidation.repository(owner: owner, name: repository),
              headSHA.count == 40, ProductProjectValidation.oid(headSHA),
              let filesComplete = value["files_complete"].bool,
              let checksComplete = value["checks_complete"].bool,
              let truncated = value["view_truncated"].bool,
              value["content_trust"].string == "github_data" else { throw RPCFailure.invalidResponse }
        self.number = number
        self.filesComplete = filesComplete && !truncated; self.checksComplete = checksComplete && !truncated
        title = try ProductProjectValidation.text(value["title"], limit: 1024)
        body = value["body"] == .null ? "" : try ProductProjectValidation.text(value["body"], limit: 65536, empty: true)
        state = "No incluido en este recibo"
        let expected = "https://github.com/\(owner)/\(repository)/pull/\(number)"
        guard let url = URL(string: expected) else { throw RPCFailure.invalidResponse }
        self.url = url
        files = try ManagementValidation.rows(value["files"], limit: 100, parse: ProductGitHubFile.init)
        checks = try ManagementValidation.rows(value["checks"], limit: 100) { check in
            let name = try ProductProjectValidation.text(check["name"], limit: 512)
            let status = try ProductProjectValidation.text(check["status"], limit: 32)
            let conclusion = check["conclusion"].string ?? "sin conclusión"
            guard conclusion.utf8.count <= 128 else { throw RPCFailure.invalidResponse }
            return "\(name): \(status) · \(conclusion)"
        }
        guard Set(files.map(\.id)).count == files.count else { throw RPCFailure.invalidResponse }
    }
}

/// A bounded receipt for one repository reference. The digest identifies the
/// backend read; neither its presence nor passing checks verifies a code audit.
public struct ProductGitHubRepositoryReview: Equatable, Sendable {
    public let owner: String
    public let repository: String
    public let repositoryID: Int
    public let defaultBranch: String
    public let referenceKind: String
    public let referenceName: String
    public let headSHA: String
    public let reviewDigest: String
    public let title: String
    public let body: String
    public let checks: [String]
    public let files: [ProductGitHubFile]
    public let filesComplete: Bool
    public let checksComplete: Bool
    public let patchesComplete: Bool
    public let viewTruncated: Bool
    public let readOnly: Bool
    public let verified: Bool
    public let url: URL
    public init(_ value: RPCValue) throws {
        try ProductInteractionValidation.bounded(value)
        owner = try ProductProjectValidation.text(value["owner"], limit: 39)
        repository = try ProductProjectValidation.text(value["repository"], limit: 100)
        defaultBranch = try ProductProjectValidation.text(value["default_branch"], limit: 255)
        referenceKind = try ProductProjectValidation.text(value["ref"]["kind"], limit: 16)
        referenceName = try ProductProjectValidation.text(value["ref"]["name"], limit: 266)
        headSHA = try ProductProjectValidation.text(value["head_sha"], limit: 40)
        reviewDigest = try ProductProjectValidation.text(value["review_digest"], limit: 64)
        guard let repositoryID = value["repository_id"].integer, repositoryID > 0,
              ProductProjectValidation.repository(owner: owner, name: repository),
              ProductProjectValidation.branch(defaultBranch),
              headSHA.count == 40, ProductProjectValidation.oid(headSHA),
              ManagementValidation.hash(reviewDigest),
              (referenceKind == "commit" && referenceName == headSHA)
                || (referenceKind == "branch" && referenceName.hasPrefix("refs/heads/")
                    && ProductProjectValidation.branch(String(referenceName.dropFirst(11)))),
              let filesComplete = value["files_complete"].bool,
              let checksComplete = value["checks_complete"].bool,
              let patchesComplete = value["patches_complete"].bool,
              let truncated = value["view_truncated"].bool,
              value["read_only"].bool == true, value["verified"].bool == false,
              value["content_trust"].string == "github_data" else { throw RPCFailure.invalidResponse }
        self.repositoryID = repositoryID
        self.filesComplete = filesComplete; self.checksComplete = checksComplete
        self.patchesComplete = patchesComplete; viewTruncated = truncated
        readOnly = true; verified = false
        title = try ProductProjectValidation.text(value["title"], limit: 512, empty: true)
        body = try ProductProjectValidation.text(value["body"], limit: 2048, empty: true)
        checks = try ManagementValidation.rows(value["checks"], limit: 20) { check in
            let name = try ProductProjectValidation.text(check["name"], limit: 160)
            let status = try ProductProjectValidation.text(check["status"], limit: 160)
            let conclusion = check["conclusion"] == .null ? "sin conclusión"
                : try ProductProjectValidation.text(check["conclusion"], limit: 160, empty: true)
            return "\(name): \(status) · \(conclusion)"
        }
        files = try ManagementValidation.rows(value["files"], limit: 6) { file in
            _ = try ProductProjectValidation.text(file["filename"], limit: 512)
            if file["patch"] != .null { _ = try ProductProjectValidation.text(file["patch"], limit: 1024, empty: true) }
            return try ProductGitHubFile(file)
        }
        guard Set(files.map(\.id)).count == files.count,
              let url = URL(string: "https://github.com/\(owner)/\(repository)/commit/\(headSHA)") else {
            throw RPCFailure.invalidResponse
        }
        self.url = url
    }
}
