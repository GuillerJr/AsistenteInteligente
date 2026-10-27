import Foundation

/// One explicit local search. It does not enable automatic recall or submit a provider turn.
public struct MemoryManualSearchRequest: Sendable {
    public let scope: SessionScope
    public let query: String
    public let mode: MemoryRecallMode
    public init(scope: SessionScope, query: String, mode: MemoryRecallMode) throws {
        guard ManagementValidation.identifier(scope.sessionID), !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              query.utf8.count <= 512, !query.contains("\0") else { throw RPCFailure.configuration }
        self.scope = scope; self.query = query; self.mode = mode
    }
    public var params: [String: RPCValue] {
        scope.params.merging(["query": .string(query), "mode": .string(mode.rawValue), "offset": .number(0)], uniquingKeysWith: { _, next in next })
    }
}

public struct MemoryManualSearchResponse: Sendable {
    public let matches: [MemoryMatch]
    public let mode: String
    public let generation: Int
    public let hasMore: Bool
    public let indexFingerprint: String?
    public init(_ value: RPCValue, requested: MemoryRecallMode) throws {
        var keys: Set<String> = ["matches", "mode", "generation", "next_offset", "has_more"]
        if requested != .lexical { keys.insert("index_fingerprint") }
        try MaintenanceValidation.keys(value, keys)
        indexFingerprint = requested == .lexical ? nil : try MemoryIntelligenceValidation.hash(value["index_fingerprint"])
        guard let mode = value["mode"].string,
              (requested == .lexical ? ["fts5_lexical", "lexical_scan"].contains(mode) : mode == requested.rawValue),
              let more = value["has_more"].bool, let rows = value["matches"].array, rows.count <= 10 else { throw RPCFailure.invalidResponse }
        matches = try rows.map { row in
            guard case .number(let score) = row["score"], score.isFinite else { throw RPCFailure.invalidResponse }
            return try MemoryMatch(row)
        }
        generation = try MaintenanceValidation.integer(value["generation"])
        let next = try MaintenanceValidation.integer(value["next_offset"], max: 100_000)
        guard next == matches.count, !more || !matches.isEmpty,
              Set(matches.map(\.id)).count == matches.count else { throw RPCFailure.invalidResponse }
        self.mode = mode; hasMore = more
    }
}

public enum MemoryManualSearchPresentation {
    public static let queryTooLarge = "La consulta supera 512 bytes. Acórtala antes de buscar; no se recortó ni se envió."
    public static func error(_ error: Error) -> String {
        guard let failure = error as? RPCFailure, case .remote(_, let code) = failure else {
            return CapabilityPresentation.errorMessage(error)
        }
        if code == "invalid_query" || code == "query_capacity" || code == "query_limit" {
            return "La consulta excede la longitud o la cantidad de términos admitida. Acórtala y vuelve a buscar; no se cambió de modo."
        }
        if ["missing_model", "invalid_model", "unavailable_runtime"].contains(code) {
            return "El motor local no está disponible. Revisa Uso automático y prepara el modelo; no se usó otro proveedor ni modo."
        }
        if code.contains("cleanup_pending") {
            return "El motor local todavía está cerrando recursos. Espera y actualiza su estado antes de buscar."
        }
        if code.contains("capacity") || code.contains("limit") {
            return "La búsqueda alcanzó su límite de capacidad. Reduce la consulta o revisa el índice; no se cambió de modo."
        }
        if code.contains("index") || code == "search_changed" {
            return "El índice no está vigente o disponible. Revisa su estado y reconstruye si corresponde; no se usó una búsqueda alternativa."
        }
        return CapabilityPresentation.errorMessage(error)
    }
}
