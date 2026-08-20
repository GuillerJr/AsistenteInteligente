import AegisAudioCore
import CoreGraphics
import Darwin
import ScreenCaptureKit

enum ScreenCaptureServiceError: Error {
    case permissionRequired
    case displayUnavailable
    case captureFailed
}

enum ScreenCaptureService {
    static var isAuthorized: Bool {
        CGPreflightScreenCaptureAccess()
    }

    @MainActor
    static func requestAccess() -> Bool {
        CGRequestScreenCaptureAccess()
    }

    static func captureMainDisplay() async throws -> LocalImageAttachment {
        guard isAuthorized else {
            throw ScreenCaptureServiceError.permissionRequired
        }
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )
        guard
            let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() })
                ?? content.displays.first,
            display.width > 0,
            display.height > 0
        else {
            throw ScreenCaptureServiceError.displayUnavailable
        }
        let ownApplications = content.applications.filter {
            $0.processID == getpid()
        }
        guard !ownApplications.isEmpty else {
            throw ScreenCaptureServiceError.captureFailed
        }
        let filter = SCContentFilter(
            display: display,
            excludingApplications: ownApplications,
            exceptingWindows: []
        )
        if #available(macOS 14.2, *) {
            filter.includeMenuBar = false
        }
        let configuration = SCStreamConfiguration()
        if display.width >= display.height {
            configuration.width = 1_024
            configuration.height = max(1, 1_024 * display.height / display.width)
        } else {
            configuration.width = max(1, 1_024 * display.width / display.height)
            configuration.height = 1_024
        }
        configuration.scalesToFit = true
        configuration.preservesAspectRatio = true
        configuration.showsCursor = false
        configuration.capturesAudio = false
        do {
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            return try LocalImageEncoder.encodeImage(image)
        } catch let error as LocalImageError {
            throw error
        } catch {
            throw ScreenCaptureServiceError.captureFailed
        }
    }
}
