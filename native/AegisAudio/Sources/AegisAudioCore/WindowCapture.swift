import CoreGraphics
import Foundation

public struct WindowGeometryCandidate: Equatable, Sendable {
    public let index: Int
    public let frame: CGRect

    public init(index: Int, frame: CGRect) {
        self.index = index
        self.frame = frame
    }
}

public enum WindowCapturePlan {
    public static let minimumIntersectionRatio: CGFloat = 0.70
    public static let maximumEdgeDelta: CGFloat = 12

    public static func select(
        accessibilityFrame: CGRect,
        candidates: [WindowGeometryCandidate]
    ) -> Int? {
        guard
            accessibilityFrame.width >= 1,
            accessibilityFrame.height >= 1,
            accessibilityFrame.isFinite,
            !candidates.isEmpty
        else {
            return nil
        }
        let targetArea = accessibilityFrame.width * accessibilityFrame.height
        let ranked = candidates.compactMap { candidate -> (Int, CGFloat, CGFloat)? in
            guard
                candidate.frame.width >= 1,
                candidate.frame.height >= 1,
                candidate.frame.isFinite
            else {
                return nil
            }
            let intersection = accessibilityFrame.intersection(candidate.frame)
            let intersectionArea = intersection.isNull
                ? 0
                : intersection.width * intersection.height
            let candidateArea = candidate.frame.width * candidate.frame.height
            let unionArea = targetArea + candidateArea - intersectionArea
            let intersectionOverUnion = unionArea > 0 ? intersectionArea / unionArea : 0
            let edgeDelta = abs(accessibilityFrame.minX - candidate.frame.minX)
                + abs(accessibilityFrame.minY - candidate.frame.minY)
                + abs(accessibilityFrame.maxX - candidate.frame.maxX)
                + abs(accessibilityFrame.maxY - candidate.frame.maxY)
            guard
                intersectionOverUnion >= minimumIntersectionRatio
                    || edgeDelta <= maximumEdgeDelta * 4
            else {
                return nil
            }
            return (candidate.index, intersectionOverUnion, edgeDelta)
        }
        return ranked.sorted { left, right in
            if left.1 != right.1 { return left.1 > right.1 }
            if left.2 != right.2 { return left.2 < right.2 }
            return left.0 < right.0
        }.first?.0
    }

    public static func pixelDimensions(
        frame: CGRect,
        displayBounds: CGRect,
        displayPixelWidth: Int,
        displayPixelHeight: Int
    ) -> (width: Int, height: Int)? {
        guard
            frame.width >= 1,
            frame.height >= 1,
            displayBounds.width >= 1,
            displayBounds.height >= 1,
            displayPixelWidth > 0,
            displayPixelHeight > 0
        else {
            return nil
        }
        let scaleX = CGFloat(displayPixelWidth) / displayBounds.width
        let scaleY = CGFloat(displayPixelHeight) / displayBounds.height
        let width = Int((frame.width * scaleX).rounded(.up))
        let height = Int((frame.height * scaleY).rounded(.up))
        guard
            (1 ... 16_384).contains(width),
            (1 ... 16_384).contains(height),
            Int64(width) * Int64(height) <= 100_000_000
        else {
            return nil
        }
        return (width, height)
    }
}

private extension CGRect {
    var isFinite: Bool {
        [minX, minY, width, height].allSatisfy(\.isFinite)
    }
}
