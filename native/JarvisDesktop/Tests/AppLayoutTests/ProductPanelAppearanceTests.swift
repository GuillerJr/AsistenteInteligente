import AppKit
import DesktopCore
import SwiftUI
import XCTest
@testable import JarvisApp

final class ProductPanelAppearanceTests: XCTestCase {
    @MainActor func testDarkPanelHasOpaqueDarkSurfaceAndLightTextInsideLightApp() throws {
        try assertRenderedPanel(appearance: "dark", appScheme: .light, darkPanel: true)
    }

    @MainActor func testLightPanelHasOpaqueLightSurfaceAndDarkTextInsideDarkApp() throws {
        try assertRenderedPanel(appearance: "light", appScheme: .dark, darkPanel: false)
    }

    @MainActor func testSystemPanelRendersWithEachInheritedAppAppearance() throws {
        try assertRenderedPanel(appearance: "system", appScheme: .light, darkPanel: false)
        try assertRenderedPanel(appearance: "system", appScheme: .dark, darkPanel: true)
    }

    @MainActor private func assertRenderedPanel(appearance: String, appScheme: ColorScheme,
                                              darkPanel: Bool,
                                              file: StaticString = #filePath, line: UInt = #line) throws {
        _ = NSApplication.shared
        let scope = SessionScope(sessionID: String(repeating: "a", count: 32), temporary: true)
        let binding = try ProductInteractionBinding(.object([
            "session_id": .string(scope.sessionID), "temporary": .bool(true),
            "run_id": .string("host"), "agent_id": .string("jarvis"),
            "workspace_id": .string("synthetic_workspace"), "generation": .number(0)
        ]), expectedScope: scope)
        let definition = ProductCanvasDefinition(title: "Panel legible", appearance: appearance,
            widgets: [ProductCanvasWidget(id: "text", type: "text", title: "Texto principal",
                text: "Contenido visible del panel de prueba.")])
        let interaction = try ProductInteractionRecord(.object([
            "id": .string("appearance_panel"), "kind": .string("panel"),
            "revision": .number(1), "status": .string("active"),
            "scope_run_id": .string("host"), "expires_ns": .number(0),
            "payload": definition.submission
        ]), binding: binding)
        let store = ProductInteractionStore()
        defer { store.close() }
        let panel = ProductInteractionWidgets(store: store, interaction: interaction)
            .frame(width: 340)
        // First render without an app backing: a foreground-only theme switch
        // cannot pass by borrowing the surrounding window's opaque pixels.
        let isolated = try bitmap(panel.environment(\.colorScheme, appScheme))
        XCTAssertGreaterThan(isolated.pixelsHigh, 60, file: file, line: line)
        guard isolated.pixelsHigh > 28, isolated.pixelsWide > 28 else {
            XCTFail("The rendered panel must have a measurable content area", file: file, line: line)
            return
        }
        let surface = try pixel(isolated, x: isolated.pixelsWide / 2, y: 6)
        XCTAssertGreaterThan(surface.alphaComponent, 0.98,
                             "The panel itself must paint an opaque surface", file: file, line: line)
        if darkPanel {
            XCTAssertLessThan(luminance(surface), 0.25, file: file, line: line)
        } else {
            XCTAssertGreaterThan(luminance(surface), 0.75, file: file, line: line)
        }
        var visibleTextPixels = 0
        for y in 14..<(isolated.pixelsHigh - 14) {
            for x in 14..<(isolated.pixelsWide - 14) {
                let color = try pixel(isolated, x: x, y: y)
                guard color.alphaComponent > 0.98 else { continue }
                let value = luminance(color)
                if darkPanel ? value > 0.70 : value < 0.25 { visibleTextPixels += 1 }
            }
        }
        XCTAssertGreaterThan(visibleTextPixels, 40,
                             "Rendered glyphs must contrast with the panel surface", file: file, line: line)

        let appBackground: Color = appScheme == .dark ? .black : .white
        let composite = try bitmap(ZStack(alignment: .topLeading) {
            appBackground
            panel
        }.frame(width: 380, height: CGFloat(isolated.pixelsHigh + 30))
            .environment(\.colorScheme, appScheme))
        let panelOnApp = try pixel(composite, x: 170, y: 6)
        XCTAssertEqual(luminance(panelOnApp), luminance(surface), accuracy: 0.02,
                       "The opposite app theme must not bleed through the panel", file: file, line: line)
        let outside = try pixel(composite, x: composite.pixelsWide - 6,
                                y: composite.pixelsHigh / 2)
        XCTAssertEqual(luminance(outside), appScheme == .dark ? 0 : 1, accuracy: 0.02,
                       "The local panel surface must not repaint its surroundings", file: file, line: line)
    }

    @MainActor private func bitmap<Content: View>(_ content: Content) throws -> NSBitmapImageRep {
        let renderer = ImageRenderer(content: content)
        renderer.scale = 1
        return NSBitmapImageRep(cgImage: try XCTUnwrap(renderer.cgImage))
    }

    private func pixel(_ bitmap: NSBitmapImageRep, x: Int, y: Int) throws -> NSColor {
        try XCTUnwrap(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.sRGB))
    }

    private func luminance(_ color: NSColor) -> CGFloat {
        0.2126 * color.redComponent + 0.7152 * color.greenComponent + 0.0722 * color.blueComponent
    }
}
