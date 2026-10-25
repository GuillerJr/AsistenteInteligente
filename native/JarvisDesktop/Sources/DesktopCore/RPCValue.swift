import Foundation

public enum RPCValue: Codable, Equatable, Sendable {
    case object([String: RPCValue]), array([RPCValue]), string(String)
    case number(Double), bool(Bool), null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode(Double.self), value.isFinite {
            self = .number(value)
        } else if let value = try? container.decode([String: RPCValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([RPCValue].self) { self = .array(value) }
        else { throw RPCFailure.invalidResponse }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    public subscript(_ key: String) -> RPCValue { object?[key] ?? .null }
    public var object: [String: RPCValue]? { if case .object(let v) = self { return v }; return nil }
    public var array: [RPCValue]? { if case .array(let v) = self { return v }; return nil }
    public var string: String? { if case .string(let v) = self { return v }; return nil }
    public var bool: Bool? { if case .bool(let v) = self { return v }; return nil }
    public var integer: Int? {
        if case .number(let v) = self, v.isFinite, v.rounded() == v,
           abs(v) <= 9_007_199_254_740_991 { return Int(v) }
        return nil
    }
}

public enum RPCFailure: Error, LocalizedError, Equatable, Sendable {
    case configuration, insecurePath, unavailable, disconnected, invalidResponse, oversized, serviceExited, startupUnconfirmed
    case remote(Int, String)

    public var errorDescription: String? {
        switch self {
        case .configuration: return "Falta la configuración explícita de desarrollo de Jarvis."
        case .insecurePath: return "El transporte local no cumple los permisos de seguridad."
        case .unavailable: return "El servicio local no está disponible."
        case .serviceExited: return "El servicio terminó durante el inicio. Comprueba si otra instancia usa este perfil."
        case .startupUnconfirmed: return "El arranque del servicio local aún no está confirmado. Jarvis seguirá consultando su estado."
        case .disconnected: return "La conexión se interrumpió. La operación no se repetirá automáticamente."
        case .invalidResponse: return "El servicio devolvió una respuesta no válida."
        case .oversized: return "El mensaje supera el límite permitido."
        case .remote(_, let message): return message
        }
    }
}

public enum RPCFrame {
    public static let limit = 1_048_576

    public static func request(id: Int, method: String, params: [String: RPCValue],
                               token: String) throws -> Data {
        guard id > 0, id <= 9_007_199_254_740_991,
              method.range(of: "^[a-z][a-z0-9_.]{0,95}$", options: .regularExpression) != nil,
              token.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
        else { throw RPCFailure.configuration }
        let value = RPCValue.object(["jsonrpc": .string("2.0"), "id": .number(Double(id)),
                                     "method": .string(method), "params": .object(params),
                                     "auth": .string(token)])
        var data = try JSONEncoder().encode(value)
        data.append(10)
        guard data.count <= limit else { throw RPCFailure.oversized }
        return data
    }

    public static func response(_ frame: Data, expectedID: Int) throws -> RPCValue {
        guard frame.count <= limit else { throw RPCFailure.oversized }
        guard frame.last == 10, !frame.dropLast().contains(10) else {
            throw RPCFailure.invalidResponse
        }
        let value = try JSONDecoder().decode(RPCValue.self, from: frame)
        guard let object = value.object, value["jsonrpc"] == .string("2.0"),
              value["id"].integer == expectedID else { throw RPCFailure.invalidResponse }
        if let result = object["result"], Set(object.keys) == ["jsonrpc", "id", "result"] {
            return result
        }
        guard Set(object.keys) == ["jsonrpc", "id", "error"],
              let error = value["error"].object, Set(error.keys) == ["code", "message"],
              let code = value["error"]["code"].integer,
              let message = value["error"]["message"].string else {
            throw RPCFailure.invalidResponse
        }
        throw RPCFailure.remote(code, message)
    }
}
