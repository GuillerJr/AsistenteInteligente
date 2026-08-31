import CoreSpotlight
import Foundation
import UniformTypeIdentifiers

public struct SpotlightGraphItem: Codable, Sendable, Equatable {
    public let identifier: String
    public let domainIdentifier: String
    public let nodeName: String
    public let nodeType: String
    public let relationshipSummary: String
    public let edgeTypes: [String]
    public let contentDigest: String

    enum CodingKeys: String, CodingKey {
        case identifier
        case domainIdentifier = "domain_identifier"
        case nodeName = "node_name"
        case nodeType = "node_type"
        case relationshipSummary = "relationship_summary"
        case edgeTypes = "edge_types"
        case contentDigest = "content_digest"
    }
}

public enum SpotlightIndexerError: Error, Sendable, Equatable {
    case invalidItem
    case batchTooLarge
    case indexingFailed
}

public actor SpotlightIndexer {
    public static let indexName = "ai.aegis.graphrag"
    public static let maximumBatchSize = 100

    private let index: CSSearchableIndex

    public init() {
        index = CSSearchableIndex(
            name: Self.indexName,
            protectionClass: FileProtectionType.complete
        )
    }

    public func indexItems(_ items: [SpotlightGraphItem]) async throws {
        guard !items.isEmpty, items.count <= Self.maximumBatchSize else {
            throw SpotlightIndexerError.batchTooLarge
        }
        let searchable = try items.map(Self.searchableItem)
        try await withCheckedThrowingContinuation { continuation in
            index.indexSearchableItems(searchable) { error in
                if error == nil {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: SpotlightIndexerError.indexingFailed)
                }
            }
        }
    }

    public func deleteItems(identifiers: [String]) async throws {
        guard
            !identifiers.isEmpty,
            identifiers.count <= Self.maximumBatchSize,
            identifiers.allSatisfy(Self.validIdentifier)
        else {
            throw SpotlightIndexerError.invalidItem
        }
        try await withCheckedThrowingContinuation { continuation in
            index.deleteSearchableItems(withIdentifiers: identifiers) { error in
                if error == nil {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: SpotlightIndexerError.indexingFailed)
                }
            }
        }
    }

    public func resetGraphDomain() async throws {
        try await withCheckedThrowingContinuation { continuation in
            index.deleteSearchableItems(
                withDomainIdentifiers: ["ai.aegis.graphrag"]
            ) { error in
                if error == nil {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: SpotlightIndexerError.indexingFailed)
                }
            }
        }
    }

    private static func searchableItem(_ item: SpotlightGraphItem) throws -> CSSearchableItem {
        guard
            validIdentifier(item.identifier),
            item.domainIdentifier == "ai.aegis.graphrag",
            (1 ... 256).contains(item.nodeName.utf8.count),
            item.nodeType.range(
                of: #"^[a-z][a-z0-9_]{1,31}$"#,
                options: .regularExpression
            ) != nil,
            item.relationshipSummary.utf8.count <= 2_048,
            item.edgeTypes.count <= 32,
            item.edgeTypes.allSatisfy({
                $0.range(of: #"^[A-Z][A-Z0-9_]{1,63}$"#, options: .regularExpression) != nil
            }),
            item.contentDigest.range(
                of: #"^[0-9a-f]{64}$"#,
                options: .regularExpression
            ) != nil
        else {
            throw SpotlightIndexerError.invalidItem
        }
        let attributes = CSSearchableItemAttributeSet(contentType: .data)
        attributes.title = item.nodeName
        attributes.displayName = item.nodeName
        attributes.contentDescription = item.relationshipSummary
        attributes.keywords = (
            ["aegis_graph", "aegis:type:\(item.nodeType)"]
                + item.edgeTypes.map { "aegis:edge:\($0.lowercased())" }
        ).sorted()
        attributes.relatedUniqueIdentifier = item.contentDigest
        let searchable = CSSearchableItem(
            uniqueIdentifier: item.identifier,
            domainIdentifier: item.domainIdentifier,
            attributeSet: attributes
        )
        searchable.expirationDate = nil
        return searchable
    }

    private static func validIdentifier(_ value: String) -> Bool {
        value.range(
            of: #"^aegis-graph-[0-9a-f-]{36}$"#,
            options: .regularExpression
        ) != nil
    }
}
