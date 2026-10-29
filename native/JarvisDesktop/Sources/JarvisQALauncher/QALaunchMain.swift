import AppKit
import Foundation
import QALaunchSupport

/// QA-only Launch Services entry point. It never types, inspects UI, or starts a shell wrapper.
@main struct QALaunchMain {
    @MainActor static func main() async {
        do {
            let arguments = Array(CommandLine.arguments.dropFirst())
            guard arguments.count == 4, arguments[0] == "--request", arguments[2] == "--receipt" else { throw QALaunchIssue.arguments }
            let coordinator = QALaunchCoordinator(driver: QALaunchWorkspaceDriver())
            _ = try await coordinator.launch(requestURL: QALaunchAdmission.fileURL(from: arguments[1], isDirectory: false),
                                             receiptURL: QALaunchAdmission.fileURL(from: arguments[3], isDirectory: false))
            print("Lanzamiento confirmado; consulta el recibo sintético.")
        } catch let failure as QALaunchUncertainFailure {
            // The ordinary receipt could not retain disk custody. Preserve only bounded,
            // explicitly uncertain returned-app metadata; never emit arguments/environment.
            let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
            if var data = try? encoder.encode(failure.receipt) {
                data.append(0x0a); FileHandle.standardError.write(data)
            }
            FileHandle.standardError.write(Data((failure.issue.errorDescription! + "\n").utf8))
            exit(1)
        } catch {
            // Never print request contents, arguments, environment, or an OS error's userInfo.
            let issue = (error as? QALaunchIssue) ?? .launchServices
            FileHandle.standardError.write(Data((issue.errorDescription! + "\n").utf8))
            exit(1)
        }
    }
}

@MainActor final class QALaunchWorkspaceDriver: QALaunchDriver {
    func open(_ admission: QALaunchAdmission) async throws -> QALaunchApplication {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.arguments = admission.request.arguments
        configuration.environment = admission.request.environment
        configuration.createsNewApplicationInstance = true
        configuration.allowsRunningApplicationSubstitution = false
        configuration.addsToRecentItems = false
        configuration.activates = true
        configuration.promptsUserIfNeeded = false
        return try await withCheckedThrowingContinuation { continuation in
            let completion = QALaunchCompletion(continuation)
            let deadline = Task { @MainActor in
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                completion.finish(.failure(QALaunchIssue.timeout))
            }
            NSWorkspace.shared.openApplication(at: admission.appURL, configuration: configuration) { app, error in
                // Read only the returned application. Do not enumerate apps or process arguments.
                let application: QALaunchApplication?
                if error == nil, let app, let bundleID = app.bundleIdentifier, let bundleURL = app.bundleURL,
                   let executableURL = app.executableURL, let launchDate = app.launchDate {
                    application = QALaunchApplication(pid: app.processIdentifier, bundleID: bundleID,
                        bundleURL: bundleURL, executableURL: executableURL, launchDate: launchDate)
                } else { application = nil }
                Task { @MainActor in
                    deadline.cancel()
                    if let application { completion.finish(.success(application)) }
                    else { completion.finish(.failure(QALaunchIssue.launchServices)) }
                }
            }
        }
    }
}

@MainActor private final class QALaunchCompletion {
    private var continuation: CheckedContinuation<QALaunchApplication, any Error>?
    init(_ continuation: CheckedContinuation<QALaunchApplication, any Error>) { self.continuation = continuation }
    func finish(_ result: Result<QALaunchApplication, any Error>) {
        let pending = continuation; continuation = nil
        pending?.resume(with: result)
    }
}
