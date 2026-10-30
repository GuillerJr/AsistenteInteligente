import Darwin
import Foundation

/// A lifetime pipe carries no data. Its writer belongs only to the desktop;
/// desktop exit/crash closes it and lets the Python service observe EOF. It is
/// not permission, a heartbeat, or authority to signal any parent PID.
final class ServiceParentWatch {
    let pipe = Pipe()
    private var writerClosed = false

    init() throws {
        let descriptor = pipe.fileHandleForWriting.fileDescriptor
        let flags = fcntl(descriptor, F_GETFD)
        guard flags >= 0, fcntl(descriptor, F_SETFD, flags | FD_CLOEXEC) == 0 else {
            try? pipe.fileHandleForReading.close(); try? pipe.fileHandleForWriting.close()
            throw RPCFailure.configuration
        }
    }
    func didLaunch() { try? pipe.fileHandleForReading.close() }
    func close() {
        guard !writerClosed else { return }
        writerClosed = true
        try? pipe.fileHandleForWriting.close()
    }
    deinit {
        try? pipe.fileHandleForReading.close()
        close()
    }
}
