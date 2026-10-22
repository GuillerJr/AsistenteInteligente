import Darwin
import Foundation
import Testing
@testable import AegisAudioCore

private final class SyntheticIPCServer {
    let path = "/private/tmp/jarvis-ipc-\(UUID().uuidString).sock"
    private let descriptor: Int32
    private let worker = DispatchGroup()

    init(handler: @escaping @Sendable (Int32) -> Void) throws {
        descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw LocalIPCError.connectionFailed }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8CString)
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            bytes.withUnsafeBytes { destination.copyBytes(from: $0) }
        }
        let length = socklen_t(MemoryLayout<sa_family_t>.size + bytes.count)
        address.sun_len = UInt8(length)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(descriptor, $0, length)
            }
        }
        guard bound == 0, chmod(path, 0o600) == 0, listen(descriptor, 1) == 0 else {
            close(descriptor)
            unlink(path)
            throw LocalIPCError.connectionFailed
        }
        let listener = descriptor
        let completion = worker
        completion.enter()
        DispatchQueue.global().async {
            defer { completion.leave() }
            let peer = accept(listener, nil, nil)
            guard peer >= 0 else { return }
            defer { close(peer) }
            var one: Int32 = 1
            _ = setsockopt(peer, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
            var timeout = timeval(tv_sec: 2, tv_usec: 0)
            _ = setsockopt(peer, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            handler(peer)
        }
    }

    deinit {
        shutdown(descriptor, SHUT_RDWR)
        close(descriptor)
        _ = worker.wait(timeout: .now() + 3)
        unlink(path)
    }
}

private func receiveSyntheticRequest(_ peer: Int32) {
    var byte: UInt8 = 0
    while Darwin.read(peer, &byte, 1) == 1, byte != 0x0A {}
}

private func sendSyntheticBytes(_ bytes: Data, to peer: Int32, pause: useconds_t = 0) {
    for var byte in bytes {
        guard Darwin.write(peer, &byte, 1) == 1 else { return }
        if pause > 0 { usleep(pause) }
    }
}

@Test(arguments: [false, true])
func ipcTransportUsesOneDeadlineDespiteSlowResponseBytes(streamed: Bool) throws {
    // Each byte arrives before the socket's old per-read timeout. The total
    // response must nevertheless stop at the request deadline.
    let bytes = streamed
        ? Data([0xAE, 0x15, 0x01, 0, 0, 0, 1, 0] + Array(repeating: 0x20, count: 32))
        : Data("{\"payload\":\"a deliberately unfinished response".utf8)
    let server = try SyntheticIPCServer { peer in
        receiveSyntheticRequest(peer)
        sendSyntheticBytes(bytes, to: peer, pause: 30_000)
    }
    let client = try LocalIPCClient(
        socketPath: server.path, secret: Data(repeating: 0x11, count: 32), timeoutSeconds: 0.15
    )
    let start = ContinuousClock.now
    #expect(throws: LocalIPCError.connectionFailed) { try client.call(method: "health") }
    #expect(start.duration(to: .now) < .milliseconds(450))
}

@Test(arguments: [false, true])
func ipcTransportAcceptsFragmentedAuthenticatedResponse(streamed: Bool) throws {
    let secret = Data(repeating: 0x11, count: 32)
    let identifier = UUID()
    let nonce = String(repeating: "ab", count: 16)
    let now = Date()
    var envelope: [String: Any] = [
        "protocol_version": "1.0", "request_id": identifier.uuidString.lowercased(),
        "request_nonce": nonce, "timestamp": LocalIPCClient.formatTimestamp(now),
        "ok": true,
        "payload": ["status": "ready", "data": String(repeating: "x", count: streamed ? 40_000 : 2_000)],
        "error_code": NSNull(),
    ]
    envelope["auth_tag"] = try LocalIPCClient.authenticationTag(body: envelope, secret: secret)
    let encoded = try LocalIPCClient.canonicalJSON(envelope)
    let bytes: Data
    if streamed {
        bytes = try IPCStreamFraming.encodeFrames(
            encoded, secret: secret, legacyFrameBytes: 1, maxMessageBytes: 1_048_576
        ).reduce(into: Data()) { $0.append($1) }
    } else {
        bytes = encoded + Data([0x0A])
    }
    let server = try SyntheticIPCServer { peer in
        receiveSyntheticRequest(peer)
        sendSyntheticBytes(bytes, to: peer)
    }
    let client = try LocalIPCClient(
        socketPath: server.path, secret: secret, maxFrameBytes: 65_536,
        maxMessageBytes: 1_048_576, timeoutSeconds: 2, clockSkewSeconds: 30,
        now: { now }, requestID: { identifier }, nonce: { nonce }
    )
    let result = try client.call(method: "health")
    #expect(result.ok)
    #expect(result.payload["status"] as? String == "ready")
    #expect((result.payload["data"] as? String)?.count == (streamed ? 40_000 : 2_000))
}

@Test func ipcTransportHandlesPeerClosingBeforeReadingRequest() throws {
    let server = try SyntheticIPCServer { peer in
        shutdown(peer, SHUT_RDWR)
    }
    let client = try LocalIPCClient(
        socketPath: server.path, secret: Data(repeating: 0x11, count: 32), timeoutSeconds: 0.2
    )
    #expect(throws: (any Error).self) {
        try client.call(method: "health", payload: ["data": String(repeating: "x", count: 500_000)])
    }
}

@Test func ipcTransportBoundsWritesToPeerThatStopsReading() throws {
    let server = try SyntheticIPCServer { _ in usleep(750_000) }
    let client = try LocalIPCClient(
        socketPath: server.path, secret: Data(repeating: 0x11, count: 32), timeoutSeconds: 0.15
    )
    let start = ContinuousClock.now
    #expect(throws: LocalIPCError.connectionFailed) {
        try client.call(method: "health", payload: ["data": String(repeating: "x", count: 500_000)])
    }
    #expect(start.duration(to: .now) < .milliseconds(500))
}
