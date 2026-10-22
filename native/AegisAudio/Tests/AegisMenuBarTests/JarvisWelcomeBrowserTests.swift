import Foundation
import XCTest
@testable import AegisMenuBar

@MainActor
final class JarvisWelcomeBrowserTests: XCTestCase {
    func testWelcomeFirstLaunchMissingCredentialAndSkippedConfiguredLaunch() async {
        XCTAssertTrue(JarvisWelcomeState.shouldPresent(completedVersion: 0, credential: nil))
        XCTAssertTrue(JarvisWelcomeState.shouldPresent(completedVersion: 0, credential: "configured"))
        XCTAssertTrue(JarvisWelcomeState.shouldPresent(completedVersion: 1, credential: "missing"))
        XCTAssertFalse(JarvisWelcomeState.shouldPresent(completedVersion: 1, credential: "configured"))
        XCTAssertFalse(JarvisWelcomeState.shouldPresent(completedVersion: 1, credential: nil),
                       "Una desconexión transitoria no vuelve a abrir la bienvenida completada")
    }

    func testBrowserAddressAndSearchPreserveUserQueryWithoutExecutingSchemes() async throws {
        XCTAssertEqual(JarvisBrowserStore.destination(for: " https://example.com/a?q=1 ")?.absoluteString,
                       "https://example.com/a?q=1")
        XCTAssertEqual(JarvisBrowserStore.destination(for: "example.com/manual")?.absoluteString,
                       "https://example.com/manual")
        XCTAssertNil(JarvisBrowserStore.destination(for: " \n\t"))
        for query in ["receta de café & té", "hola\nmundo.com", "javascript:alert('a.b')", "file:///tmp/a.txt", "https://"] {
            let url = try XCTUnwrap(JarvisBrowserStore.destination(for: query))
            XCTAssertEqual(url.host, "duckduckgo.com")
            XCTAssertEqual(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, query)
        }
    }

    func testExternalPageNavigationCannotLaunchAnAppWithoutNativeConfirmation() async throws {
        let browser = JarvisBrowserStore(persistent: false)
        let shortcut = try XCTUnwrap(URL(string: "shortcuts://run-shortcut?name=synthetic"))
        browser.reviewExternalNavigation(shortcut, activatedLink: false)
        XCTAssertNil(browser.externalURL, "Una redirección no puede abrir una app")
        XCTAssertNotNil(browser.failure)
        browser.reviewExternalNavigation(shortcut, activatedLink: true)
        XCTAssertEqual(browser.externalURL, shortcut, "Un enlace solo prepara la revisión; no lo abre")
        browser.externalURL = nil // The owner cancels the review.
        browser.reviewExternalNavigation(URL(fileURLWithPath: "/tmp/synthetic"), activatedLink: true)
        XCTAssertNil(browser.externalURL)
    }
}
