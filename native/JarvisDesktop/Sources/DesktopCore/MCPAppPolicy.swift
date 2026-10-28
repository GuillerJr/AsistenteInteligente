import Foundation

public enum MCPAppFailure: Error, LocalizedError, Equatable, Sendable {
    case invalidDocument, invalidMessage, forbiddenOrigin, unsupported, closed, busy
    public var errorDescription: String? {
        switch self {
        case .invalidDocument: return "La App MCP no publicó un documento válido con identidad verificable."
        case .invalidMessage: return "La App MCP envió un mensaje no válido."
        case .forbiddenOrigin: return "La App MCP declaró un origen de red no permitido."
        case .unsupported: return "Esta capacidad de la App MCP no está disponible en Jarvis."
        case .closed: return "La App MCP ya no pertenece al contexto abierto."
        case .busy: return "La App MCP tiene una solicitud pendiente; espera su resultado."
        }
    }
}

/// Server declarations are data, not authority. The owner must approve these
/// exact origins before rendering; the native proxy still rejects private DNS.
public struct MCPAppContentPolicy: Equatable, Sendable {
    public let connectDomains: [String]
    public let resourceDomains: [String]
    public static func isAppResource(uri: String, mimeType: String?) -> Bool {
        let mime = mimeType?.lowercased().filter { !$0.isWhitespace }
        return uri.lowercased().hasPrefix("ui://") || mime == "text/html;profile=mcp-app" || mime == "text/html;profile=\"mcp-app\""
    }
    public init(connectDomains: [String] = [], resourceDomains: [String] = []) throws {
        guard connectDomains.count <= 16, resourceDomains.count <= 16 else { throw MCPAppFailure.forbiddenOrigin }
        self.connectDomains = try connectDomains.map(Self.origin)
        self.resourceDomains = try resourceDomains.map(Self.origin)
        guard Set(self.connectDomains).count == self.connectDomains.count,
              Set(self.resourceDomains).count == self.resourceDomains.count else { throw MCPAppFailure.forbiddenOrigin }
    }
    public init(_ value: RPCValue) throws {
        guard let object = value.object,
              Set(object.keys).isSubset(of: ["connectDomains", "resourceDomains", "frameDomains", "baseUriDomains"])
        else { throw MCPAppFailure.forbiddenOrigin }
        func domains(_ key: String) throws -> [String] {
            guard let value = object[key] else { return [] }
            guard let rows = value.array, rows.allSatisfy({ $0.string != nil }) else { throw MCPAppFailure.forbiddenOrigin }
            return rows.compactMap(\.string)
        }
        // Nested frames and a remote base URI are deliberately not advertised
        // as host capabilities. They cannot expand a resource's authority.
        guard try domains("frameDomains").isEmpty, try domains("baseUriDomains").isEmpty else { throw MCPAppFailure.unsupported }
        try self.init(connectDomains: domains("connectDomains"), resourceDomains: domains("resourceDomains"))
    }
    public static func origin(_ raw: String) throws -> String {
        guard let components = URLComponents(string: raw), components.query == nil,
              components.fragment == nil, components.path.isEmpty || components.path == "/" else {
            throw MCPAppFailure.forbiddenOrigin
        }
        do {
            let url = try BrowserAutomationPolicy().validate(raw)
            return BrowserAutomationPolicy.origin(url)
        } catch { throw MCPAppFailure.forbiddenOrigin }
    }
    public var contentSecurityPolicy: String {
        let connect = connectDomains.isEmpty ? "'none'" : connectDomains.joined(separator: " ")
        let resources = resourceDomains.joined(separator: " ")
        return "default-src 'none'; script-src 'unsafe-inline' \(resources); style-src 'unsafe-inline' \(resources); "
            + "img-src data: \(resources); font-src \(resources.isEmpty ? "'none'" : resources); "
            + "connect-src \(connect); frame-src 'none'; child-src 'none'; worker-src 'none'; "
            + "object-src 'none'; base-uri 'none'; form-action 'none'; media-src 'none'"
    }
}

/// RAM-only materialized source. No file URL, provider credential or local
/// transport token belongs in a document sent to the untrusted iframe.
public struct MCPAppDocument: Equatable, Identifiable, Sendable {
    public let id: String
    public let serverID: String
    public let resourceURI: String
    public let html: String
    public let sha256: String
    public let policy: MCPAppContentPolicy
    public init(id: String, serverID: String, resourceURI: String, html: String,
                sha256: String, policy: MCPAppContentPolicy) throws {
        guard ManagementValidation.identifier(id), serverID.range(of: "^[a-z][a-z0-9_]{0,23}$", options: .regularExpression) != nil,
              ManagementValidation.text(resourceURI, limit: 4096),
              ManagementValidation.text(html, limit: 98_304), ManagementValidation.hash(sha256),
              ManagementValidation.digest(html) == sha256 else { throw MCPAppFailure.invalidDocument }
        self.id = id; self.serverID = serverID; self.resourceURI = resourceURI
        self.html = html; self.sha256 = sha256; self.policy = policy
    }
}

public struct MCPAppBridgeMessage: Equatable, Sendable {
    public let id: RPCValue?
    public let method: String
    public let params: RPCValue
    public init(_ value: RPCValue) throws {
        guard let object = value.object, Set(object.keys).isSubset(of: ["jsonrpc", "id", "method", "params"]),
              value["jsonrpc"].string == "2.0", let method = value["method"].string,
              ManagementValidation.text(method, limit: 128),
              (object["params"] == nil || value["params"].object != nil),
              let bytes = try? JSONEncoder().encode(value), bytes.count <= 65_536 else { throw MCPAppFailure.invalidMessage }
        if let id = object["id"] {
            guard id.integer.map({ $0 >= 0 }) == true || id.string.map({ ManagementValidation.text($0, limit: 128) }) == true else {
                throw MCPAppFailure.invalidMessage
            }
            self.id = id
        } else { id = nil }
        self.method = method; params = object["params"] ?? .object([:])
    }
    public static func appVisible(_ value: RPCValue?) throws -> Bool {
        guard let value else { return true }
        guard let rows = value.array, !rows.isEmpty, rows.count <= 2,
              rows.allSatisfy({ $0.string == "app" || $0.string == "model" }),
              Set(rows.compactMap(\.string)).count == rows.count else { throw MCPAppFailure.invalidMessage }
        return rows.contains(.string("app"))
    }
}
