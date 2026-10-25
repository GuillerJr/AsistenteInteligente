import Darwin
import Foundation

/// Private GCD queue owns the blocking descriptor. No network work runs on the UI actor.
/// Tokens and POSIX ownership are base authentication, not signed macOS client attestation.
public final class LocalRPCClient: @unchecked Sendable {
    private let runtime: ProfileRuntimeGuard
    private let directory: String
    private let timeout: TimeInterval
    private let connectionIdleLimit: TimeInterval
    private let queue = DispatchQueue(label: "jarvis.local-rpc")
    private var descriptor: Int32 = -1
    private var token = ""
    private var nextID = 0
    private var lastResponseAt: TimeInterval?
    private var authorizationFailure: RPCFailure?

    public init(stateDirectory: String, timeout: TimeInterval = 30,
                connectionIdleLimit: TimeInterval = 20, runtime: ProfileRuntimeGuard? = nil) {
        precondition(timeout.isFinite && timeout > 0 && timeout <= 30)
        precondition(connectionIdleLimit.isFinite && connectionIdleLimit > 0 && connectionIdleLimit <= 30)
        self.runtime = runtime ?? ProfileRuntimeGuard()
        directory = stateDirectory
        self.timeout = timeout
        self.connectionIdleLimit = connectionIdleLimit
    }

    public var isActive: Bool { runtime.isActive }

    public func call(_ method: String, params: [String: RPCValue] = [:]) async throws -> RPCValue {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                do { continuation.resume(returning: try perform(method, params: params)) }
                catch { continuation.resume(throwing: error) }
            }
        }
    }

    public func retire() async { runtime.retire(); await close() }

    public func close() async {
        await withCheckedContinuation { continuation in
            queue.async { [self] in disconnect(clearAuthorization: true); continuation.resume() }
        }
    }

    private func disconnect(clearAuthorization: Bool = false) {
        if descriptor >= 0 { Darwin.close(descriptor) }
        descriptor = -1
        token = ""
        lastResponseAt = nil
        if clearAuthorization { authorizationFailure = nil }
    }

    private func inspect(_ path: String, type: mode_t, mode: mode_t) throws -> stat {
        var info = stat()
        guard lstat(path, &info) == 0 else {
            if errno == ENOENT || errno == ENOTDIR { throw RPCFailure.unavailable }
            throw RPCFailure.insecurePath
        }
        guard (info.st_mode & S_IFMT) == type,
              info.st_uid == getuid(), (info.st_mode & 0o777) == mode else {
            throw RPCFailure.insecurePath
        }
        return info
    }

    private func connect() throws {
        let components = directory.split(separator: "/", omittingEmptySubsequences: false)
        guard directory.hasPrefix("/"), !components.contains(".."), !components.contains("."),
              !directory.contains("//"), !directory.hasSuffix("/") else {
            throw RPCFailure.configuration
        }
        // standardizedFileURL may map /private/tmp to /tmp on macOS. Inspect the
        // literal path instead so that symlinks cannot be hidden by normalization.
        var component = directory
        while component != "/" {
            var info = stat()
            guard lstat(component, &info) == 0 else {
                if errno == ENOENT { throw RPCFailure.unavailable }
                throw RPCFailure.insecurePath
            }
            guard (info.st_mode & S_IFMT) == S_IFDIR else {
                throw RPCFailure.insecurePath
            }
            guard let separator = component.lastIndex(of: "/") else { throw RPCFailure.insecurePath }
            component = separator == component.startIndex ? "/" : String(component[..<separator])
        }
        _ = try inspect(directory, type: S_IFDIR, mode: 0o700)
        let tokenPath = directory + "/transport.token"
        let credential = Darwin.open(tokenPath, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard credential >= 0 else { throw RPCFailure.unavailable }
        defer { Darwin.close(credential) }
        var info = stat()
        guard fstat(credential, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == getuid(), (info.st_mode & 0o777) == 0o600,
              info.st_nlink == 1, info.st_size == 64 else { throw RPCFailure.insecurePath }
        var bytes = [UInt8](repeating: 0, count: 65)
        let count = Darwin.read(credential, &bytes, bytes.count)
        guard count == 64, let secret = String(bytes: bytes.prefix(64), encoding: .ascii),
              secret.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
            throw RPCFailure.insecurePath
        }
        let socketPath = directory + "/service.sock"
        guard socketPath.utf8.count <= 103 else { throw RPCFailure.configuration }
        _ = try inspect(socketPath, type: S_IFSOCK, mode: 0o600)
        let fd = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw RPCFailure.unavailable }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &address.sun_path) { buffer in
            for (index, byte) in socketPath.utf8CString.enumerated() { buffer[index] = UInt8(bitPattern: byte) }
        }
        let seconds = Int(timeout)
        var timeout = timeval(tv_sec: seconds, tv_usec: Int32((self.timeout - Double(seconds)) * 1_000_000))
        var noSignal: Int32 = 1
        guard setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout))) == 0,
              setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout.size(ofValue: timeout))) == 0,
              setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, socklen_t(MemoryLayout.size(ofValue: noSignal))) == 0
        else { Darwin.close(fd); throw RPCFailure.unavailable }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { Darwin.close(fd); throw RPCFailure.unavailable }
        let flags = fcntl(fd, F_GETFL)
        guard flags >= 0, fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0 else {
            Darwin.close(fd); throw RPCFailure.unavailable
        }
        descriptor = fd
        token = secret
        lastResponseAt = ProcessInfo.processInfo.systemUptime
    }

    private func waitForDescriptor(_ events: Int16, deadline: TimeInterval) throws {
        while true {
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0 else { throw RPCFailure.disconnected }
            var item = pollfd(fd: descriptor, events: events, revents: 0)
            let milliseconds = Int32(min(Double(Int32.max), max(1, ceil(remaining * 1_000))))
            let result = Darwin.poll(&item, 1, milliseconds)
            if result < 0, errno == EINTR { continue }
            guard result > 0, item.revents & events != 0 else { throw RPCFailure.disconnected }
            return
        }
    }

    private func perform(_ method: String, params: [String: RPCValue]) throws -> RPCValue {
        try runtime.check()
        // No request has been written yet. A modal owner review can outlive
        // the server's idle deadline; renew that stale connection once before
        // sending new work. This is not a retry after an ambiguous mutation.
        if let authorizationFailure { throw authorizationFailure }
        if descriptor >= 0, let lastResponseAt,
           ProcessInfo.processInfo.systemUptime - lastResponseAt >= connectionIdleLimit {
            disconnect()
        }
        if descriptor < 0 { try runtime.check(); try connect() }
        nextID = nextID % 9_007_199_254_740_991 + 1
        let frame = try RPCFrame.request(id: nextID, method: method, params: params, token: token)
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        do {
            try runtime.check()
            try frame.withUnsafeBytes { buffer in
                var sent = 0
                while sent < buffer.count {
                    try waitForDescriptor(Int16(POLLOUT), deadline: deadline)
                    let count = try runtime.withActive { Darwin.write(descriptor, buffer.baseAddress!.advanced(by: sent), buffer.count - sent) }
                    if count < 0, errno == EINTR || errno == EAGAIN { continue }
                    guard count > 0 else { throw RPCFailure.disconnected }
                    sent += count
                }
            }
            var response = Data()
            var bytes = [UInt8](repeating: 0, count: 4096)
            while response.count < RPCFrame.limit {
                try waitForDescriptor(Int16(POLLIN), deadline: deadline)
                let count = Darwin.read(descriptor, &bytes, min(bytes.count, RPCFrame.limit - response.count))
                if count < 0, errno == EINTR || errno == EAGAIN { continue }
                guard count > 0 else { throw RPCFailure.disconnected }
                response.append(contentsOf: bytes.prefix(count))
                if bytes.prefix(count).contains(10) {
                    let result = try RPCFrame.response(response, expectedID: nextID)
                    lastResponseAt = ProcessInfo.processInfo.systemUptime
                    try runtime.check()
                    return result
                }
            }
            throw RPCFailure.oversized
        } catch RPCFailure.remote(let code, let message) {
            // A complete, validated remote error leaves framing synchronized.
            lastResponseAt = ProcessInfo.processInfo.systemUptime
            if code == -32001 { authorizationFailure = .remote(code, message) }
            throw RPCFailure.remote(code, message)
        } catch {
            disconnect()
            throw error
        }
    }
}
