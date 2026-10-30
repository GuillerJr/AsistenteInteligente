import Foundation
import XCTest
@testable import DesktopCore

/// Actual signed RPC and own Python/SQLite. No model, account or owner profile.
final class ProductCoordinationIntegrationTests: XCTestCase {
    @MainActor
    func testFreshServiceZeroGenerationOrganizationAndTwoWorkspacesUseActualWire() async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"],
              let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para integrar Swift y Python.")
        }
        let root = "/private/tmp/jarvis-product-wire-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try FileManager.default.removeItem(atPath: root) }
        let profile = root + "/profile"
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.arguments = ["-c", """
        import asyncio, signal, sys
        from pathlib import Path
        from jarvis_core.service import JarvisService
        class Peer:
          async def account_status(self): raise AssertionError('product wire used account')
          async def chat(self,*args,**kwargs): raise AssertionError('product wire used model')
          async def aclose(self): pass
        async def main():
          service=JarvisService(Path(sys.argv[1]),Peer())
          done=asyncio.Event()
          asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
          try: await service.start(); await done.wait()
          finally: await service.aclose()
        asyncio.run(main())
        """, profile]
        var paths = source
        if let dependencies = environment["JARVIS_DESKTOP_DEPENDENCIES"] { paths += ":" + dependencies }
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": paths,
            "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try await startFixtureProcess(child, directory: root)
        try await ServiceBootstrap.waitForHealth(stateDirectory: profile) { child.isRunning }
        let transport = LocalRPCClient(stateDirectory: profile)
        let initialized = try await transport.call("product.initialize")
        let workspaceID = try XCTUnwrap(initialized["workspaces"].array?.first?["workspace_id"].string)
        let created = try await transport.call("sessions.create", params: [
            "title": .string("QA inicial"), "workspace_id": .string(workspaceID)])
        let scope = SessionScope(sessionID: try XCTUnwrap(created["session"]["session_id"].string),
                                 temporary: false)
        let store = ProductCoordinationStore()
        store.configure(transport); store.setScope(scope)
        await store.refresh()
        XCTAssertNil(store.errorMessage)
        let fresh = try XCTUnwrap(store.snapshot)
        XCTAssertEqual(fresh.binding.securityGeneration, 0)
        XCTAssertEqual(fresh.context.generation, 0)
        XCTAssertTrue(fresh.isOwner)
        let changed = await store.bind(workspaceID: workspaceID, group: "Grupo QA", pinned: true)
        XCTAssertTrue(changed)
        let organized = try XCTUnwrap(store.snapshot)
        XCTAssertEqual(organized.binding.group, "Grupo QA")
        XCTAssertTrue(organized.binding.pinned)
        XCTAssertEqual(organized.binding.securityGeneration, 0)
        XCTAssertGreaterThan(organized.binding.revision, fresh.binding.revision)
        let secondPath = root + "/second-workspace"
        try FileManager.default.createDirectory(atPath: secondPath, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let second = try await transport.call("product.initialize", params: [
            "workspace_path": .string(secondPath), "workspace_name": .string("Segunda carpeta")])
        let secondID = try XCTUnwrap(second["workspaces"].array?.first(where: {
            $0["path"].string == secondPath
        })?["workspace_id"].string)
        let other = try await transport.call("sessions.create", params: [
            "title": .string("QA segunda carpeta"), "workspace_id": .string(secondID)])
        await store.refresh()
        let both = try XCTUnwrap(store.snapshot)
        XCTAssertEqual(both.sessions.count, 2)
        XCTAssertTrue(both.sessions.allSatisfy { $0.securityGeneration == 0 })
        XCTAssertTrue(both.sessions.contains { $0.workspaceID == secondID })
        let otherScope = SessionScope(sessionID: try XCTUnwrap(other["session"]["session_id"].string),
                                      temporary: false)
        let response = try await transport.call("product.snapshot", params: otherScope.params)
        let parsed = try ProductCoordinationSnapshot(response, expectedScope: otherScope)
        XCTAssertEqual(parsed.binding.workspaceID, secondID)
        let rebound = await store.bind(workspaceID: secondID, group: "Grupo QA", pinned: true)
        XCTAssertTrue(rebound)
        XCTAssertEqual(store.snapshot?.binding.securityGeneration, 1)
        XCTAssertEqual(store.snapshot?.context.generation, 1)
        store.close(); await transport.close()
    }
}
