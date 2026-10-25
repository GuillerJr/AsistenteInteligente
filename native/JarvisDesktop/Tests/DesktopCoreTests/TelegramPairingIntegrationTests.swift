import Foundation
import XCTest
@testable import DesktopCore

@MainActor private final class PairingOpenerFixture: TelegramNativeURLOpener {
    var available = true
    var succeeds = true
    var opens = 0
    var acceptedGrammar = false
    func canOpenTelegram() -> Bool { available }
    func openTelegram(_ sensitiveURL: URL) async -> Bool {
        opens += 1
        let parts = URLComponents(url: sensitiveURL, resolvingAgainstBaseURL: false)
        acceptedGrammar = parts?.scheme == "tg" && parts?.host == "resolve" &&
            parts?.queryItems?.first?.value == "jarvis_prueba" && parts?.queryItems?.last?.value?.count == 43
        // Retain only booleans and counts. The URI/nonce are never diagnostics.
        return succeeds
    }
}

final class TelegramPairingIntegrationTests: XCTestCase {
    @MainActor
    private func withFixture(_ operation: (TelegramPairingClient, TelegramClient, LocalRPCClient) async throws -> Void) async throws {
        let env = ProcessInfo.processInfo.environment
        guard let python = env["JARVIS_DESKTOP_PYTHON"], let source = env["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa la cualificación de escritorio con el núcleo Python de pairing declarado.")
        }
        let directory = "/private/tmp/jvs-pair-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        // Actual service, journal, reader and revision checks; only the Bot API,
        // provider and native opener are synthetic. No network or real account.
        child.arguments = ["-c", """
        import asyncio, json, signal, sys, time
        from pathlib import Path
        from jarvis_core.service import JarvisService
        from jarvis_core.telegram import BotIdentity, TelegramError
        from jarvis_core.protocol import RpcError
        secret = '72501:SENTINEL_JARVIS_TOKEN_ONLY_IN_RAM'
        class Peer:
          calls = 0
          async def account_status(self): return {'connected': True, 'mode': 'chatgpt'}
          async def chat(self, *args, **kwargs):
            self.calls += 1; raise AssertionError('pairing must not invoke a model')
          async def aclose(self): pass
        class Bot:
          def __init__(self, token):
            if token != secret: raise TelegramError('telegram_token_invalid')
            self.identity = None; self.closed = False; self.updates = asyncio.Queue(); self.polls = 0; self.sent = 0
          async def connect(self):
            self.identity = BotIdentity(72501, 'jarvis_prueba'); return self.identity
          async def ensure_no_webhook(self): pass
          async def get_updates(self, offset, *, before_dispatch=None, **kwargs):
            if before_dispatch: before_dispatch()
            self.polls += 1; return await self.updates.get()
          async def aclose(self): self.closed = True; self.identity = None
        async def main():
          root = Path(sys.argv[1]); peer = Peer(); service = JarvisService(root, peer)
          service.initialize_telegram(client_factory=Bot)
          open_calls = 0; cancel_calls = 0; mode = 'valid'; last_nonce = None
          original_open = service.server._handlers['telegram.pairing.open']
          original_cancel = service.server._handlers['telegram.pairing.cancel']
          async def open_link(params):
            nonlocal open_calls
            open_calls += 1; value = await original_open(params)
            uri = value['telegram_uri']
            if mode == 'https': uri = uri.replace('tg://resolve?domain=', 'https://t.me/')
            elif mode == 'extra': uri += '&text=unexpected'
            elif mode == 'otherbot': uri = uri.replace('jarvis_prueba', 'otro_bot')
            elif mode == 'fragment': uri += '#unexpected'
            elif mode == 'credentials': uri = uri.replace('tg://resolve', 'tg://user@resolve')
            elif mode == 'encoded': uri = uri.replace('start=', 'start=%41')
            return {'challenge_id': value['challenge_id'], 'telegram_uri': uri}
          async def cancel(params):
            nonlocal cancel_calls
            cancel_calls += 1
            if mode == 'cleanup-fails': raise RpcError(-32035, 'synthetic_private_diagnostic')
            return await original_cancel(params)
          async def set_mode(params):
            nonlocal mode
            mode = params['mode']; return {'ok': True}
          async def claim(params):
            nonlocal last_nonce
            pairing = service._telegram_pairing; last_nonce = pairing._nonce
            await service._telegram_channel.client.updates.put([{'update_id': 1, 'message': {
              'message_id': 4, 'date': int(time.time()), 'text': '/start ' + last_nonce,
              'chat': {'id': 4567, 'type': 'private'}, 'from': {'id': 4567, 'is_bot': False}}}])
            return {'ok': True}
          async def trace(params):
            bot = service._telegram_channel.client if service._telegram_channel else None
            data = [p.read_bytes() for p in root.rglob('*') if p.is_file()]
            clean = all(secret.encode() not in value and b'tg://' not in value and
                        (last_nonce is None or last_nonce.encode() not in value) for value in data)
            return {'model_calls': peer.calls, 'polls': bot.polls if bot else 0, 'sent': bot.sent if bot else 0,
                    'opens': open_calls, 'cancels': cancel_calls, 'routes': len(service._telegram_journal.routes()),
                    'sessions': len(service.store.list_sessions()), 'secret_free_files': clean}
          service.server._handlers.update({'telegram.pairing.open': open_link, 'telegram.pairing.cancel': cancel, 'fixture.mode': set_mode,
                                          'fixture.claim': claim, 'fixture.trace': trace})
          done = asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, done.set)
          try: await service.start(); await done.wait()
          finally: await service.aclose()
        asyncio.run(main())
        """, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        try startFixtureProcess(child, directory: directory)
        try await ServiceBootstrap.waitForHealth(stateDirectory: directory) { child.isRunning }
        let transport = LocalRPCClient(stateDirectory: directory)
        do {
            try await operation(TelegramPairingClient(transport: transport), TelegramClient(transport: transport), transport)
            await transport.close()
        } catch { await transport.close(); throw error }
    }

    @MainActor
    private func begin(_ pairing: TelegramPairingClient, telegram: TelegramClient) async throws -> TelegramPairingStatus {
        let current = try await telegram.status()
        if !current.connected { _ = try await telegram.connect(token: "72501:SENTINEL_JARVIS_TOKEN_ONLY_IN_RAM") }
        let review = try await pairing.review(agentID: "jarvis")
        return try await pairing.start(review, acknowledgeUpdateConsumption: true, confirmNoOtherConsumer: true)
    }

    @MainActor
    func testReadOnlyReviewDoesNotPollAndTwoExplicitDecisionsCreateOnlyAnIdleRoute() async throws {
        try await withFixture { pairing, telegram, transport in
            let idle = try await pairing.status()
            XCTAssertEqual(idle.state, "idle")
            _ = try await telegram.connect(token: "72501:SENTINEL_JARVIS_TOKEN_ONLY_IN_RAM")
            let review = try await pairing.review(agentID: "jarvis")
            let untouched = try await transport.call("fixture.trace")
            XCTAssertEqual(untouched["polls"].integer, 0)
            do { _ = try await pairing.start(review, acknowledgeUpdateConsumption: false, confirmNoOtherConsumer: true); XCTFail("Falta una decisión.") }
            catch { XCTAssertEqual(error as? RPCFailure, .configuration) }
            let listening = try await pairing.start(review, acknowledgeUpdateConsumption: true, confirmNoOtherConsumer: true)
            let opener = PairingOpenerFixture()
            try await pairing.open(listening, using: opener)
            XCTAssertEqual(opener.opens, 1); XCTAssertTrue(opener.acceptedGrammar)
            do { try await pairing.open(listening, using: opener); XCTFail("No se debe recuperar ni abrir la URI dos veces.") }
            catch { XCTAssertEqual(error as? TelegramPairingOpenFailure, .alreadyAttempted) }
            _ = try await transport.call("fixture.claim")
            var claimed = try await pairing.status()
            for _ in 0..<100 where claimed.state != "claimed" {
                try await Task.sleep(for: .milliseconds(20)); claimed = try await pairing.status()
            }
            XCTAssertEqual(claimed.state, "claimed")
            let before = try await transport.call("fixture.trace")
            XCTAssertEqual(before["routes"].integer, 0); XCTAssertEqual(before["sessions"].integer, 0)
            let confirmed = try await pairing.confirm(claimed)
            XCTAssertFalse(confirmed.running); XCTAssertEqual(confirmed.routes.first?.chatID, 4567)
            XCTAssertEqual(confirmed.routes.first?.agentID, "jarvis")
            let finalState = try await pairing.status()
            XCTAssertEqual(finalState.state, "confirmed"); XCTAssertNil(finalState.claimID)
            let after = try await transport.call("fixture.trace")
            XCTAssertEqual(after["routes"].integer, 1); XCTAssertEqual(after["sessions"].integer, 1)
            XCTAssertEqual(after["opens"].integer, 1); XCTAssertEqual(after["model_calls"].integer, 0)
            XCTAssertEqual(after["sent"].integer, 0); XCTAssertEqual(after["secret_free_files"].bool, true)
        }
    }

    @MainActor
    func testMissingTelegramCancelsReaderWithoutRetrievingAnySensitiveURI() async throws {
        try await withFixture { pairing, telegram, transport in
            let state = try await begin(pairing, telegram: telegram)
            let opener = PairingOpenerFixture(); opener.available = false
            do { try await pairing.open(state, using: opener); XCTFail("No hay app Telegram instalada en este fixture.") }
            catch { XCTAssertEqual(error as? TelegramPairingOpenFailure, .unavailable) }
            XCTAssertEqual(opener.opens, 0)
            let status = try await pairing.status()
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(status.state, "cancelled"); XCTAssertEqual(trace["opens"].integer, 0)
            XCTAssertEqual(trace["model_calls"].integer, 0)
        }
    }

    @MainActor
    func testFailedNativeOpeningCancelsWithoutRetryingOrFallingBackToABrowser() async throws {
        try await withFixture { pairing, telegram, transport in
            let state = try await begin(pairing, telegram: telegram)
            let opener = PairingOpenerFixture(); opener.succeeds = false
            do { try await pairing.open(state, using: opener); XCTFail("El opener no confirmó la apertura.") }
            catch { XCTAssertEqual(error as? TelegramPairingOpenFailure, .failed) }
            let status = try await pairing.status()
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(status.state, "cancelled"); XCTAssertEqual(opener.opens, 1)
            XCTAssertEqual(trace["opens"].integer, 1); XCTAssertEqual(trace["routes"].integer, 0)
        }
    }

    @MainActor
    func testOnlyExactNativeBotURIsReachTheOpener() async throws {
        try await withFixture { pairing, telegram, transport in
            for mode in ["https", "extra", "otherbot", "fragment", "credentials", "encoded"] {
                _ = try await transport.call("fixture.mode", params: ["mode": .string(mode)])
                let state = try await begin(pairing, telegram: telegram)
                let opener = PairingOpenerFixture()
                do { try await pairing.open(state, using: opener); XCTFail("Se aceptó una URI fuera del contrato nativo.") }
                catch { XCTAssertEqual(error as? TelegramPairingOpenFailure, .invalidLink) }
                XCTAssertEqual(opener.opens, 0)
                let status = try await pairing.status()
                XCTAssertEqual(status.state, "cancelled")
            }
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(trace["model_calls"].integer, 0); XCTAssertEqual(trace["routes"].integer, 0)
        }
    }

    @MainActor
    func testUnconfirmedCleanupRemainsUnknownUntilAnExplicitCancellation() async throws {
        try await withFixture { pairing, telegram, transport in
            _ = try await transport.call("fixture.mode", params: ["mode": .string("cleanup-fails")])
            let state = try await begin(pairing, telegram: telegram)
            let opener = PairingOpenerFixture(); opener.succeeds = false
            do { try await pairing.open(state, using: opener); XCTFail("El fixture no confirma el cierre.") }
            catch { XCTAssertEqual(error as? TelegramPairingOpenFailure, .cleanupUnconfirmed) }
            let uncertain = try await pairing.status()
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(uncertain.state, "listening")
            XCTAssertEqual(trace["opens"].integer, 1); XCTAssertEqual(trace["cancels"].integer, 1)
            XCTAssertEqual(opener.opens, 1)
            _ = try await transport.call("fixture.mode", params: ["mode": .string("valid")])
            let cancelled = try await pairing.cancel()
            XCTAssertEqual(cancelled.state, "cancelled")
            let settled = try await transport.call("fixture.trace")
            XCTAssertEqual(settled["cancels"].integer, 2); XCTAssertEqual(settled["model_calls"].integer, 0)
        }
    }

    @MainActor
    func testRetiredNativeContextCannotOpenAResponseThatArrivesLater() async throws {
        try await withFixture { pairing, telegram, transport in
            let state = try await begin(pairing, telegram: telegram)
            let opener = PairingOpenerFixture()
            var checks = 0
            do {
                try await pairing.open(state, using: opener) { checks += 1; return checks == 1 }
                XCTFail("El contexto se retiró antes de entregar la URI al opener.")
            } catch { XCTAssertEqual(error as? TelegramPairingOpenFailure, .failed) }
            XCTAssertEqual(opener.opens, 0)
            let cancelled = try await pairing.status()
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(cancelled.state, "cancelled")
            XCTAssertEqual(trace["opens"].integer, 1); XCTAssertEqual(trace["cancels"].integer, 1)
        }
    }
}
