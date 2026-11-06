import Foundation

/// One entry of the official provider's visible model catalog. Listing it is
/// information only: it neither selects a model nor grants a capability.
public struct ProviderModel: Equatable, Identifiable, Sendable {
    public let id: String
    public let displayName: String
    public let isDefault: Bool
    public let acceptsImages: Bool

    public init?(_ value: RPCValue) {
        guard let id = value["id"].string, !id.isEmpty, id.utf8.count <= 80,
              let name = value["display_name"].string, !name.isEmpty, name.count <= 128,
              let isDefault = value["is_default"].bool,
              let acceptsImages = value["accepts_images"].bool else { return nil }
        self.id = id
        displayName = name
        self.isDefault = isDefault
        self.acceptsImages = acceptsImages
    }

    /// Rejects the whole `provider.models` reply when any row is malformed or duplicated.
    public static func catalog(_ result: RPCValue) -> [ProviderModel]? {
        guard let connected = result["connected"].bool, let rows = result["models"].array,
              rows.count <= 800 else { return nil }
        let models = rows.compactMap(ProviderModel.init)
        guard models.count == rows.count, Set(models.map(\.id)).count == models.count,
              connected || models.isEmpty else { return nil }
        return models
    }
}
