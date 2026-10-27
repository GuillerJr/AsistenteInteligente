import Foundation
import Observation

@MainActor @Observable public final class SessionCoreStore {
    public private(set) var scope: SessionScope?
    public private(set) var snapshot: SessionContextSnapshot?
    public private(set) var result: SessionCoreOperation?
    public private(set) var operations: [SessionCoreOperation] = []
    public private(set) var hasMoreOperations = false
    public private(set) var isRequesting = false
    public private(set) var responseUnconfirmed = false
    public private(set) var errorMessage: String?
    public private(set) var pendingOperationID: String?
    public private(set) var pendingKind: String?
    @ObservationIgnored private var client: SessionCoreClient?
    @ObservationIgnored private var version = 0
    public var blocksInteraction: Bool { isRequesting || responseUnconfirmed }
    public init() {}

    public func configure(_ transport: LocalRPCClient) { configure(SessionCoreClient(transport: transport)) }
    public func configure(_ client: SessionCoreClient) {
        version += 1; self.client = client; scope = nil; snapshot = nil; result = nil
        operations = []; hasMoreOperations = false; isRequesting = false; responseUnconfirmed = false; errorMessage = nil
        pendingOperationID = nil; pendingKind = nil
    }
    public func setScope(_ scope: SessionScope?) {
        guard !blocksInteraction, self.scope != scope else { return }
        version += 1; self.scope = scope; snapshot = nil; result = nil; operations = []; hasMoreOperations = false; errorMessage = nil
        pendingOperationID = nil; pendingKind = nil
    }
    public func close() async {
        version += 1
        await client?.close()
        client = nil; isRequesting = false
    }
    public func refresh() async {
        guard let client, let scope, !isRequesting else { return }
        let current = version; isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            if responseUnconfirmed, let id = pendingOperationID, let kind = pendingKind {
                let observed = try await client.operation(scope, id: id, kind: kind)
                guard current == version, !Task.isCancelled else { return }
                if let observed { result = observed; responseUnconfirmed = false }
            }
            let observed = try await client.context(scope)
            guard current == version, !Task.isCancelled else { return }
            let page = try await client.operations(scope)
            guard current == version, !Task.isCancelled else { return }
            operations = page.operations; hasMoreOperations = page.hasMore
            snapshot = observed; errorMessage = responseUnconfirmed
                ? "No se encontró el recibo de esta operación. Se conserva sin confirmar; no se repetirá automáticamente."
                : nil
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func loadMoreOperations() async {
        guard let client, let scope, !isRequesting, hasMoreOperations else { return }
        let current = version; isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            let page = try await client.operations(scope, offset: operations.count)
            guard current == version, !Task.isCancelled else { return }
            guard Set((operations + page.operations).map(\.id)).count == operations.count + page.operations.count else { throw RPCFailure.invalidResponse }
            operations += page.operations; hasMoreOperations = page.hasMore
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func inspect(_ operation: SessionCoreOperation) async {
        guard let client, let scope, !blocksInteraction, operations.contains(where: { $0.id == operation.id }) else { return }
        let current = version; isRequesting = true
        defer { if current == version { isRequesting = false } }
        do {
            let observed = try await client.operation(scope, id: operation.id, kind: operation.kind)
            guard current == version, !Task.isCancelled else { return }
            guard let observed else { throw RPCFailure.invalidResponse }; result = observed; errorMessage = nil
        } catch { if current == version { errorMessage = MaintenancePresentation.error(error) } }
    }
    public func compact(decisions: [String]) async {
        guard let client, let scope, let snapshot, snapshot.revision > 0, !blocksInteraction else { return }
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let current = begin(id: id, kind: "compact")
        defer { if current == version { isRequesting = false } }
        do {
            let observed = try await client.compact(scope, revision: snapshot.revision, operationID: id, decisions: decisions)
            guard current == version else { return }
            self.snapshot = observed.context; result = observed; errorMessage = nil
        } catch { failed(error, version: current) }
    }
    public func rewind(until: Int, title: String) async {
        guard let client, let scope, let snapshot, !blocksInteraction else { return }
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let current = begin(id: id, kind: "rewind")
        defer { if current == version { isRequesting = false } }
        do {
            let observed = try await client.rewind(scope, revision: snapshot.revision, until: until, operationID: id, title: title)
            guard current == version else { return }
            result = observed; errorMessage = nil; self.snapshot = nil
        } catch { failed(error, version: current) }
    }
    public func fork(title: String) async {
        guard let client, let scope, let snapshot, !blocksInteraction else { return }
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        let current = begin(id: id, kind: "fork")
        defer { if current == version { isRequesting = false } }
        do {
            let observed = try await client.fork(scope, revision: snapshot.revision, operationID: id, title: title)
            guard current == version else { return }
            result = observed; errorMessage = nil; self.snapshot = nil
        } catch { failed(error, version: current) }
    }
    /// This only retires a local uncertainty warning after an explicit choice.
    /// It neither sends another mutation nor claims the original acknowledgement.
    public func retainUnconfirmed() {
        guard !isRequesting, responseUnconfirmed else { return }
        responseUnconfirmed = false; snapshot = nil
        errorMessage = "Resultado conservado sin confirmar. La operación anterior no se reenviará."
    }
    private func begin(id: String, kind: String) -> Int {
        isRequesting = true; result = nil; errorMessage = nil
        pendingOperationID = id; pendingKind = kind
        return version
    }
    private func failed(_ error: Error, version current: Int) {
        guard current == version else { return }
        snapshot = nil
        responseUnconfirmed = MaintenanceResponsePolicy.submissionMayBeUnconfirmed(error)
        errorMessage = MaintenancePresentation.error(error)
    }
}
