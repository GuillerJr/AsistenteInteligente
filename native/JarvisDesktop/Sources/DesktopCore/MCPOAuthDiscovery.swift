import Foundation

/// Public discovery metadata only. An issuer catalog is never an authorization
/// choice: choose_issuer deliberately exposes no selected issuer.
public struct MCPOAuthDiscovery: Equatable, Sendable {
    public let resource: String
    public let issuers: [String]
    public let issuer: String?
    public let requiresSelection: Bool
    public init(_ value: RPCValue, allowLoopback: Bool = false) throws {
        guard let fields = value.object,
              Set(fields.keys).isSubset(of: ["state", "resource", "issuer", "issuers", "scopes", "supported_scopes",
                "authorization_endpoint", "token_endpoint", "cimd_supported", "registration_supported"]),
              let state = value["state"].string, ["ready", "choose_issuer"].contains(state),
              let resource = value["resource"].string else { throw RPCFailure.invalidResponse }
        func endpoint(_ text: String) -> Bool {
            guard ManagementValidation.text(text, limit: 4096), let url = URLComponents(string: text), let host = url.host,
                  url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { return false }
            return url.scheme == "https" || (allowLoopback && url.scheme == "http" && ["127.0.0.1", "::1", "[::1]"].contains(host))
        }
        guard endpoint(resource) else { throw RPCFailure.invalidResponse }
        self.resource = resource
        requiresSelection = state == "choose_issuer"
        if requiresSelection {
            guard value["issuer"] == .null, let values = value["issuers"].array, (2...8).contains(values.count),
                  values.allSatisfy({ $0.string.map(endpoint) == true }) else { throw RPCFailure.invalidResponse }
            issuers = values.compactMap(\.string)
            guard Set(issuers).count == issuers.count else { throw RPCFailure.invalidResponse }
            issuer = nil
        } else {
            guard let selected = value["issuer"].string, endpoint(selected) else { throw RPCFailure.invalidResponse }
            issuer = selected; issuers = [selected]
        }
    }
    public func validatesSelection(_ selected: String) -> Bool {
        !requiresSelection && issuer == selected && !selected.isEmpty
    }
}
