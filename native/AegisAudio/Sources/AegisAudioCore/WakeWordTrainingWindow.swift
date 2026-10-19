import Foundation

public enum WakeWordTrainingWindow {
    public static func sampleRange(
        frameRMS: [Double],
        framesPerRMS: Int,
        sampleCount: Int,
        sampleRate: Double
    ) -> Range<Int>? {
        guard sampleRate.isFinite, (8_000 ... 48_000).contains(sampleRate),
              sampleCount > 0, sampleCount <= Int(sampleRate * 3),
              framesPerRMS > 0, framesPerRMS <= Int(sampleRate * 0.1),
              frameRMS.count == (sampleCount + framesPerRMS - 1) / framesPerRMS,
              frameRMS.allSatisfy({ $0.isFinite && (0 ... 1).contains($0) })
        else { return nil }
        let sorted = frameRMS.sorted()
        let noise = sorted[Int(Double(sorted.count - 1) * 0.2)]
        let threshold = max(noise * 3, (sorted.last ?? 0) * 0.1)
        let active = frameRMS.indices.filter { frameRMS[$0] > threshold }
        guard let first = active.first, let last = active.last else {
            // Retain ambiguous samples rather than selecting them out of validation.
            return 0 ..< sampleCount
        }
        let speechStart = first * framesPerRMS
        let speechEnd = min((last + 1) * framesPerRMS, sampleCount)
        let length = min(sampleCount, max(Int(sampleRate),
                                          speechEnd - speechStart + Int(sampleRate * 0.16)))
        let start = min(max(0, (speechStart + speechEnd - length) / 2), sampleCount - length)
        return start ..< start + length
    }
}
