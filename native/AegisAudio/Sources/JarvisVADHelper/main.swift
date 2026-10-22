import AegisVADRuntime
import Darwin
import Foundation

// A deliberately small binary protocol. Never access the daemon, network or user files.
// One 512-sample frame follows a UInt32 little-endian count; zero resets the model.
func readExactly(_ count: Int) -> Data? {
    var data = Data()
    while data.count < count {
        let part = FileHandle.standardInput.readData(ofLength: count - data.count)
        guard !part.isEmpty else { return nil }
        data.append(part)
    }
    return data
}

do {
    let runtime: SileroVADRuntime
    if CommandLine.arguments.count == 3, CommandLine.arguments[1] == "--model" {
        runtime = try SileroVADRuntime(modelURL: URL(fileURLWithPath: CommandLine.arguments[2]))
    } else if CommandLine.arguments.count == 1 {
        runtime = try SileroVADRuntime()
    } else {
        exit(64)
    }
    try FileHandle.standardOutput.write(contentsOf: Data("VAD1".utf8))
    while let header = readExactly(4) {
        let count = header.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self)) }
        var probability: Float
        if count == 0 {
            runtime.reset()
            probability = 0
        } else {
            guard count == 512, let data = readExactly(2048) else { exit(65) }
            let samples = data.withUnsafeBytes { bytes in
                (0..<512).map { Float(bitPattern: UInt32(littleEndian:
                    bytes.loadUnaligned(fromByteOffset: $0 * 4, as: UInt32.self))) }
            }
            guard samples.allSatisfy(\.isFinite) else { exit(65) }
            probability = try runtime.infer(chunk: samples)
        }
        var bits = probability.bitPattern.littleEndian
        try withUnsafeBytes(of: &bits) { try FileHandle.standardOutput.write(contentsOf: Data($0)) }
    }
} catch {
    // Do not print audio, file names or protocol data in the shared app log.
    exit(70)
}
