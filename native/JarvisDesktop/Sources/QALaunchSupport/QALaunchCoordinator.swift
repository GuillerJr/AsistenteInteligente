import Darwin
import Foundation

public struct QALaunchApplication: Sendable {
    public let pid: Int32
    public let bundleID: String
    public let bundleURL: URL
    public let executableURL: URL
    public let launchDate: Date
    public init(pid: Int32, bundleID: String, bundleURL: URL, executableURL: URL, launchDate: Date) {
        self.pid = pid; self.bundleID = bundleID; self.bundleURL = bundleURL
        self.executableURL = executableURL; self.launchDate = launchDate
    }
}

public struct QALaunchProcessIdentity: Codable, Equatable, Sendable {
    public let pid: Int32
    public let parentPID: Int32
    public let startSeconds: UInt64
    public let startMicroseconds: UInt64
    public let executablePath: String
    enum CodingKeys: String, CodingKey {
        case pid, parentPID = "parent_pid", startSeconds = "start_seconds"
        case startMicroseconds = "start_microseconds", executablePath = "executable_path"
    }
    public init(pid: Int32, parentPID: Int32, startSeconds: UInt64, startMicroseconds: UInt64, executablePath: String) {
        self.pid = pid; self.parentPID = parentPID; self.startSeconds = startSeconds
        self.startMicroseconds = startMicroseconds; self.executablePath = executablePath
    }
    public static func observe(_ pid: Int32) -> Self? {
        guard pid > 1 else { return nil }
        var info = proc_bsdinfo()
        let size = MemoryLayout<proc_bsdinfo>.size
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(size)) == size,
              info.pbi_pid == UInt32(pid) else { return nil }
        var buffer = [CChar](repeating: 0, count: 4096)
        guard proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 else { return nil }
        return Self(pid: pid, parentPID: Int32(info.pbi_ppid), startSeconds: info.pbi_start_tvsec,
                    startMicroseconds: info.pbi_start_tvusec, executablePath: String(cString: buffer))
    }
}

@MainActor public protocol QALaunchDriver: AnyObject {
    func open(_ admission: QALaunchAdmission) async throws -> QALaunchApplication
}

public struct QALaunchReceipt: Codable, Sendable {
    public let schema: String
    public let operationID: String
    public var status: String
    public var launchEffectStatus: String
    public let requestSHA256: String
    public let argumentsSHA256: String
    public let environmentSHA256: String
    public let bundleID: String
    public let bundlePath: String
    public let executablePath: String
    public let executableSHA256: String
    public let launchRequestedUnix: Double
    public var launchDateUnix: Double?
    public var process: QALaunchProcessIdentity?
    public var returnedCandidate: QALaunchCandidate?
    public var errorCode: String?
    public let createsNewApplicationInstance: Bool
    public let allowsRunningApplicationSubstitution: Bool
    public let returnedApplicationOnly: Bool
    public let normalAppQuitClaimed: Bool
    public let credentialsOrArgumentsLogged: Bool
    enum CodingKeys: String, CodingKey {
        case schema, status, process
        case returnedCandidate = "returned_candidate_unconfirmed"
        case operationID = "operation_id", launchEffectStatus = "launch_effect_status"
        case requestSHA256 = "request_sha256", argumentsSHA256 = "arguments_sha256", environmentSHA256 = "environment_sha256"
        case bundleID = "bundle_id", bundlePath = "bundle_path", executablePath = "executable_path"
        case executableSHA256 = "executable_sha256", launchRequestedUnix = "launch_requested_unix"
        case launchDateUnix = "launch_date_unix", errorCode = "error_code"
        case createsNewApplicationInstance = "creates_new_application_instance"
        case allowsRunningApplicationSubstitution = "allows_running_application_substitution"
        case returnedApplicationOnly = "returned_application_only", normalAppQuitClaimed = "normal_app_quit_claimed"
        case credentialsOrArgumentsLogged = "credentials_or_arguments_logged"
    }
}

/// Returned-app metadata is retained even when later checks fail. It is not signal authority.
public struct QALaunchCandidate: Codable, Sendable {
    public let pid: Int32
    public let bundleID: String
    public let bundlePath: String
    public let executablePath: String
    public let launchDateUnix: Double
    public let independentlyConfirmed: Bool
    enum CodingKeys: String, CodingKey {
        case pid, bundleID = "bundle_id", bundlePath = "bundle_path", executablePath = "executable_path"
        case launchDateUnix = "launch_date_unix", independentlyConfirmed = "independently_confirmed"
    }
    init(_ app: QALaunchApplication) {
        pid = app.pid; bundleID = app.bundleID; bundlePath = app.bundleURL.path
        executablePath = app.executableURL.path; launchDateUnix = app.launchDate.timeIntervalSince1970
        independentlyConfirmed = false
    }
}

/// If disk custody fails after an effect, callers can retain this bounded metadata in RAM/stderr.
/// It contains neither configuration arguments nor credentials and must never trigger a retry.
public struct QALaunchUncertainFailure: Error {
    public let issue: QALaunchIssue
    public let receipt: QALaunchReceipt
}

/// Owns one exclusive receipt inode. Repeated requests never launch again, including uncertain results.
@MainActor public final class QALaunchCoordinator {
    private let driver: any QALaunchDriver
    private let observe: (Int32) -> QALaunchProcessIdentity?
    private let now: () -> Date
    public init(driver: any QALaunchDriver,
                observe: @escaping (Int32) -> QALaunchProcessIdentity? = QALaunchProcessIdentity.observe,
                now: @escaping () -> Date = Date.init) {
        self.driver = driver; self.observe = observe; self.now = now
    }

    public func launch(requestURL: URL, receiptURL: URL) async throws -> QALaunchReceipt {
        let admission = try QALaunchAdmission.admit(requestURL: requestURL, receiptURL: receiptURL)
        let descriptor = Darwin.open(receiptURL.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw errno == EEXIST ? QALaunchIssue.receiptExists : QALaunchIssue.receiptWrite }
        defer { close(descriptor) }
        var receipt = QALaunchReceipt(schema: "jarvis.qa-launch-receipt.v1", operationID: UUID().uuidString.lowercased(),
            status: "requested", launchEffectStatus: "unknown", requestSHA256: admission.requestSHA256,
            argumentsSHA256: admission.argumentsSHA256, environmentSHA256: admission.environmentSHA256,
            bundleID: admission.request.expectedBundleID, bundlePath: admission.appURL.path,
            executablePath: admission.executableURL.path, executableSHA256: admission.request.expectedExecutableSHA256,
            launchRequestedUnix: now().timeIntervalSince1970, createsNewApplicationInstance: true,
            allowsRunningApplicationSubstitution: false, returnedApplicationOnly: true,
            normalAppQuitClaimed: false, credentialsOrArgumentsLogged: false)
        // A durable intent precedes the only launch call. No attempt is made if persistence fails.
        try persist(receipt, descriptor: descriptor, path: receiptURL)
        do {
            try admission.revalidate()
            try verifyReceiptIdentity(descriptor, path: receiptURL)
            let app = try await driver.open(admission)
            receipt.returnedCandidate = QALaunchCandidate(app)
            guard app.pid > 1, app.bundleID == admission.request.expectedBundleID,
                  app.bundleURL.standardizedFileURL == admission.appURL,
                  app.executableURL.standardizedFileURL == admission.executableURL,
                  app.launchDate.timeIntervalSince1970 >= receipt.launchRequestedUnix - 1,
                  app.launchDate <= now().addingTimeInterval(2) else { throw QALaunchIssue.returnedApplication }
            guard let process = observe(app.pid), process.pid == app.pid,
                  process.executablePath == admission.executableURL.path,
                  Double(process.startSeconds) >= receipt.launchRequestedUnix - 1,
                  Double(process.startSeconds) <= now().timeIntervalSince1970 + 2
            else { throw QALaunchIssue.processIdentity }
            // A changed request/bundle cannot gain a known receipt after Launch Services returns.
            try admission.revalidate()
            guard observe(app.pid) == process else { throw QALaunchIssue.processIdentity }
            receipt.status = "launched"; receipt.launchEffectStatus = "known"
            receipt.process = process; receipt.launchDateUnix = app.launchDate.timeIntervalSince1970
            try persist(receipt, descriptor: descriptor, path: receiptURL)
            return receipt
        } catch {
            receipt.status = "unknown"; receipt.launchEffectStatus = "unknown"
            receipt.errorCode = (error as? QALaunchIssue)?.rawValue ?? QALaunchIssue.launchServices.rawValue
            // Error text from Launch Services may contain request data. Preserve only a stable code.
            do { try persist(receipt, descriptor: descriptor, path: receiptURL) }
            catch { throw QALaunchUncertainFailure(issue: .receiptWrite, receipt: receipt) }
            throw (error as? QALaunchIssue) ?? QALaunchIssue.launchServices
        }
    }

    private func verifyReceiptIdentity(_ descriptor: Int32, path: URL) throws {
        var held = stat(), named = stat()
        guard fstat(descriptor, &held) == 0, lstat(path.path, &named) == 0,
              held.st_dev == named.st_dev, held.st_ino == named.st_ino,
              (held.st_mode & S_IFMT) == S_IFREG, (named.st_mode & S_IFMT) == S_IFREG,
              held.st_nlink == 1, named.st_nlink == 1,
              held.st_uid == geteuid(), named.st_uid == geteuid()
        else { throw QALaunchIssue.receiptWrite }
    }

    private func persist(_ receipt: QALaunchReceipt, descriptor: Int32, path: URL) throws {
        try verifyReceiptIdentity(descriptor, path: path)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        var data = try encoder.encode(receipt); data.append(0x0a)
        guard data.count <= 16_384 else { throw QALaunchIssue.receiptWrite }
        let written: Bool = data.withUnsafeBytes { bytes in
            guard let base = bytes.baseAddress else { return false }
            var offset = 0
            while offset < bytes.count {
                let result = pwrite(descriptor, base.advanced(by: offset), bytes.count - offset, off_t(offset))
                if result < 0 && errno == EINTR { continue }
                guard result > 0 else { return false }
                offset += result
            }
            return true
        }
        guard written, ftruncate(descriptor, off_t(data.count)) == 0, fsync(descriptor) == 0 else { throw QALaunchIssue.receiptWrite }
        try verifyReceiptIdentity(descriptor, path: path)
    }
}
