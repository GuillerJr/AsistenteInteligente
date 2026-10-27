import Foundation

/// One explicit page endpoint. No target discovery, browser-wide attachment,
/// cookie API, arbitrary expression argument or retry is exposed to the model.
@MainActor
final class BrowserAutomationCDP {
    let targetID: String
    private let socket: URLSessionWebSocketTask
    private let session: URLSession
    private var sequence = 0
    private var closed = false
    private(set) var navigationObserved = false

    init(endpoint: URL, targetID: String) throws {
        guard ["127.0.0.1", "[::1]", "::1"].contains(endpoint.host ?? ""),
              endpoint.user == nil, endpoint.password == nil, endpoint.query == nil,
              endpoint.fragment == nil, let port = endpoint.port, (1...65535).contains(port),
              targetID.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression) != nil
        else { throw BrowserAutomationError.invalidArguments }
        var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)!
        if endpoint.scheme == "http", endpoint.path.isEmpty || endpoint.path == "/" {
            components.scheme = "ws"
            components.path = "/devtools/page/" + targetID
        } else {
            guard endpoint.scheme == "ws", endpoint.path == "/devtools/page/" + targetID else {
                throw BrowserAutomationError.invalidArguments
            }
        }
        guard let url = components.url else { throw BrowserAutomationError.invalidArguments }
        self.targetID = targetID
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 6
        config.timeoutIntervalForResource = 10
        session = URLSession(configuration: config, delegate: BrowserAutomationNoRedirect(), delegateQueue: nil)
        socket = session.webSocketTask(with: url)
        socket.maximumMessageSize = 262_144
        socket.resume()
    }

    func close() {
        guard !closed else { return }
        closed = true
        socket.cancel(with: .goingAway, reason: nil)
        session.invalidateAndCancel()
    }

    func call(_ method: String, params: [String: RPCValue] = [:]) async throws -> RPCValue {
        guard !closed, sequence < 1_000_000 else { throw BrowserAutomationError.unavailable }
        sequence += 1
        let id = sequence
        let request = RPCValue.object(["id": .number(Double(id)), "method": .string(method),
                                       "params": .object(params)])
        let data = try JSONEncoder().encode(request)
        guard data.count <= 65_536, let text = String(data: data, encoding: .utf8) else {
            throw BrowserAutomationError.oversized
        }
        do {
            return try await withThrowingTaskGroup(of: RPCValue.self) { group in
                group.addTask { [self] in
                    try await socket.send(.string(text))
                    for _ in 0..<128 {
                        let reply = try await socket.receive()
                        let bytes: Data
                        switch reply {
                        case .data(let value): bytes = value
                        case .string(let value): bytes = Data(value.utf8)
                        @unknown default: throw BrowserAutomationError.unavailable
                        }
                        guard bytes.count <= 262_144 else { throw BrowserAutomationError.oversized }
                        let value = try JSONDecoder().decode(RPCValue.self, from: bytes)
                        if value["method"].string == "Page.frameNavigated" ||
                            value["method"].string == "Runtime.executionContextsCleared" {
                            await self.recordNavigation()
                        }
                        if let responseID = value["id"].integer {
                            guard responseID == id, value["error"] == .null,
                                  value["result"].object != nil else { throw BrowserAutomationError.unavailable }
                            return value["result"]
                        }
                    }
                    throw BrowserAutomationError.unavailable
                }
                group.addTask { [self] in
                    try await Task.sleep(nanoseconds: 6_000_000_000)
                    await self.close()
                    throw BrowserAutomationError.unavailable
                }
                defer { group.cancelAll() }
                guard let result = try await group.next() else { throw BrowserAutomationError.unavailable }
                return result
            }
        } catch {
            // Cancelling the socket releases a receive that ignored task cancellation.
            close()
            throw error
        }
    }

    private func recordNavigation() { navigationObserved = true }
    func clearNavigationObservation() { navigationObserved = false }
}

final class BrowserAutomationNoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
