import AegisAudioCore
import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import ScreenCaptureKit

enum ScreenCaptureServiceError: Error {
    case applicationUnavailable
    case captureFailed
    case tccPermissionDenied(TCCPrivacyPermission)
    case unsafeTarget
    case windowUnavailable
}

enum ScreenCaptureService {
    static var isAuthorized: Bool {
        CGPreflightScreenCaptureAccess()
    }

    @MainActor
    static func requestAccess() -> Bool {
        CGRequestScreenCaptureAccess()
    }

    static func captureAuthorizedFrontmostWindow(
        allowedBundleIdentifier: String
    ) async throws -> LocalImageAttachment {
        do {
            return try await performAuthorizedFrontmostWindowCapture(
                allowedBundleIdentifier: allowedBundleIdentifier
            )
        } catch let error as ScreenCaptureServiceError {
            if case .tccPermissionDenied = error {
                throw error
            }
            if let permission = revokedPermission(for: nil) {
                throw ScreenCaptureServiceError.tccPermissionDenied(permission)
            }
            throw error
        } catch let error as LocalImageError {
            if let permission = revokedPermission(for: nil) {
                throw ScreenCaptureServiceError.tccPermissionDenied(permission)
            }
            throw error
        } catch {
            if let permission = revokedPermission(for: error) {
                throw ScreenCaptureServiceError.tccPermissionDenied(permission)
            }
            throw ScreenCaptureServiceError.captureFailed
        }
    }

    private static func performAuthorizedFrontmostWindowCapture(
        allowedBundleIdentifier: String
    ) async throws -> LocalImageAttachment {
        guard isAuthorized else {
            throw ScreenCaptureServiceError.tccPermissionDenied(.screenRecording)
        }
        guard AXIsProcessTrusted() else {
            throw ScreenCaptureServiceError.tccPermissionDenied(.accessibility)
        }
        guard
            let application = NSWorkspace.shared.frontmostApplication,
            application.processIdentifier != getpid(),
            let bundleIdentifier = application.bundleIdentifier,
            !ComputerControlSafety.isRestrictedBundleIdentifier(bundleIdentifier),
            !allowedBundleIdentifier.isEmpty,
            allowedBundleIdentifier.utf8.count <= 255,
            allowedBundleIdentifier == bundleIdentifier
        else {
            throw ScreenCaptureServiceError.unsafeTarget
        }
        let accessibilityFrame = try focusedWindowFrame(
            processIdentifier: application.processIdentifier
        )
        let content = try await SCShareableContent.excludingDesktopWindows(
            true,
            onScreenWindowsOnly: true
        )
        let matchingWindows: [(window: SCWindow, geometry: WindowGeometryCandidate)] =
            content.windows.enumerated().compactMap { index, window in
                guard
                    window.isOnScreen,
                    window.owningApplication?.processID == application.processIdentifier,
                    window.owningApplication?.bundleIdentifier == bundleIdentifier
                else {
                    return nil
                }
                return (
                    window,
                    WindowGeometryCandidate(index: index, frame: window.frame)
                )
            }
        guard
            let selectedIndex = WindowCapturePlan.select(
                accessibilityFrame: accessibilityFrame,
                candidates: matchingWindows.map(\.geometry)
            ),
            let window = matchingWindows.first(where: {
                $0.geometry.index == selectedIndex
            })?.window,
            let display = content.displays.max(by: {
                intersectionArea(CGDisplayBounds($0.displayID), window.frame)
                    < intersectionArea(CGDisplayBounds($1.displayID), window.frame)
            }),
            let dimensions = WindowCapturePlan.pixelDimensions(
                frame: window.frame,
                displayBounds: CGDisplayBounds(display.displayID),
                displayPixelWidth: display.width,
                displayPixelHeight: display.height
            )
        else {
            throw ScreenCaptureServiceError.windowUnavailable
        }

        let filter = SCContentFilter(desktopIndependentWindow: window)
        let configuration = SCStreamConfiguration()
        configuration.width = dimensions.width
        configuration.height = dimensions.height
        configuration.scalesToFit = false
        configuration.preservesAspectRatio = true
        configuration.showsCursor = false
        configuration.capturesAudio = false
        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
        if let permission = revokedPermission(for: nil) {
            throw ScreenCaptureServiceError.tccPermissionDenied(permission)
        }
        return try LocalImageEncoder.encodeWindowCapture(image)
    }

    private static func focusedWindowFrame(processIdentifier: pid_t) throws -> CGRect {
        let application = AXUIElementCreateApplication(processIdentifier)
        var rawWindow: CFTypeRef?
        let status = AXUIElementCopyAttributeValue(
            application,
            kAXFocusedWindowAttribute as CFString,
            &rawWindow
        )
        if status == .apiDisabled || !AXIsProcessTrusted() {
            throw ScreenCaptureServiceError.tccPermissionDenied(.accessibility)
        }
        guard
            status == .success,
            let rawWindow,
            CFGetTypeID(rawWindow) == AXUIElementGetTypeID()
        else {
            throw ScreenCaptureServiceError.windowUnavailable
        }
        let window = unsafeDowncast(rawWindow, to: AXUIElement.self)
        guard
            let position = pointAttribute(window, kAXPositionAttribute as CFString),
            let size = sizeAttribute(window, kAXSizeAttribute as CFString),
            size.width >= 1,
            size.height >= 1
        else {
            throw ScreenCaptureServiceError.windowUnavailable
        }
        return CGRect(origin: position, size: size)
    }

    private static func revokedPermission(for error: Error?) -> TCCPrivacyPermission? {
        if !isAuthorized {
            return .screenRecording
        }
        if !AXIsProcessTrusted() {
            return .accessibility
        }
        guard let error else { return nil }
        let captureError = error as NSError
        if isScreenCapturePermissionError(captureError) {
            return .screenRecording
        }
        if let underlying = captureError.userInfo[NSUnderlyingErrorKey] as? Error,
           isScreenCapturePermissionError(underlying as NSError)
        {
            return .screenRecording
        }
        return nil
    }

    private static func isScreenCapturePermissionError(_ error: NSError) -> Bool {
        error.domain == SCStreamErrorDomain
            && (error.code == -3_801 || error.code == -3_803)
    }

    private static func pointAttribute(_ element: AXUIElement, _ name: CFString) -> CGPoint? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let value,
            CFGetTypeID(value) == AXValueGetTypeID()
        else {
            return nil
        }
        var point = CGPoint.zero
        return AXValueGetValue(unsafeDowncast(value, to: AXValue.self), .cgPoint, &point)
            ? point
            : nil
    }

    private static func sizeAttribute(_ element: AXUIElement, _ name: CFString) -> CGSize? {
        var value: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(element, name, &value) == .success,
            let value,
            CFGetTypeID(value) == AXValueGetTypeID()
        else {
            return nil
        }
        var size = CGSize.zero
        return AXValueGetValue(unsafeDowncast(value, to: AXValue.self), .cgSize, &size)
            ? size
            : nil
    }

    private static func intersectionArea(_ left: CGRect, _ right: CGRect) -> CGFloat {
        let intersection = left.intersection(right)
        return intersection.isNull ? 0 : intersection.width * intersection.height
    }
}
