import AegisAudioCore
import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import ScreenCaptureKit

enum ScreenCaptureServiceError: Error {
    case accessibilityPermissionRequired
    case applicationUnavailable
    case captureFailed
    case permissionRequired
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
        guard isAuthorized else {
            throw ScreenCaptureServiceError.permissionRequired
        }
        guard AXIsProcessTrusted() else {
            throw ScreenCaptureServiceError.accessibilityPermissionRequired
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
        do {
            let image = try await SCScreenshotManager.captureImage(
                contentFilter: filter,
                configuration: configuration
            )
            return try LocalImageEncoder.encodeWindowCapture(image)
        } catch let error as LocalImageError {
            throw error
        } catch {
            throw ScreenCaptureServiceError.captureFailed
        }
    }

    private static func focusedWindowFrame(processIdentifier: pid_t) throws -> CGRect {
        let application = AXUIElementCreateApplication(processIdentifier)
        var rawWindow: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                application,
                kAXFocusedWindowAttribute as CFString,
                &rawWindow
            ) == .success,
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
