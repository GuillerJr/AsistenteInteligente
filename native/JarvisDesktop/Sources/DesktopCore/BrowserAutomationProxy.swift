import Foundation
import Network

/// Private authenticated CONNECT proxy. WebKit cannot bypass it on failure.
/// DNS is screened and each tunnel opens the selected numeric address, while
/// WebKit remains responsible for TLS hostname/certificate verification.
final class BrowserAutomationProxy: @unchecked Sendable {
    private let policy: BrowserAutomationPolicy
    private let queue = DispatchQueue(label: "jarvis.browser.network")
    private let credential = UUID().uuidString + UUID().uuidString
    private var listener: NWListener?
    private var connections: [UUID: BrowserProxyConnection] = [:]
    private var stopped = false
    private var forwarded = 0
    private var authorized = 0

    init(policy: BrowserAutomationPolicy) { self.policy = policy }

    func start() async throws -> ProxyConfiguration {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                do {
                    let parameters = NWParameters.tcp
                    parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
                    let listener = try NWListener(using: parameters)
                    self.listener = listener
                    var completed = false
                    listener.stateUpdateHandler = { [weak self] state in
                        guard let self else { return }
                        switch state {
                        case .ready:
                            guard !completed, !self.stopped, let port = listener.port else { return }
                            completed = true
                            var config = ProxyConfiguration(httpCONNECTProxy:
                                .hostPort(host: "127.0.0.1", port: port))
                            config.allowFailover = false
                            config.matchDomains = ["", "127.0.0.1", "::1"]
                            config.excludedDomains = []
                            config.applyCredential(username: "jarvis", password: self.credential)
                            continuation.resume(returning: config)
                        case .failed, .cancelled:
                            if !completed { completed = true; continuation.resume(throwing: BrowserAutomationError.unavailable) }
                        default: break
                        }
                    }
                    listener.newConnectionHandler = { [weak self] connection in
                        guard let self, !self.stopped, self.connections.count < 32 else {
                            connection.cancel(); return
                        }
                        let id = UUID()
                        let entry = BrowserProxyConnection(connection, policy: self.policy,
                            credential: self.credential, queue: self.queue, onAuthorized: { [weak self] in self?.authorized += 1 }, onForward: { [weak self] in self?.forwarded += 1 }) { [weak self] in
                            self?.connections.removeValue(forKey: id)
                        }
                        self.connections[id] = entry
                        entry.start()
                    }
                    listener.start(queue: queue)
                    queue.asyncAfter(deadline: .now() + 5) {
                        if !completed { listener.cancel() }
                    }
                } catch { continuation.resume(throwing: error) }
            }
        }
    }

    func authorizedCount() async -> Int {
        await withCheckedContinuation { continuation in queue.async { [self] in continuation.resume(returning: authorized) } }
    }

    func forwardedCount() async -> Int {
        await withCheckedContinuation { continuation in queue.async { [self] in continuation.resume(returning: forwarded) } }
    }

    func stop() async {
        await withCheckedContinuation { continuation in
            queue.async { [self] in
                stopped = true
                listener?.cancel(); listener = nil
                let old = Array(connections.values)
                for entry in old { entry.close() }
                continuation.resume()
            }
        }
    }
}

private final class BrowserProxyConnection: @unchecked Sendable {
    let client: NWConnection
    let policy: BrowserAutomationPolicy
    let credential: String
    let queue: DispatchQueue
    let finished: () -> Void
    let onForward: () -> Void
    let onAuthorized: () -> Void
    var upstream: NWConnection?
    var input = Data()
    var closed = false
    var transferred = 0

    init(_ client: NWConnection, policy: BrowserAutomationPolicy, credential: String,
         queue: DispatchQueue, onAuthorized: @escaping () -> Void, onForward: @escaping () -> Void, finished: @escaping () -> Void) {
        self.client = client; self.policy = policy; self.credential = credential
        self.queue = queue; self.finished = finished; self.onForward = onForward; self.onAuthorized = onAuthorized
    }

    func start() {
        client.start(queue: queue)
        readHeader()
        queue.asyncAfter(deadline: .now() + 60) { [weak self] in self?.close() }
    }

    func close() {
        guard !closed else { return }
        closed = true; client.cancel(); upstream?.cancel(); finished()
    }

    func readHeader() {
        client.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self] data, _, done, error in
            guard let self, !closed else { return }
            if let data { input.append(data) }
            guard input.count <= 16_384, error == nil else { close(); return }
            if let end = input.range(of: Data([13, 10, 13, 10])) {
                let head = input[..<end.lowerBound]
                let remainder = input[end.upperBound...]
                parse(Data(head), remainder: Data(remainder))
            } else if done { close() } else { readHeader() }
        }
    }

    func parse(_ data: Data, remainder: Data) {
        guard let text = String(data: data, encoding: .utf8) else { close(); return }
        let lines = text.components(separatedBy: "\r\n")
        let first = lines.first?.split(separator: " ").map(String.init) ?? []
        let credentials = lines.dropFirst().filter { $0.lowercased().hasPrefix("proxy-authorization:") }
        let expected = "Basic " + Data("jarvis:\(credential)".utf8).base64EncodedString()
        guard first.count == 3, first[2] == "HTTP/1.1", credentials.count == 1,
              credentials[0].split(separator: ":", maxSplits: 1).last?
                .trimmingCharacters(in: .whitespaces) == expected else {
            client.send(content: Data("HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm=\"Jarvis\"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".utf8),
                completion: .contentProcessed { [weak self] _ in self?.close() })
            return
        }
        onAuthorized()
        let connect = first[0] == "CONNECT"
        let destination: URL
        var initial = remainder
        do {
            if connect {
                guard !first[1].contains("/"), !first[1].contains("@"), !first[1].contains("?")
                else { throw BrowserAutomationError.forbiddenURL }
                let secure = try policy.validate("https://" + first[1])
                destination = secure
            } else {
                destination = try policy.validate(first[1])
                guard destination.scheme == "http", policy.testOrigins.contains(BrowserAutomationPolicy.origin(destination)),
                      ["GET", "HEAD", "POST"].contains(first[0]),
                      !lines.contains(where: { $0.lowercased().hasPrefix("transfer-encoding:") })
                else { throw BrowserAutomationError.forbiddenURL }
                var components = URLComponents(url: destination, resolvingAgainstBaseURL: false)!
                components.scheme = nil; components.host = nil; components.port = nil
                components.fragment = nil
                let path = components.string?.isEmpty == false ? components.string! : "/"
                let headers = lines.dropFirst().filter {
                    !$0.lowercased().hasPrefix("proxy-") && !$0.lowercased().hasPrefix("connection:")
                }
                initial = Data(("\(first[0]) \(path) HTTP/1.1\r\n" + headers.joined(separator: "\r\n")
                    + "\r\nConnection: close\r\n\r\n").utf8) + remainder
            }
        } catch {
            // Some network stacks tunnel the explicitly admitted HTTP fixture.
            if connect, let fixture = URL(string: "http://" + first[1]),
               policy.testOrigins.contains(BrowserAutomationPolicy.origin(fixture)) {
                open(fixture, connect: true, initial: remainder); return
            }
            close(); return
        }
        open(destination, connect: connect, initial: initial)
    }

    func open(_ destination: URL, connect: Bool, initial: Data) {
        let policy = self.policy
        Task.detached { [weak self] in
            let address: String
            do { address = try policy.pinnedAddress(for: destination) }
            catch { self?.queue.async { [weak self] in self?.close() }; return }
            self?.queue.async { [weak self] in
                guard let self, !closed, let port = NWEndpoint.Port(rawValue: UInt16(destination.port ?? 443)) else { return }
                let connection = NWConnection(host: NWEndpoint.Host(address), port: port, using: .tcp)
                upstream = connection
                var started = false
                connection.stateUpdateHandler = { [weak self] state in
                    guard let self, !closed else { return }
                    switch state {
                    case .ready:
                        guard !started else { return }; started = true; onForward()
                        let begin = { [weak self] in
                            guard let self, !closed else { return }
                            if !initial.isEmpty { connection.send(content: initial, completion: .contentProcessed { [weak self] error in
                                guard let self, error == nil else { self?.close(); return }; pump(client, into: connection)
                            }) } else { pump(client, into: connection) }
                            pump(connection, into: client)
                        }
                        if connect {
                            client.send(content: Data("HTTP/1.1 200 Connection Established\r\n\r\n".utf8),
                                completion: .contentProcessed { [weak self] error in
                                    if error != nil { self?.close() } else { begin() }
                                })
                        } else { begin() }
                    case .failed, .cancelled: close()
                    default: break
                    }
                }
                connection.start(queue: queue)
            }
        }
    }

    func pump(_ source: NWConnection, into target: NWConnection) {
        guard !closed else { return }
        source.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, done, error in
            guard let self, !closed else { return }
            guard error == nil else { close(); return }
            if let data, !data.isEmpty {
                transferred += data.count
                guard transferred <= 64 * 1024 * 1024 else { close(); return }
                target.send(content: data, completion: .contentProcessed { [weak self] error in
                    guard let self else { return }
                    if error != nil || done { close() } else { pump(source, into: target) }
                })
            } else if done { close() } else { pump(source, into: target) }
        }
    }
}
