import Foundation
import XCTest
@testable import DesktopCore

final class ManagementIntegrationTests: XCTestCase {
    @MainActor
    private func withFixture(_ operation: (ManagementClient, LocalRPCClient, String) async throws -> Void) async throws {
        let environment = ProcessInfo.processInfo.environment
        guard let python = environment["JARVIS_DESKTOP_PYTHON"], let source = environment["JARVIS_DESKTOP_SOURCE"] else {
            throw XCTSkip("Usa script/test_desktop.sh para el contrato Swift con servicio Python propio.")
        }
        let directory = "/private/tmp/jvs-mg-" + UUID().uuidString.prefix(8)
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        // Actual own service/state/management, but synthetic provider and owner
        // choices. No credentials, TCC, original owner state or effect tools.
        child.arguments = ["-c", """
        import asyncio,signal,sys,time
        from pathlib import Path
        from uuid import uuid4
        from jarvis_core.service import JarvisService
        class Peer:
          calls=0
          async def account_status(self): return {'connected':True,'mode':'chatgpt'}
          async def chat(self,message,*,on_thread,on_event,**kwargs):
            self.calls+=1
            on_thread('synthetic_'+uuid4().hex)
            return {'answer':'Respuesta propia de fixture; sin acciones.'}
          async def aclose(self): pass
        async def main():
          root=Path(sys.argv[1]); fixture=root/'skill_fixture'; fixture.mkdir(mode=0o700)
          manifest=fixture/'SKILL.md'
          manifest.write_text('---\\nname: fixture-local\\ndescription: Prueba propia sin permisos\\nresources: ["notes/example.txt"]\\n---\\nResponde en español.\\n')
          manifest.chmod(0o600)
          peer=Peer(); service=JarvisService(root,peer)
          async def trace(params): return {'calls':peer.calls,'runner_running':service.scheduler.running}
          service.server._handlers['fixture.trace']=trace
          done=asyncio.Event(); asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
          try:
            await service.start(); await done.wait()
          finally: await service.aclose()
        asyncio.run(main())
        """, directory]
        child.environment = ["PATH": "/usr/bin:/bin", "PYTHONPATH": source,
                             "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"]
        child.standardOutput = FileHandle.nullDevice; child.standardError = FileHandle.nullDevice
        try startFixtureProcess(child, directory: directory)
        try await ServiceBootstrap.waitForHealth(stateDirectory: directory) { child.isRunning }
        let transport = LocalRPCClient(stateDirectory: directory)
        let client = ManagementClient(transport: transport)
        do { try await operation(client, transport, directory); await client.close() }
        catch { await client.close(); throw error }
    }

    @MainActor
    func testAgentSkillScheduleAndForkUseActualServiceWithoutImplicitDispatch() async throws {
        try await withFixture { client, transport, directory in
            let initialAgents = try await client.agents()
            XCTAssertTrue(initialAgents.contains(where: { $0.id == "jarvis" }))
            let created = try await client.mutate("agents.create", params: ["name": .string("Agente de fixture"), "instructions": .string("No conceder permisos.")])
            let agent = try AgentDescriptor(created["agent"])
            let preview = try await client.inspect(path: directory + "/skill_fixture")
            let imported = try await client.mutate("skills.import", params: ["agent_id": .string(agent.id),
                "path": .string(preview.sourcePath), "expected_sha256": .string(preview.hash), "enabled": .bool(false)])
            let skill = try SkillDescriptor(imported["skill"])
            XCTAssertFalse(skill.enabled)
            let importedText = try await client.skillText(skill)
            let importedSkills = try await client.skills(agentID: agent.id)
            XCTAssertEqual(importedText, preview.text)
            XCTAssertEqual(importedSkills, [skill])
            let before = try await transport.call("fixture.trace")
            XCTAssertEqual(before["calls"].integer, 0)
            XCTAssertEqual(before["runner_running"].bool, false)
            let at = Date().timeIntervalSince1970 - 1
            let createdSchedule = try await client.mutate("schedules.create", params: ["title": .string("Tarea de fixture"),
                "prompt": .string("Responde sin herramientas"), "agent_id": .string(agent.id),
                "spec": .object(["kind": .string("once"), "at": .number(at), "timezone": .string("UTC")])])
            let schedule = try ScheduleDescriptor(createdSchedule["schedule"])
            XCTAssertFalse(schedule.enabled)
            let disabledCatalog = try await client.schedules()
            XCTAssertFalse(disabledCatalog.running)
            var enabled = schedule.revisionParams; enabled["enabled"] = .bool(true)
            _ = try await client.mutate("schedules.update", params: enabled)
            let reviewed = try await client.schedules()
            XCTAssertFalse(reviewed.running)
            let stillNotDispatched = try await transport.call("fixture.trace")
            XCTAssertEqual(stillNotDispatched["calls"].integer, 0)
            _ = try await client.mutate("scheduler.start", params: ["configuration_hash": .string(reviewed.configurationHash)])
            var history: [ScheduleOccurrenceSnapshot] = []
            for _ in 0..<100 {
                history = try await client.history(scheduleID: schedule.id)
                if history.first?.status == "completed" { break }
                try await Task.sleep(for: .milliseconds(20))
            }
            let occurrence = try XCTUnwrap(history.first)
            XCTAssertEqual(occurrence.status, "completed")
            XCTAssertEqual(occurrence.outcome, "known")
            let sessionID = try XCTUnwrap(occurrence.sessionID)
            let sessions = try await transport.call("sessions.list")
            let session = try XCTUnwrap(sessions["sessions"].array?.first(where: { $0["session_id"].string == sessionID }))
            XCTAssertEqual(session["agent_id"].string, agent.id)
            let fork = try await transport.call("sessions.fork", params: ["session_id": .string(sessionID), "temporary": .bool(false)])
            XCTAssertEqual(fork["session"]["parent_session_id"].string, sessionID)
            XCTAssertEqual(fork["session"]["agent_id"].string, agent.id)
            let stopped = try await client.mutate("scheduler.stop", params: [:])
            XCTAssertEqual(stopped["running"].bool, false)
            let scope = SessionScope(sessionID: sessionID, temporary: false)
            let processCatalog = try await client.processes(scope: scope)
            let finalTrace = try await transport.call("fixture.trace")
            XCTAssertTrue(processCatalog.processes.isEmpty)
            XCTAssertEqual(finalTrace["calls"].integer, 1)
        }
    }

    @MainActor
    func testChangedScheduleAndSkillCannotReuseOldReview() async throws {
        try await withFixture { client, transport, directory in
            let preview = try await client.inspect(path: directory + "/skill_fixture")
            let imported = try await client.mutate("skills.import", params: ["agent_id": .string("jarvis"),
                "path": .string(preview.sourcePath), "expected_sha256": .string(preview.hash)])
            let original = try SkillDescriptor(imported["skill"])
            var edit = original.revisionParams; edit["text"] = .string(preview.text + "Nueva línea de prueba.\n")
            _ = try await client.mutate("skills.update", params: edit)
            var stale = original.revisionParams; stale["enabled"] = .bool(true)
            do { _ = try await client.mutate("skills.enable", params: stale); XCTFail("La versión cambió.") } catch {}
            let updatedSkills = try await client.skills(agentID: "jarvis")
            XCTAssertFalse(updatedSkills[0].enabled)
            let old = try await client.schedules()
            _ = try await client.mutate("schedules.create", params: ["title": .string("Nueva"), "prompt": .string("Prueba"),
                "agent_id": .string("jarvis"), "spec": .object(["kind": .string("interval"), "interval_seconds": .number(3600), "timezone": .string("UTC")])])
            do { _ = try await client.mutate("scheduler.start", params: ["configuration_hash": .string(old.configurationHash)]); XCTFail("El catálogo cambió.") } catch {}
            let trace = try await transport.call("fixture.trace")
            XCTAssertEqual(trace["calls"].integer, 0)
            XCTAssertEqual(trace["runner_running"].bool, false)
        }
    }
}
