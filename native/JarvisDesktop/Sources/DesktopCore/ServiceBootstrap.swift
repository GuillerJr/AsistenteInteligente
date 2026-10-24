import Foundation

/// One launch, repeated read-only readiness checks. No mutation is retried here.
@MainActor
public enum ServiceBootstrap {
    public static func waitForHealth(stateDirectory: String, timeout: TimeInterval = 30,
                                     isRunning: () -> Bool) async throws {
        precondition(timeout.isFinite && timeout > 0 && timeout <= 60)
        let client = LocalRPCClient(stateDirectory: stateDirectory, timeout: min(1, timeout))
        let deadline = ProcessInfo.processInfo.systemUptime + timeout
        var lastError: Error = RPCFailure.unavailable
        do {
            while ProcessInfo.processInfo.systemUptime < deadline {
                try Task.checkCancellation()
                guard isRunning() else { throw RPCFailure.serviceExited }
                do {
                    let health = try await client.call("health")
                    guard health["service"].string == "jarvis", health["version"].integer == 1 else {
                        throw RPCFailure.invalidResponse
                    }
                    await client.close()
                    return
                } catch {
                    lastError = error
                }
                try await Task.sleep(for: .milliseconds(100))
            }
            throw lastError
        } catch {
            await client.close()
            throw error
        }
    }
}
