import XCTest
@testable import AegisMenuBar

final class CommandPaletteSelectionTests: XCTestCase {
    private func command(_ id: String, enabled: Bool = true, title: String? = nil,
                         keywords: [String] = []) -> JarvisPaletteCommand {
        JarvisPaletteCommand(id: id, title: title ?? id, symbol: "circle",
                             keywords: keywords, enabled: enabled, run: {})
    }

    func testInitialSelectionSkipsUnavailableCommands() {
        let commands = [command("blocked", enabled: false), command("first"), command("second")]
        XCTAssertEqual(JarvisPaletteSelection.resolved(nil, in: commands), "first")
        XCTAssertEqual(JarvisPaletteSelection.resolved("blocked", in: commands), "first")
        XCTAssertEqual(JarvisPaletteSelection.resolved("removed", in: commands), "first")
    }

    func testArrowsSkipUnavailableCommandsAndWrapBothWays() {
        let commands = [command("start", enabled: false), command("one"),
                        command("middle", enabled: false), command("two"),
                        command("end", enabled: false)]
        XCTAssertEqual(JarvisPaletteSelection.moved("one", in: commands, forward: true), "two")
        XCTAssertEqual(JarvisPaletteSelection.moved("two", in: commands, forward: true), "one")
        XCTAssertEqual(JarvisPaletteSelection.moved("two", in: commands, forward: false), "one")
        XCTAssertEqual(JarvisPaletteSelection.moved("one", in: commands, forward: false), "two")
        XCTAssertEqual(JarvisPaletteSelection.moved(nil, in: commands, forward: true), "two")
    }

    func testEmptyOrEntirelyUnavailablePaletteHasNoSelectionOrSideEffects() {
        var executed = 0
        let unavailable = (0..<20).map { index in
            JarvisPaletteCommand(id: "blocked-\(index)", title: "Bloqueado", symbol: "circle",
                                 enabled: false, run: { executed += 1 })
        }
        for commands in [[], unavailable] {
            XCTAssertNil(JarvisPaletteSelection.resolved(nil, in: commands))
            XCTAssertNil(JarvisPaletteSelection.resolved("blocked-0", in: commands))
            XCTAssertNil(JarvisPaletteSelection.moved(nil, in: commands, forward: true))
            XCTAssertNil(JarvisPaletteSelection.moved("blocked-0", in: commands, forward: false))
        }
        XCTAssertEqual(executed, 0)
    }

    func testSingleAvailableCommandRemainsSelectedInEitherDirection() {
        let commands = [command("before", enabled: false), command("only"),
                        command("after", enabled: false)]
        XCTAssertEqual(JarvisPaletteSelection.moved("only", in: commands, forward: true), "only")
        XCTAssertEqual(JarvisPaletteSelection.moved("only", in: commands, forward: false), "only")
    }

    func testSelectionFollowsIdentityAfterReorderingAndRecoversIfDisabled() {
        let initial = [command("first"), command("selected"), command("last")]
        XCTAssertEqual(JarvisPaletteSelection.resolved("selected", in: initial), "selected")
        let reordered = [initial[2], initial[0], initial[1]]
        XCTAssertEqual(JarvisPaletteSelection.resolved("selected", in: reordered), "selected")
        let changed = [command("selected", enabled: false), command("last"), command("first")]
        XCTAssertEqual(JarvisPaletteSelection.resolved("selected", in: changed), "last")
    }

    func testSearchSelectsFirstAvailableMatchWithoutChangingMatchingRules() {
        let commands = [command("blocked", enabled: false, title: "Abrir acción"),
                        command("other", title: "Buscar chats"),
                        command("match", title: "Abrir ajustes", keywords: ["acción", "configuración"])]
        let matches = commands.filter { $0.matches(" ABRIR accion ") }
        XCTAssertEqual(matches.map(\.id), ["blocked", "match"])
        XCTAssertEqual(JarvisPaletteSelection.resolved(nil, in: matches), "match")
        XCTAssertNil(JarvisPaletteSelection.resolved(nil, in: commands.filter { $0.matches("inexistente") }))
    }
}
