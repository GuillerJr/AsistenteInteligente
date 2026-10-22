import Darwin
import Foundation
import Security

/// Synchronous, single-flight inference with deadlines on every pipe operation.
/// Capture cannot enqueue unbounded audio or wait indefinitely for a stalled helper.
final class VADProcessClient: @unchecked Sendable {
    private let process: Process
    private let input: FileHandle
    private let output: FileHandle
    private let timeout: TimeInterval
    private var failed = false

    convenience init(bundle: Bundle, modelURL: URL? = nil) throws {
        let helper = try Self.resolveHelper(bundle: bundle)
        try self.init(executableURL: helper, modelURL: modelURL)
    }

    // Internal injection is used by protocol tests; production always resolves a signed bundle helper.
    init(executableURL: URL, modelURL: URL? = nil, timeout: TimeInterval = 0.25) throws {
        self.timeout = timeout
        process = Process()
        let requests = Pipe()
        let responses = Pipe()
        input = requests.fileHandleForWriting
        output = responses.fileHandleForReading
        process.executableURL = executableURL
        process.arguments = modelURL.map { ["--model", $0.path] } ?? []
        process.environment = ["PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"]
        process.standardInput = requests
        process.standardOutput = responses
        process.standardError = FileHandle.nullDevice
        _ = fcntl(input.fileDescriptor, F_SETNOSIGPIPE, 1)
        for descriptor in [input.fileDescriptor, output.fileDescriptor] {
            let flags = fcntl(descriptor, F_GETFL)
            guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) == 0 else {
                throw SileroVADError.inferenceFailed
            }
        }
        do {
            try process.run()
            // Close parent copies of the child's pipe ends so EOF is observable.
            try requests.fileHandleForReading.close()
            try responses.fileHandleForWriting.close()
            let hello = try readExactly(4, deadline: Self.deadline(after: 5))
            guard hello == Data("VAD1".utf8) else { throw SileroVADError.invalidModel }
        } catch {
            stop()
            throw SileroVADError.invalidModel
        }
    }

    deinit { stop() }

    func reset() {
        do { _ = try exchange(samples: []) } catch { stop() }
    }

    func infer(chunk: [Float]) throws -> Float {
        guard chunk.count == 512 else { throw SileroVADError.invalidInput }
        return try exchange(samples: chunk)
    }

    private func exchange(samples: [Float]) throws -> Float {
        guard !failed else { throw SileroVADError.inferenceFailed }
        do {
            var count = UInt32(samples.count).littleEndian
            var payload = withUnsafeBytes(of: &count) { Data($0) }
            for sample in samples {
                var bits = sample.bitPattern.littleEndian
                withUnsafeBytes(of: &bits) { payload.append(contentsOf: $0) }
            }
            let deadline = Self.deadline(after: timeout)
            try writeAll(payload, deadline: deadline)
            let result = try readExactly(4, deadline: deadline)
            let value = result.withUnsafeBytes {
                Float(bitPattern: UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self)))
            }
            guard value.isFinite, (0...1).contains(value), !samples.isEmpty || value == 0 else {
                throw SileroVADError.inferenceFailed
            }
            return value
        } catch {
            stop()
            throw SileroVADError.inferenceFailed
        }
    }

    private static func deadline(after seconds: TimeInterval) -> UInt64 {
        DispatchTime.now().uptimeNanoseconds + UInt64(max(seconds, 0) * 1_000_000_000)
    }

    private func wait(_ descriptor: Int32, events: Int16, deadline: UInt64) throws {
        while true {
            let now = DispatchTime.now().uptimeNanoseconds
            guard now < deadline else { throw SileroVADError.inferenceFailed }
            let remaining = Int32(min((deadline - now) / 1_000_000 + 1, UInt64(Int32.max)))
            var fd = pollfd(fd: descriptor, events: events, revents: 0)
            let result = poll(&fd, 1, remaining)
            if result < 0, errno == EINTR { continue }
            guard result > 0, fd.revents & events != 0 else {
                throw SileroVADError.inferenceFailed
            }
            return
        }
    }

    private func readExactly(_ count: Int, deadline: UInt64) throws -> Data {
        var result = Data(count: count)
        var offset = 0
        while offset < count {
            try wait(output.fileDescriptor, events: Int16(POLLIN), deadline: deadline)
            let n = result.withUnsafeMutableBytes {
                Darwin.read(output.fileDescriptor, $0.baseAddress!.advanced(by: offset), count - offset)
            }
            if n < 0, errno == EINTR || errno == EAGAIN { continue }
            guard n > 0 else { throw SileroVADError.inferenceFailed }
            offset += n
        }
        return result
    }

    private func writeAll(_ data: Data, deadline: UInt64) throws {
        var offset = 0
        while offset < data.count {
            try wait(input.fileDescriptor, events: Int16(POLLOUT), deadline: deadline)
            let n = data.withUnsafeBytes {
                Darwin.write(input.fileDescriptor, $0.baseAddress!.advanced(by: offset), data.count - offset)
            }
            if n < 0, errno == EINTR || errno == EAGAIN { continue }
            guard n > 0 else { throw SileroVADError.inferenceFailed }
            offset += n
        }
    }

    private func stop() {
        guard !failed else { return }
        failed = true
        try? input.close()
        try? output.close()
        // Only kill this owned child. It has no persisted state to flush.
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }

    static func resolveHelper(bundle: Bundle) throws -> URL {
        let helper = bundle.bundleURL.appendingPathComponent("Contents/Helpers/jarvis-vad-helper")
        if bundle.bundleURL.pathExtension == "app" {
            try validateHelper(helper, bundleURL: bundle.bundleURL)
            return helper
        }
        #if DEBUG
        // SwiftPM tests run without an app bundle. Never enable this path in release builds.
        let roots = [bundle.bundleURL] + Bundle.allBundles
            .filter { $0.bundleURL.pathExtension == "xctest" }.map(\.bundleURL)
        for root in roots {
            var directory = root
            for _ in 0..<6 {
                let candidate = directory.appendingPathComponent("jarvis-vad-helper")
                if FileManager.default.isExecutableFile(atPath: candidate.path) { return candidate }
                directory.deleteLastPathComponent()
            }
        }
        #endif
        throw SileroVADError.invalidModel
    }

    static func validateHelper(_ helper: URL, bundleURL: URL) throws {
        let expected = bundleURL.standardizedFileURL.appendingPathComponent("Contents/Helpers/jarvis-vad-helper")
        let attributes = try FileManager.default.attributesOfItem(atPath: helper.path)
        guard helper.standardizedFileURL == expected,
              helper.resolvingSymlinksInPath().path == expected.path,
              attributes[.type] as? FileAttributeType == .typeRegular,
              let permissions = attributes[.posixPermissions] as? NSNumber,
              permissions.intValue & 0o022 == 0,
              FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw SileroVADError.invalidModel
        }
        var running: SecCode?
        var runningStatic: SecStaticCode?
        var helperStatic: SecStaticCode?
        guard SecCodeCopySelf([], &running) == errSecSuccess,
              let running,
              SecCodeCopyStaticCode(running, [], &runningStatic) == errSecSuccess,
              let runningStatic,
              SecStaticCodeCreateWithPath(helper as CFURL, [], &helperStatic) == errSecSuccess,
              let helperStatic,
              SecStaticCodeCheckValidity(helperStatic, SecCSFlags(rawValue: kSecCSStrictValidate), nil) == errSecSuccess,
              matchesSigningIdentity(
                ownCertificate: signingIdentity(runningStatic).certificate,
                helperCertificate: signingIdentity(helperStatic).certificate,
                helperIdentifier: signingIdentity(helperStatic).identifier
              ) else {
            throw SileroVADError.invalidModel
        }
    }

    static func matchesSigningIdentity(
        ownCertificate: Data?, helperCertificate: Data?, helperIdentifier: String?
    ) -> Bool {
        guard let ownCertificate, !ownCertificate.isEmpty,
              let helperCertificate, !helperCertificate.isEmpty,
              helperIdentifier == "jarvis-vad-helper" else { return false }
        return ownCertificate == helperCertificate
    }

    private static func signingIdentity(_ code: SecStaticCode) -> (certificate: Data?, identifier: String?) {
        var information: CFDictionary?
        guard SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &information) == errSecSuccess,
              let dictionary = information as? [String: Any] else { return (nil, nil) }
        let identifier = dictionary[kSecCodeInfoIdentifier as String] as? String
        guard
              let certificates = dictionary[kSecCodeInfoCertificates as String] as? [SecCertificate],
              let leaf = certificates.first else { return (nil, identifier) }
        return (SecCertificateCopyData(leaf) as Data, identifier)
    }
}
