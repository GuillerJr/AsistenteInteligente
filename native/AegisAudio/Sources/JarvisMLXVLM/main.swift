import AegisAudioCore
import CoreImage
import Darwin
import Foundation
import MLX
import MLXHuggingFace
import MLXLMCommon
import MLXVLM
import Tokenizers

private struct VisionRequest: Decodable {
    let protocolVersion: String
    let requestID: UUID
    let task: String
    let targetDescription: String
    let expectedX: Double
    let expectedY: Double
    let imagePNGBase64: String

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case task
        case targetDescription = "target_description"
        case expectedX = "expected_x"
        case expectedY = "expected_y"
        case imagePNGBase64 = "image_png_base64"
    }
}

private struct ModelDecision: Codable {
    let offsetX: Double
    let offsetY: Double
    let confidence: Double
    let targetFound: Bool

    enum CodingKeys: String, CodingKey {
        case offsetX = "offset_x"
        case offsetY = "offset_y"
        case confidence
        case targetFound = "target_found"
    }
}

private struct VisionResponse: Encodable {
    let protocolVersion = "1.0"
    let requestID: UUID
    let success: Bool
    let decision: ModelDecision?
    let errorCode: String?

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case requestID = "request_id"
        case success
        case decision
        case errorCode = "error_code"
    }
}

private enum VLMFailure: Error {
    case configuration
    case invalidFrame
    case invalidRequest
    case invalidImage
    case invalidDecision
}

@main
private enum JarvisMLXVLM {
    static func main() async {
        let requestID = UUID()
        do {
            let request = try readRequest()
            let response = try await analyze(request)
            try writeResponse(response)
        } catch {
            let code: String = switch error {
            case VLMFailure.configuration: "vlm_configuration_invalid"
            case VLMFailure.invalidFrame: "vlm_frame_invalid"
            case VLMFailure.invalidRequest: "vlm_request_invalid"
            case VLMFailure.invalidImage: "vlm_image_invalid"
            case VLMFailure.invalidDecision: "vlm_decision_invalid"
            default: "vlm_inference_failed"
            }
            try? writeResponse(
                VisionResponse(
                    requestID: requestID,
                    success: false,
                    decision: nil,
                    errorCode: code
                )
            )
            exit(EXIT_FAILURE)
        }
    }

    private static func analyze(_ request: VisionRequest) async throws -> VisionResponse {
        guard
            request.protocolVersion == "1.0",
            request.task == "localized_click_offset",
            !request.targetDescription.isEmpty,
            request.targetDescription.utf8.count <= 512,
            request.expectedX.isFinite,
            request.expectedY.isFinite,
            (0 ..< 120).contains(request.expectedX),
            (0 ..< 120).contains(request.expectedY),
            let imageData = Data(base64Encoded: request.imagePNGBase64),
            imageData.count <= OnDemandVisionCapture.maximumEncodedBytes,
            let image = CIImage(data: imageData),
            image.extent.width == 120,
            image.extent.height == 120
        else {
            throw VLMFailure.invalidRequest
        }
        guard
            let modelDirectory = ProcessInfo.processInfo.environment["AEGIS_MLX_VLM_DIRECTORY"],
            modelDirectory.hasPrefix("/"),
            FileManager.default.fileExists(atPath: modelDirectory)
        else {
            throw VLMFailure.configuration
        }
        let container = try await VLMModelFactory.shared.loadContainer(
            from: URL(fileURLWithPath: modelDirectory, isDirectory: true),
            using: #huggingFaceTokenizerLoader()
        )
        let session = ChatSession(
            container,
            instructions: """
            You inspect one 120 by 120 pixel UI crop. Return one JSON object only. Locate the requested clickable target relative to the expected point. Never infer coordinates outside the crop. Schema: {"offset_x":number,"offset_y":number,"confidence":number,"target_found":boolean}. Confidence must be 0 through 1.
            """,
            generateParameters: GenerateParameters(maxTokens: 96, temperature: 0)
        )
        let raw = try await session.respond(
            to: "Target: \(request.targetDescription). Expected point: (\(request.expectedX), \(request.expectedY)).",
            image: .ciImage(image)
        )
        await session.clear()
        Memory.clearCache()
        let decision = try decodeDecision(raw)
        guard
            decision.offsetX.isFinite,
            decision.offsetY.isFinite,
            decision.confidence.isFinite,
            (-60 ... 60).contains(decision.offsetX),
            (-60 ... 60).contains(decision.offsetY),
            (0 ... 1).contains(decision.confidence)
        else {
            throw VLMFailure.invalidDecision
        }
        return VisionResponse(
            requestID: request.requestID,
            success: true,
            decision: decision,
            errorCode: nil
        )
    }

    private static func decodeDecision(_ raw: String) throws -> ModelDecision {
        guard
            let start = raw.firstIndex(of: "{"),
            let end = raw.lastIndex(of: "}"),
            start <= end,
            let data = String(raw[start ... end]).data(using: .utf8)
        else {
            throw VLMFailure.invalidDecision
        }
        do {
            return try JSONDecoder().decode(ModelDecision.self, from: data)
        } catch {
            throw VLMFailure.invalidDecision
        }
    }

    private static func readRequest() throws -> VisionRequest {
        let input = FileHandle.standardInput
        let header = try input.read(upToCount: 4) ?? Data()
        guard header.count == 4 else { throw VLMFailure.invalidFrame }
        let length = header.reduce(UInt32.zero) { ($0 << 8) | UInt32($1) }
        guard length > 0, length <= 262_144 else { throw VLMFailure.invalidFrame }
        let body = try input.read(upToCount: Int(length)) ?? Data()
        guard body.count == Int(length) else { throw VLMFailure.invalidFrame }
        do {
            return try JSONDecoder().decode(VisionRequest.self, from: body)
        } catch {
            throw VLMFailure.invalidRequest
        }
    }

    private static func writeResponse(_ response: VisionResponse) throws {
        let body = try JSONEncoder().encode(response)
        guard !body.isEmpty, body.count <= 65_536 else { throw VLMFailure.invalidFrame }
        var length = UInt32(body.count).bigEndian
        var frame = Data(bytes: &length, count: 4)
        frame.append(body)
        try FileHandle.standardOutput.write(contentsOf: frame)
    }
}
