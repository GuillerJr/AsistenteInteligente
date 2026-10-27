import Foundation

public struct RunInputReply: Equatable, Sendable {
    public let runID: String
    public let requestID: String
    public let status: String
    public init(queued value: RPCValue, requestID: String) throws {
        guard ManagementValidation.identifier(requestID), value["run_id"].string == requestID,
              let status = value["status"].string, ["queued", "running"].contains(status) else { throw RPCFailure.invalidResponse }
        runID = requestID; self.requestID = requestID; self.status = status
    }
    public init(steering value: RPCValue, runID: String, requestID: String) throws {
        guard ManagementValidation.identifier(runID), ManagementValidation.identifier(requestID),
              value["run_id"].string == runID, value["request_id"].string == requestID,
              let status = value["status"].string, ["accepted", "unknown"].contains(status),
              value["permits_replay"].bool == false else { throw RPCFailure.invalidResponse }
        self.runID = runID; self.requestID = requestID; self.status = status
    }
}
