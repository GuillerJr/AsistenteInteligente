import AppKit
import DesktopCore
import Foundation

@MainActor extension MCPStore {
    func setContext(_ value: SessionScope?, bindingKey: String? = nil) async {
        guard value != context || (bindingKey != nil && bindingKey != contextBindingKey) else { return }
        guard pendingContentRunID == nil else {
            errorMessage = "Consulta o cancela la lectura MCP pendiente antes de cambiar de contexto. Su identidad se conserva."
            return
        }
        generation += 1; contentTask?.cancel(); contentTask = nil
        await closeApplication()
        guard !appCleanupUnconfirmed else { return }
        context = value; contextBindingKey = bindingKey; contentPreview = nil
        pendingContentRunID = nil; pendingContentServer = nil; pendingContentCatalog = nil
    }
    func loadCatalog(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, server.connected, servers.contains(server), let client else { return }
        var version = generation
        do {
            let result = try await client.catalog(serverID: server.id)
            guard runtimeIsActive(), !isChanging, version == generation, servers.contains(server) else { return }
            if app?.serverID == server.id, appCatalogHash != result.hash {
                generation += 1; version = generation
                await closeApplication()
                guard runtimeIsActive(), !isChanging, version == generation, servers.contains(server) else { return }
            }
            catalogs[server.id] = result; catalogSources[server.id] = server; errorMessage = nil
        } catch { if version == generation { report(error) } }
    }
    func expandTemplate(server: String, template: String, arguments: [String: RPCValue]) async -> String? {
        guard runtimeIsActive(), !isChanging, let client, let catalog = catalogs[server],
              contentSourceIsCurrent(server: server, catalog: catalog.hash) else { return nil }
        let version = generation
        do {
            let uri = try await client.expandTemplate(serverID: server, catalog: catalog.hash, template: template, arguments: arguments)
            guard version == generation, !isChanging, catalogs[server]?.hash == catalog.hash,
                  contentSourceIsCurrent(server: server, catalog: catalog.hash) else { return nil }
            return uri
        } catch { if version == generation { report(error) }; return nil }
    }
    func readContent(server: String, kind: String, name: String, arguments: [String: RPCValue] = [:], app: Bool = false, toolRunID: String? = nil) async {
        guard runtimeIsActive(), !blocksContextChanges, pendingContentRunID == nil, let client, let scope = context,
              let catalog = catalogs[server], contentSourceIsCurrent(server: server, catalog: catalog.hash) else { return }
        let version = generation, request = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        isChanging = true; contentPreview = nil; errorMessage = nil
        if app {
            await closeApplication()
            guard !appCleanupUnconfirmed, version == generation, context == scope,
                  contentSourceIsCurrent(server: server, catalog: catalog.hash) else { isChanging = false; return }
        }
        pendingContentRunID = request; pendingContentServer = server; pendingContentCatalog = catalog.hash; pendingAppOpen = app
        defer { isChanging = false }
        do {
            let result: RPCValue
            if app { result = try await client.openApp(scope: scope, serverID: server, catalog: catalog.hash, uri: name, requestID: request, toolRunID: toolRunID) }
            else { result = try await client.content(scope: scope, serverID: server, catalog: catalog.hash, kind: kind, name: name, arguments: arguments, requestID: request) }
            guard version == generation, context == scope,
                  let run = result["run_id"].string, ManagementValidation.identifier(run) else { throw RPCFailure.invalidResponse }
            pendingContentRunID = run
            onRunStarted?(run, scope)
            resumeContentRead()
        } catch {
            // Keep the submitted identity. A missing ACK is not a reason to
            // execute a second read or replace an uncertain tool operation.
            if version == generation {
                report(error)
                if RunPresentation.startWasRejected(error) { pendingContentRunID = nil }
                else { onRunStarted?(request, scope) }
            }
        }
    }
    func resumeContentRead() {
        guard runtimeIsActive(), let client, let scope = context, let run = pendingContentRunID else { return }
        guard let server = pendingContentServer, let catalog = pendingContentCatalog else { return }
        let version = generation, appOpen = pendingAppOpen
        contentTask?.cancel()
        contentTask = Task { [weak self] in
            guard let self else { return }
            do {
                for _ in 0..<600 {
                    guard !Task.isCancelled, version == generation, context == scope else { return }
                    if appOpen {
                        let result = try await client.appDocument(scope: scope, runID: run)
                        if result["settled"].bool == true {
                            guard version == generation, context == scope else { return }
                            if result["app"].object != nil {
                                let document = try MCPAppDocument(result["app"], expectedServer: server, expectedCatalog: catalog)
                                if !contentSourceIsCurrent(server: server, catalog: catalog) {
                                    app = document; appCatalogHash = catalog; appSource = catalogSources[server]
                                    await closeApplication()
                                    errorMessage = "La fuente MCP cambió. Se retiró la App recibida; su solicitud no se volverá a ejecutar."
                                } else {
                                    await presentApplication(document, catalog: catalog, input: result["app"].object?["tool_input"], output: result["app"].object?["tool_result"])
                                }
                                pendingContentRunID = nil
                            } else { pendingContentRunID = nil; errorMessage = "La lectura terminó sin una App MCP disponible." }
                            return
                        }
                    } else {
                        let result = try await client.contentResult(scope: scope, runID: run)
                        if result.settled {
                            guard version == generation, context == scope else { return }
                            pendingContentRunID = nil
                            guard contentSourceIsCurrent(server: server, catalog: catalog) else {
                                contentPreview = nil
                                errorMessage = "La fuente MCP cambió. El recibo conserva la solicitud, pero su contenido anterior no se mostrará ni reenviará."
                                return
                            }
                            contentPreview = result
                            if result.errorCode == "approval_denied" {
                                errorMessage = "Operación rechazada. No se consultó la fuente MCP."
                            } else if result.errorCode != nil {
                                errorMessage = "La fuente MCP no confirmó su resultado. Revisa el recibo de la operación."
                            }
                            return
                        }
                    }
                    try await Task.sleep(for: .milliseconds(350))
                }
                errorMessage = "El resultado sigue pendiente. Consulta la misma solicitud; no se volverá a ejecutar."
            } catch is CancellationError { }
            catch { if version == generation { report(error) } }
        }
    }
    private func presentApplication(_ document: MCPAppDocument, catalog: String, input: RPCValue?, output: RPCValue?) async {
        guard let scope = context, client != nil, runtimeIsActive() else { return }
        await closeApplication()
        guard !appCleanupUnconfirmed else { await discardApplication(document, catalog: catalog); return }
        let version = generation
        let origins = document.policy.connectDomains + document.policy.resourceDomains
        guard OwnerManagementReview.confirm(title: "Abrir App MCP externa", action: "Abrir este contenido",
            detail: "Servidor: \(document.serverID)\nRecurso: \(document.resourceURI)\nSHA-256: \(document.sha256)\n\n"
                + (origins.isEmpty ? "Sin conexiones de red declaradas." : "Orígenes de red declarados:\n" + origins.joined(separator: "\n"))
                + "\n\nEl contenido es externo y no está verificado. No recibe cookies ni credenciales del proveedor. Las herramientas se aprueban mediante la autoridad habitual.",
            isCurrent: { self.runtimeIsActive() && self.generation == version && self.context == scope }) else {
            await discardApplication(document, catalog: catalog); return
        }
        guard contentSourceIsCurrent(server: document.serverID, catalog: catalog) else {
            app = document; appCatalogHash = catalog; appSource = catalogSources[document.serverID]
            await closeApplication(); return
        }
        app = document; appCatalogHash = catalog; appSource = catalogSources[document.serverID]; appError = nil
        let renderer = MCPAppRenderer(document: document, toolInput: input, toolResult: output,
            isCurrent: { [weak self] in self?.runtimeIsActive() == true && self?.appSurfaceVisible == true && self?.generation == version && self?.context == scope && self?.app?.id == document.id && self?.contentSourceIsCurrent(server: document.serverID, catalog: catalog) == true },
            request: { [weak self] message in
                guard let self, runtimeIsActive(), generation == version, context == scope, app?.id == document.id else { throw MCPAppFailure.closed }
                return try await requestApplication(message, document: document, scope: scope, version: version)
            })
        renderer.onError = { [weak self] text in if self?.generation == version { self?.appError = text } }
        // Own the renderer while its proxy is starting, so retiring the source
        // can close that proxy immediately instead of waiting for start().
        appRenderer = renderer
        do {
            try await renderer.start()
            guard version == generation, context == scope, app?.id == document.id else { await renderer.close(); return }
        }
        catch {
            await renderer.close()
            guard version == generation, context == scope, app?.id == document.id else { return }
            errorMessage = "La App MCP no pudo abrirse de forma aislada. No se intentará cargar de nuevo automáticamente."
            await closeApplication()
        }
    }
    private func requestApplication(_ message: RPCValue, document: MCPAppDocument, scope: SessionScope, version: Int) async throws -> RPCValue {
        guard !isChanging, !appRequestPending, let client else { throw MCPAppFailure.busy }
        let token = UUID()
        appRequestToken = token
        appRequestPending = true
        defer { if appRequestToken == token { appRequestToken = nil; appRequestPending = false } }
        let response: RPCValue
        do { response = try await client.appRequest(id: document.id, message: message) }
        catch {
            if version == generation, context == scope, app?.id == document.id,
               !RunPresentation.startWasRejected(error) {
                appCleanupUnconfirmed = true
                appError = "La solicitud no quedó confirmada. Cierra esta App para reconciliar sus operaciones antes de continuar."
            }
            throw error
        }
        guard version == generation, context == scope, app?.id == document.id else { throw MCPAppFailure.closed }
        if let run = response["run_id"].string, ManagementValidation.identifier(run) { onRunStarted?(run, scope) }
        var latest = response
        guard let requestID = response["request_id"].string, ManagementValidation.identifier(requestID) else { throw RPCFailure.invalidResponse }
        for _ in 0..<600 {
            guard !Task.isCancelled, version == generation, context == scope, app?.id == document.id else { throw MCPAppFailure.closed }
            if latest["settled"].bool == true {
                if case .null = latest["error"] { return latest["result"] }
                throw RPCFailure.remote(-32020, "mcp_app_request_failed")
            }
            try await Task.sleep(for: .milliseconds(350))
            latest = try await client.pollApp(id: document.id, requestID: requestID)
        }
        throw RPCFailure.disconnected
    }
    func closeApplication() async {
        guard !appCloseInProgress, app != nil || !pendingAppCloseIDs.isEmpty else { return }
        appCloseInProgress = true
        defer { appCloseInProgress = false }
        let current = app
        if let current { pendingAppCloseIDs.insert(current.id) }
        let closingIDs = pendingAppCloseIDs
        // Stop accepting JS before awaiting the backend capability revocation.
        let renderer = appRenderer, catalog = appCatalogHash, source = appSource
        app = nil
        appRenderer = nil; appCatalogHash = nil; appSource = nil
        appCleanupUnconfirmed = true
        await renderer?.close()
        for identity in closingIDs {
            do {
                guard let client else { throw RPCFailure.unavailable }
                try await client.closeApp(id: identity)
                pendingAppCloseIDs.remove(identity)
            } catch { /* Retain the exact capability identity for explicit reconciliation. */ }
        }
        if pendingAppCloseIDs.isEmpty {
            appCleanupUnconfirmed = false; appRequestToken = nil; appRequestPending = false; appError = nil
        } else {
            appError = "No se confirmó el cierre de la App MCP. Reintenta el cierre antes de cambiar de contexto."
            app = current; appCatalogHash = catalog; appSource = source
        }
    }
    private func discardApplication(_ document: MCPAppDocument, catalog: String) async {
        pendingAppCloseIDs.insert(document.id)
        if app == nil { app = document; appCatalogHash = catalog; appSource = catalogSources[document.serverID] }
        await closeApplication()
    }
    func contentSourceIsCurrent(server: String, catalog: String) -> Bool {
        runtimeIsActive() && catalogs[server]?.hash == catalog
            && servers.contains(where: { $0.id == server && $0.connected && $0 == catalogSources[server] })
    }
    func prepareServerChange(serverID: String) async -> Bool {
        guard pendingContentRunID == nil else {
            errorMessage = "Consulta o cancela la lectura MCP pendiente antes de cambiar el servidor. No se cancelará ni reenviará por ti."
            return false
        }
        generation += 1; contentTask?.cancel(); contentTask = nil
        catalogs = [:]; catalogSources = [:]; contentPreview = nil
        oauthAuthorizationURLs.removeValue(forKey: serverID)
        await closeApplication()
        guard !appCleanupUnconfirmed else {
            errorMessage = "El cierre de la App MCP sigue sin confirmar. Completa ese cierre antes de cambiar servidores."
            return false
        }
        return runtimeIsActive()
    }
    func discoverOAuth(_ server: MCPServerSnapshot, issuer: String? = nil) async -> MCPOAuthDiscovery? {
        guard runtimeIsActive(), !isChanging, let client, servers.contains(server) else { return nil }
        let version = generation
        isChanging = true
        defer { isChanging = false }
        do {
            let fields = issuer.map { ["issuer": RPCValue.string($0)] } ?? [:]
            let result = try await client.oauth("discover", serverID: server.id, fields: fields)
            let discovery = try MCPOAuthDiscovery(result["metadata"], allowLoopback: server.configuration.allowLoopback)
            guard version == generation, servers.contains(server), !Task.isCancelled,
                  issuer == nil || discovery.validatesSelection(issuer!) else { return nil }
            errorMessage = nil
            return discovery
        } catch { if version == generation { report(error) }; return nil }
    }
    func authorizeOAuth(_ server: MCPServerSnapshot, clientID: String, scopes: [String], issuer selectedIssuer: String) async -> Bool {
        guard runtimeIsActive(), !isChanging, let client, servers.contains(server), !selectedIssuer.isEmpty else { return false }
        guard oauthHasNoPendingContent() else { return false }
        isChanging = true
        defer { isChanging = false }
        let version = generation
        do {
            let response = try await client.oauth("discover", serverID: server.id, fields: ["issuer": .string(selectedIssuer)])
            let discovery = try MCPOAuthDiscovery(response["metadata"], allowLoopback: server.configuration.allowLoopback)
            guard discovery.validatesSelection(selectedIssuer), let issuer = discovery.issuer,
                  ManagementValidation.text(clientID, limit: 512), scopes.count <= 32,
                  OwnerManagementReview.confirm(title: "Autorizar conexión OAuth de MCP", action: "Abrir inicio de sesión",
                    detail: "Servidor: \(server.id)\nEmisor elegido: \(issuer)\nRecurso: \(discovery.resource)\nCliente público: \(clientID)\nScopes: \(scopes.joined(separator: ", "))\n\nCompleta tú el acceso y la autorización en el navegador. Los tokens permanecen en el servicio y no llegan a las Apps MCP.",
                    isCurrent: { self.runtimeIsActive() && self.generation == version && self.servers.contains(server) }) else { return false }
            guard await prepareOAuthMutation(server, generation: version) else { return false }
            let result = try await client.oauth("begin", serverID: server.id,
                fields: ["client_id": .string(clientID), "scopes": .array(scopes.map(RPCValue.string))])
            let flow = result["flow"]
            guard version == generation, let id = flow["flow_id"].string, ManagementValidation.identifier(id),
                  let raw = flow["authorization_url"].string, let url = URL(string: raw),
                  ["https", "http"].contains(url.scheme ?? ""), url.user == nil, url.password == nil,
                  url.scheme == "https" || (server.configuration.allowLoopback && ["127.0.0.1", "::1", "[::1]"].contains(url.host ?? "")) else { throw RPCFailure.invalidResponse }
            // The public flow ID and validated URL exist only in RAM. Reopening
            // this URL resumes the same flow instead of issuing another grant.
            oauthStates[server.id] = .object(["flow_id": .string(id), "status": .string("authorization_pending")])
            oauthAuthorizationURLs[server.id] = url
            guard NSWorkspace.shared.open(url) else { throw RPCFailure.unavailable }
            return true
        } catch { if version == generation { report(error) }; return false }
    }
    func finishOAuth(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, let client, let flow = oauthStates[server.id]?["flow_id"].string else { return }
        isChanging = true
        defer { isChanging = false }
        let version = generation
        do {
            let result = try await client.oauth("finish", serverID: server.id, fields: ["flow_id": .string(flow)])
            guard version == generation, servers.contains(server), let status = result["status"].string,
                  ["connected", "authorization_pending", "failed"].contains(status) else { throw RPCFailure.invalidResponse }
            oauthStates[server.id] = .object(["flow_id": .string(flow), "status": .string(status)])
            if status != "authorization_pending" { oauthAuthorizationURLs.removeValue(forKey: server.id) }
        } catch { report(error) }
    }
    func revokeOAuth(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, let client, servers.contains(server), oauthHasNoPendingContent() else { return }
        let version = generation
        isChanging = true
        defer { isChanging = false }
        guard OwnerManagementReview.confirm(title: "Revocar OAuth de «\(server.id)»", action: "Revocar esta conexión",
            detail: "Primero se cerrará cualquier App MCP abierta de este servidor y se esperará a que terminen sus operaciones. Después se retirará la autorización local. No afecta tu sesión de Codex.",
            isCurrent: { self.runtimeIsActive() && self.generation == version && self.servers.contains(server) }) else { return }
        guard await prepareOAuthMutation(server, generation: version) else { return }
        do {
            let result = try await client.oauth("revoke", serverID: server.id)
            guard version == generation, servers.contains(server), result["revoked"].bool == true else { throw RPCFailure.invalidResponse }
            oauthStates.removeValue(forKey: server.id)
            oauthAuthorizationURLs.removeValue(forKey: server.id)
        } catch { report(error) }
    }
    func refreshOAuth(_ server: MCPServerSnapshot) async {
        guard runtimeIsActive(), !isChanging, let client, servers.contains(server), server.configuration.transport != .stdio else { return }
        guard oauthHasNoPendingContent() else { return }
        let version = generation
        isChanging = true
        defer { isChanging = false }
        guard OwnerManagementReview.confirm(title: "Renovar autorización OAuth", action: "Solicitar esta renovación",
            detail: "Servidor: \(server.id)\nDestino: \(server.configuration.endpoint ?? "")\n\nSe solicitará una renovación explícita de la autorización existente. Los tokens permanecen en el servicio. Si se pierde la respuesta, Jarvis no enviará otra renovación automáticamente.",
            isCurrent: { self.runtimeIsActive() && self.generation == version && self.servers.contains(server) }) else { return }
        guard await prepareOAuthMutation(server, generation: version) else { return }
        do {
            let result = try await client.oauth("refresh", serverID: server.id)
            guard version == generation, servers.contains(server), let status = result["status"].string,
                  ["connected", "failed"].contains(status) else { throw RPCFailure.invalidResponse }
            oauthStates[server.id] = .object(["status": .string(status)])
            if status == "failed" { errorMessage = "La autorización no quedó renovada. Revisa su estado antes de iniciar otro acceso." }
        } catch {
            if version == generation { errorMessage = "La respuesta de renovación no quedó confirmada. No se volverá a solicitar automáticamente." }
        }
    }
    private func oauthHasNoPendingContent() -> Bool {
        guard pendingContentRunID == nil else {
            errorMessage = "Cancela o espera la lectura MCP pendiente y consulta su resultado antes de cambiar la autorización. Jarvis no la cancelará ni reenviará por ti."
            return false
        }
        return true
    }
    private func prepareOAuthMutation(_ server: MCPServerSnapshot, generation expected: Int) async -> Bool {
        guard runtimeIsActive(), generation == expected, servers.contains(server), oauthHasNoPendingContent() else { return false }
        if app?.serverID == server.id { await closeApplication() }
        guard !appCleanupUnconfirmed else {
            errorMessage = "El cierre de la App MCP sigue sin confirmar. No se modificó OAuth; completa ese cierre antes de continuar."
            return false
        }
        oauthAuthorizationURLs.removeValue(forKey: server.id)
        catalogs.removeValue(forKey: server.id); catalogSources.removeValue(forKey: server.id)
        if pendingContentServer == server.id { contentPreview = nil }
        return runtimeIsActive() && generation == expected && servers.contains(server) && oauthHasNoPendingContent()
    }
}
