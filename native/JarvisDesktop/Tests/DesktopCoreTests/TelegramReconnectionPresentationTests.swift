import XCTest
@testable import DesktopCore

final class TelegramReconnectionPresentationTests: XCTestCase {
    func testBoundedReaderReconnectHasAnExplicitSpanishStatus() {
        let expected = "Reconectando la lectura de Telegram… La espera es limitada; el estado se actualizará al recuperar la conexión."
        XCTAssertEqual(TelegramPresentation.channelError("telegram_reconnecting"), expected)
        XCTAssertEqual(TelegramPresentation.errorMessage(RPCFailure.remote(-32036, "telegram_reconnecting")), expected)
    }
    func testAnEchoedRemoteBodyCannotImpersonateTheReconnectStatus() {
        let code = "telegram_reconnecting\nPRIVATE RESPONSE"
        let message = TelegramPresentation.channelError(code)
        XCTAssertEqual(message, "No se confirmó la operación de Telegram. Actualiza el estado antes de continuar.")
        XCTAssertFalse(message.contains("PRIVATE RESPONSE"))
    }
}
