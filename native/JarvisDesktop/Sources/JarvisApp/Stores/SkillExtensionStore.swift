import DesktopCore
import Foundation

@MainActor extension ManagementStore {
    func completeExtensionCleanup() async {
        guard runtimeIsActive(), !isChanging, let client,
              OwnerManagementReview.confirm(title: "Completar cierre de extensiones", action: "Revisar y cerrar recursos retenidos",
                detail: "Se conserva el recibo y no se vuelve a ejecutar ninguna modificación. Sólo se consulta y cierra el propietario de recursos retenidos.", isCurrent: runtimeIsActive) else { return }
        isChanging = true
        defer { isChanging = false }
        let version = generation
        do {
            let result = try await client.cleanupExtensions()
            guard version == generation else { return }
            extensionCleanupReceipt = result.receipt
            errorMessage = result.closed ? "Los recursos se cerraron; la publicación anterior sigue conservando su recibo sin confirmar."
                : "El cierre aún no se confirmó; no se repitió la operación."
        } catch { if version == generation { errorMessage = "No se pudo confirmar el cierre de las extensiones." } }
    }
    func metadata(_ skill: SkillDescriptor) async -> SkillInvocationMetadata? {
        guard runtimeIsActive(), let client else { return nil }
        let version = generation
        do { let result = try await client.skillMetadata(skill); return version == generation ? result : nil }
        catch { errorMessage = "No se pudo consultar la metadata de esta versión de la skill."; return nil }
    }
    func versions(_ skill: SkillDescriptor) async -> [ExtensionVersion]? {
        guard runtimeIsActive(), let client else { return nil }
        let version = generation
        do { let result = try await client.skillVersions(skill); return version == generation ? result : nil }
        catch { errorMessage = "No se pudieron consultar las versiones de esta skill."; return nil }
    }
    func resource(_ skill: SkillDescriptor, path: String) async -> ExtensionResource? {
        guard runtimeIsActive(), let client, skills.contains(skill) else { return nil }
        let version = generation
        do { let result = try await client.skillResource(skill, path: path); return version == generation ? result : nil }
        catch { errorMessage = "No se pudo leer el recurso conservado de esta versión."; return nil }
    }
    func invoke(_ skill: SkillDescriptor, arguments: [String: RPCValue]) async -> SkillInvocation? {
        guard runtimeIsActive(), !isChanging, let client, skills.contains(skill) else { return nil }
        let version = generation
        do { let result = try await client.skillInvocation(skill, arguments: arguments); return version == generation ? result : nil }
        catch { errorMessage = "Los argumentos o la versión de la skill ya no son válidos. No se ejecutó código."; return nil }
    }
    func inspectLibrary(path: String) async -> SkillLibraryPreview? {
        guard runtimeIsActive(), !isChanging, let client else { return nil }
        let version = generation
        do { let result = try await client.inspectSkillLibrary(path: path); return version == generation ? result : nil }
        catch { errorMessage = "La biblioteca no pudo leerse de forma acotada. Revisa los archivos declarados."; return nil }
    }
    func importLibrary(_ preview: SkillPreviewSnapshot, library: SkillLibraryPreview, agent: String) async -> Bool {
        guard preview.hash == library.skillHash, preview.name == library.name else { return false }
        return await change("skills.import_library", ["agent_id": .string(agent), "path": .string(preview.sourcePath),
            "expected_sha256": .string(preview.hash), "expected_library_sha256": .string(library.libraryHash), "enabled": .bool(false)],
            title: "Importar biblioteca de skill deshabilitada", action: "Importar estos archivos",
            detail: "Sólo los recursos declarados conservados en esta copia. No se ejecuta código ni se concede acceso a sus rutas de origen.\n\n"
                + preview.text + "\n\nBiblioteca SHA-256: " + library.libraryHash + "\n"
                + library.files.map { "\($0.path) · \($0.bytes) bytes · \($0.hash)" }.joined(separator: "\n"))
    }
    func changeVersion(_ skill: SkillDescriptor, version: ExtensionVersion, retire: Bool) async -> Bool {
        guard !version.current, let revision = version.revision else { return false }
        var params = skill.revisionParams; params["target_revision"] = .number(Double(revision))
        return await change(retire ? "skills.retire_version" : "skills.rollback", params,
            title: retire ? "Retirar copia histórica de la skill" : "Volver a una versión conservada", action: "Autorizar esta versión",
            detail: "Versión: \(version.version) · revisión \(revision)\nSHA-256: \(version.hash)\n\n"
                + (retire ? "Sólo se retira la copia histórica elegida." : "Se conserva el estado actual y la versión restaurada queda deshabilitada."))
    }
}
