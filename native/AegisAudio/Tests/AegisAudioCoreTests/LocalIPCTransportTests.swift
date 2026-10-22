import Darwin
import Foundation
import Testing
@testable import AegisAudioCore

private enum SyntheticIPCServerError: Error {
    case startupTimedOut
}

private final class SyntheticIPCServer {
    let path = "/private/tmp/jarvis-ipc-\(UUID().uuidString).sock"
    private var descriptor: Int32 = -1
    private let worker = DispatchGroup()

    init(startupDelayMicroseconds: useconds_t = 0,
         handler: @escaping @Sendable (Int32) -> Void) throws {
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
            closeListener()
            unlink(path)
            throw LocalIPCError.connectionFailed
        }
        let listener = descriptor
        let completion = worker
        let ready = DispatchSemaphore(value: 0)
        completion.enter()
        // Swift Testing can occupy the cooperative/global pool with synchronous
        // clients. Give the blocking peer its own thread, and do not consume the
        // client's deadline while that thread is still waiting to start.
        let thread = Thread {
            defer { completion.leave() }
            if startupDelayMicroseconds > 0 { usleep(startupDelayMicroseconds) }
            guard !Thread.current.isCancelled else { return }
            ready.signal()
            var peer: Int32
            repeat { peer = accept(listener, nil, nil) } while peer < 0 && errno == EINTR
            guard peer >= 0 else { return }
            defer { close(peer) }
            var one: Int32 = 1
            _ = setsockopt(peer, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout<Int32>.size))
            var timeout = timeval(tv_sec: 2, tv_usec: 0)
            _ = setsockopt(peer, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            _ = setsockopt(peer, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            handler(peer)
        }
        thread.name = "Jarvis synthetic IPC peer"
        thread.qualityOfService = .userInitiated
        thread.start()
        guard ready.wait(timeout: .now() + 10) == .success else {
            thread.cancel()
            shutdown(descriptor, SHUT_RDWR)
            _ = completion.wait(timeout: .now() + 3)
            closeListener()
            unlink(path)
            throw SyntheticIPCServerError.startupTimedOut
        }
    }

    deinit {
        closeListener()
        _ = worker.wait(timeout: .now() + 3)
        unlink(path)
    }

    private func closeListener() {
        guard descriptor >= 0 else { return }
        let listener = descriptor
        descriptor = -1
        shutdown(listener, SHUT_RDWR)
        close(listener)
    }
}

private func receiveSyntheticRequest(_ peer: Int32) {
    var byte: UInt8 = 0
    while true {
        let count = Darwin.read(peer, &byte, 1)
        if count == 1 {
            if byte == 0x0A { return }
        } else if count < 0, errno == EINTR {
            continue
        } else {
            return
        }
    }
}

private func sendSyntheticBytes(_ bytes: Data, to peer: Int32, pause: useconds_t = 0) {
    bytes.withUnsafeBytes { buffer in
        guard let base = buffer.baseAddress else { return }
        var offset = 0
        while offset < buffer.count {
            // Split the initial magic/header; use modest uneven fragments for
            // the body instead of tens of thousands of scheduler-sensitive writes.
            // The deadline regression keeps one byte every 30 ms, unchanged.
            let size = pause > 0 || offset < 16 ? 1 : 257
            let count = Darwin.write(peer, base.advanced(by: offset), min(size, buffer.count - offset))
            if count > 0 {
                offset += count
                if pause > 0 { usleep(pause) }
            } else if count < 0, errno == EINTR {
                continue
            } else {
                return
            }
        }
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
    // Reproduce a worker scheduled later than the client's unchanged 2 s budget.
    // Fixture readiness must precede the request; the product never retries it.
    let server = try SyntheticIPCServer(startupDelayMicroseconds: 2_200_000) { peer in
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
