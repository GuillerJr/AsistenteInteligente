import Foundation

public enum MCPProtocolVersion: String, CaseIterable, Identifiable, Sendable {
    case sseLegacy = "2024-11-05"
    case legacy = "2025-11-25"
    case modern = "2026-07-28"
    public var id: String { rawValue }

    public func supports(_ transport: MCPTransport) -> Bool {
        self != .sseLegacy || transport == .sse
    }
}

public enum MCPTransport: String, CaseIterable, Identifiable, Sendable {
    case stdio, streamableHTTP = "streamable_http", sse
    public var id: String { rawValue }
    public var title: String { self == .stdio ? "Programa local · stdio" : self == .sse ? "HTTP + SSE" : "HTTP transmisible" }
}

public struct MCPOAuthConfiguration: Equatable, Sendable {
    public let resource: String
    public let clientID: String
    public let scopes: [String]
    public init(resource: String, clientID: String, scopes: [String], allowLoopback: Bool = false) throws {
        guard let url = URLComponents(string: resource), let host = url.host,
              url.scheme == "https" || (allowLoopback && url.scheme == "http" && ["127.0.0.1", "::1", "[::1]"].contains(host)),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              ManagementValidation.text(resource, limit: 4096), ManagementValidation.text(clientID, limit: 512),
              scopes.count <= 32, scopes.allSatisfy({ ManagementValidation.text($0, limit: 128) && !$0.contains(" ") }),
              Set(scopes).count == scopes.count else { throw MCPConfigurationError.endpoint }
        self.resource = resource; self.clientID = clientID; self.scopes = scopes
    }
    public var value: RPCValue { .object(["resource": .string(resource), "client_id": .string(clientID), "scopes": .array(scopes.map(RPCValue.string))]) }
}

public enum MCPConfigurationError: Error, LocalizedError, Equatable {
    case identifier, command, arguments, protocolVersion, endpoint

    public var errorDescription: String? {
        switch self {
        case .identifier: return "Usa un identificador de hasta 24 caracteres: una letra minúscula seguida de letras, números o guiones bajos."
        case .command: return "Elige un ejecutable mediante una ruta absoluta. No se admiten intérpretes de shell ni argumentos vacíos."
        case .arguments: return "Los argumentos deben ser un array JSON de texto, sin claves ni contraseñas. Se admiten hasta 63 argumentos, 8 KiB por elemento y 32 KiB en total."
        case .protocolVersion: return "Elige una versión del protocolo publicada por el servicio y compatible con el transporte. La versión 2024-11-05 requiere HTTP + SSE. No hay cambio automático de versión."
        case .endpoint: return "Usa una dirección HTTPS sin usuario, contraseña, consulta ni fragmento. HTTP requiere una excepción explícita para el servidor local."
        }
    }
}

public struct MCPConfiguration: Equatable, Sendable {
    public let serverID: String
    public let command: [String]
    public let protocolVersion: MCPProtocolVersion
    public let transport: MCPTransport
    public let endpoint: String?
    public let oauth: MCPOAuthConfiguration?
    public let allowLoopback: Bool

    public init(serverID: String, command: [String], protocolVersion: MCPProtocolVersion) throws {
        guard protocolVersion.supports(.stdio) else { throw MCPConfigurationError.protocolVersion }
        guard serverID.range(of: "^[a-z][a-z0-9_]{0,23}\\z", options: .regularExpression) != nil else {
            throw MCPConfigurationError.identifier
        }
        guard (1...64).contains(command.count), command.allSatisfy({ !$0.isEmpty && !$0.contains("\0") && $0.utf8.count <= 8192 }),
              command.reduce(0, { $0 + $1.utf8.count }) <= 32768 else { throw MCPConfigurationError.arguments }
        guard command[0].hasPrefix("/"), !command[0].contains("\n"), !command[0].contains("\r"),
              !["sh", "bash", "zsh", "dash", "csh", "fish"].contains(URL(fileURLWithPath: command[0]).lastPathComponent)
        else { throw MCPConfigurationError.command }
        self.serverID = serverID; self.command = command; self.protocolVersion = protocolVersion
        transport = .stdio; endpoint = nil; oauth = nil; allowLoopback = false
    }

    public init(serverID: String, endpoint: String, transport: MCPTransport, protocolVersion: MCPProtocolVersion,
                oauth: MCPOAuthConfiguration? = nil, allowLoopback: Bool = false) throws {
        guard protocolVersion.supports(transport) else { throw MCPConfigurationError.protocolVersion }
        guard serverID.range(of: "^[a-z][a-z0-9_]{0,23}$", options: .regularExpression) != nil else { throw MCPConfigurationError.identifier }
        guard transport != .stdio, ManagementValidation.text(endpoint, limit: 4096),
              let parts = URLComponents(string: endpoint), let host = parts.host?.lowercased(), !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              parts.scheme == "https" || (allowLoopback && parts.scheme == "http" && ["127.0.0.1", "::1", "[::1]"].contains(host)),
              !endpoint.contains("\r"), !endpoint.contains("\n") else { throw MCPConfigurationError.endpoint }
        self.serverID = serverID; command = []; self.endpoint = endpoint; self.transport = transport
        self.protocolVersion = protocolVersion; self.oauth = oauth; self.allowLoopback = allowLoopback
    }

    public init(spec: RPCValue) throws {
        guard let id = spec["server_id"].string, let protocolRaw = spec["protocol_version"].string,
              let version = MCPProtocolVersion(rawValue: protocolRaw) else { throw RPCFailure.invalidResponse }
        let transport = try MCPTransport.wire(spec["transport"])
        if transport == .stdio {
            guard let argv = spec["command"].array, argv.allSatisfy({ $0.string != nil }) else { throw RPCFailure.invalidResponse }
            try self.init(serverID: id, command: argv.compactMap(\.string), protocolVersion: version)
        } else {
            guard let endpoint = spec["endpoint"].string else { throw RPCFailure.invalidResponse }
            let loopback = spec["allow_loopback"].bool ?? false
            var oauth: MCPOAuthConfiguration?
            if case .null = spec["oauth"] { oauth = nil }
            else {
                guard let resource = spec["oauth"]["resource"].string, let client = spec["oauth"]["client_id"].string,
                      let scopes = spec["oauth"]["scopes"].array, scopes.allSatisfy({ $0.string != nil }) else { throw RPCFailure.invalidResponse }
                oauth = try MCPOAuthConfiguration(resource: resource, clientID: client, scopes: scopes.compactMap(\.string), allowLoopback: loopback)
            }
            try self.init(serverID: id, endpoint: endpoint, transport: transport, protocolVersion: version, oauth: oauth, allowLoopback: loopback)
        }
    }

    public init(serverID: String, executable: String, argumentsJSON: String,
                protocolVersion: MCPProtocolVersion) throws {
        guard argumentsJSON.utf8.count <= 262_144, !Self.hasTrailingComma(argumentsJSON),
              let data = argumentsJSON.data(using: .utf8),
              let arguments = try? JSONDecoder().decode([String].self, from: data) else {
            throw MCPConfigurationError.arguments
        }
        try self.init(serverID: serverID, command: [executable] + arguments, protocolVersion: protocolVersion)
    }

    // Foundation's decoder accepts a trailing array comma. The configuration
    // field promises JSON, so reject that extension before decoding its strings.
    private static func hasTrailingComma(_ json: String) -> Bool {
        var quoted = false, escaped = false
        var previous: Unicode.Scalar?
        for scalar in json.unicodeScalars {
            if quoted {
                if escaped { escaped = false }
                else if scalar == "\\" { escaped = true }
                else if scalar == "\"" { quoted = false; previous = scalar }
            } else if scalar == "\"" { quoted = true; previous = scalar }
            else if ![" ", "\n", "\r", "\t"].contains(scalar) {
                if scalar == "]", previous == "," { return true }
                previous = scalar
            }
        }
        return false
    }

    public var params: [String: RPCValue] {
        var result: [String: RPCValue] = ["server_id": .string(serverID), "command": .array(command.map(RPCValue.string)),
            "protocol_version": .string(protocolVersion.rawValue)]
        if transport != .stdio {
            result["transport"] = .string(transport.rawValue); result["endpoint"] = endpoint.map(RPCValue.string) ?? .null
            result["oauth"] = oauth?.value ?? .null; result["allow_loopback"] = .bool(allowLoopback)
        }
        return result
    }

    /// JSON escapes make whitespace and bidirectional characters visible. This is
    /// data for review, never a shell command or a request to split arguments.
    public var commandJSON: String {
        "[\n" + command.map { "  " + Self.quoted($0) }.joined(separator: ",\n") + "\n]"
    }

    private static func quoted(_ value: String) -> String {
        var result = "\""
        for scalar in value.unicodeScalars {
            switch scalar.value {
            case 34: result += "\\\""
            case 92: result += "\\\\"
            case 32...126: result.unicodeScalars.append(scalar)
            case 0...0xFFFF: result += String(format: "\\u%04x", scalar.value)
            default:
                let code = scalar.value - 0x10000
                result += String(format: "\\u%04x\\u%04x", 0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
            }
        }
        return result + "\""
    }
}

public struct MCPServerSnapshot: Identifiable, Equatable, Sendable {
    public let configuration: MCPConfiguration
    public let connected: Bool
    public let configured: Bool
    public let toolCount: Int
    public let catalogHash: String?
    public let errorCode: String?
    public var id: String { configuration.serverID }

    public init(_ value: RPCValue) throws {
        guard let id = value["server_id"].string,
              let protocolRaw = value["protocol_version"].string,
              let version = MCPProtocolVersion(rawValue: protocolRaw),
              let connected = value["connected"].bool,
              let configured = value["configured"].bool,
              let count = value["tool_count"].integer, count >= 0, count <= 10_000,
              configured else { throw RPCFailure.invalidResponse }
        do { configuration = try MCPConfiguration(spec: value) }
        catch { throw RPCFailure.invalidResponse }
        let hash = value["catalog_hash"]
        if case .null = hash { catalogHash = nil }
        else if let text = hash.string, text.utf8.count == 64,
                text.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) { catalogHash = text }
        else { throw RPCFailure.invalidResponse }
        guard !connected || catalogHash != nil,
              connected || count == 0 else { throw RPCFailure.invalidResponse }
        let error = value["error"]
        if case .null = error { errorCode = nil }
        else if let text = error.string, text.range(of: "^[a-z][a-z0-9_]{0,127}\\z", options: .regularExpression) != nil {
            errorCode = text
        } else { throw RPCFailure.invalidResponse }
        self.connected = connected; self.configured = configured; toolCount = count
    }
}

private extension MCPTransport {
    static func wire(_ value: RPCValue) throws -> Self {
        if case .null = value { return .stdio }
        guard let text = value.string, let kind = Self(rawValue: text) else { throw RPCFailure.invalidResponse }
        return kind
    }
}

public struct MCPCatalog: Equatable, Sendable {
    public let servers: [MCPServerSnapshot]
    public let supportedProtocols: [MCPProtocolVersion]

    public init(_ value: RPCValue) throws {
        guard let entries = value["servers"].array, entries.count <= 64,
              let versions = value["supported_protocols"].array, !versions.isEmpty,
              versions.allSatisfy({ $0.string.flatMap(MCPProtocolVersion.init(rawValue:)) != nil })
        else { throw RPCFailure.invalidResponse }
        servers = try entries.map(MCPServerSnapshot.init)
        supportedProtocols = versions.compactMap { $0.string.flatMap(MCPProtocolVersion.init(rawValue:)) }
        guard Set(servers.map(\.id)).count == servers.count,
              Set(supportedProtocols).count == supportedProtocols.count,
              servers.allSatisfy({ supportedProtocols.contains($0.configuration.protocolVersion) })
        else { throw RPCFailure.invalidResponse }
    }
}
