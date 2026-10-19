import CoreGraphics
import Foundation

public struct MascotPoint: Codable, Equatable, Sendable {
    public var x: Double
    public var y: Double
    public init(x: Double = 0.5, y: Double = 0.5) { self.x = x; self.y = y }
    public var isValid: Bool { x.isFinite && y.isFinite && (0...1).contains(x) && (0...1).contains(y) }
    public func distance(to other: Self) -> Double { hypot(x - other.x, y - other.y) }
}

public enum MascotIntent: String, Codable, Sendable {
    case explore, approach, observe, rest, attend
}

public struct MascotIntentPlan: Codable, Equatable, Sendable {
    public let intent: MascotIntent
    public let targets: [MascotPoint]
    public let speed: Double
    public let durationSeconds: Double
    public init(intent: MascotIntent, targets: [MascotPoint], speed: Double = 24,
                durationSeconds: Double = 45) {
        self.intent = intent; self.targets = targets; self.speed = speed
        self.durationSeconds = durationSeconds
    }
    public var isValid: Bool {
        (1...3).contains(targets.count) && targets.allSatisfy(\.isValid)
            && speed.isFinite && (5...45).contains(speed)
            && durationSeconds.isFinite && (10...90).contains(durationSeconds)
    }
    enum CodingKeys: String, CodingKey {
        case intent, targets, speed
        case durationSeconds = "duration_seconds"
    }
}

/// Nonsensitive physical memory; no conversation, permissions or executable plan.
public struct MascotLifeMemory: Codable, Equatable, Sendable {
    public var energy = 0.85
    public var curiosity = 0.7
    public var affinity = 0.4
    public var lastIntent: MascotIntent = .observe
    public var visits: [MascotPoint] = []
    public init() {}
    public var isValid: Bool {
        [energy, curiosity, affinity].allSatisfy { $0.isFinite && (0...1).contains($0) }
            && visits.count <= 6 && visits.allSatisfy(\.isValid)
    }
    enum CodingKeys: String, CodingKey {
        case energy, curiosity, affinity, visits
        case lastIntent = "last_intent"
    }
}

public struct MascotRoom: Equatable, Sendable {
    public let width: Double
    public let height: Double
    public let expansion: Double
    public var bodySize: Double { min(36 + 48 * expansion, min(width, height)) }
    public var reservedBottom: Double { min(44 * expansion, max(0, height - bodySize - 4)) }
    public var centerBounds: CGRect {
        let margin = 2 + 10 * expansion
        let half = bodySize / 2
        let minX = min(width / 2, half + margin)
        let maxX = max(minX, width - half - margin)
        let minY = min((height - reservedBottom) / 2, half + 2 + 12 * expansion)
        let maxY = max(minY, height - reservedBottom - margin - half)
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }
    public init(width: Double, height: Double, expansion: Double) {
        self.width = width.isFinite ? min(max(width, 1), 2_000) : 1
        self.height = height.isFinite ? min(max(height, 1), 1_000) : 1
        self.expansion = expansion.isFinite ? min(max(expansion, 0), 1) : 0
    }
    public func center(for point: MascotPoint) -> CGPoint {
        let bounds = centerBounds
        return CGPoint(x: bounds.minX + bounds.width * point.x,
                       y: bounds.minY + bounds.height * point.y)
    }
    public func normalized(_ point: CGPoint) -> MascotPoint {
        let bounds = centerBounds
        return MascotPoint(x: bounds.width > 0 ? min(max((point.x - bounds.minX) / bounds.width, 0), 1) : 0.5,
                           y: bounds.height > 0 ? min(max((point.y - bounds.minY) / bounds.height, 0), 1) : 0.5)
    }
    public var payload: [String: Double] {
        ["width": width, "height": height, "body_size": bodySize,
         "travel_width": centerBounds.width, "travel_height": centerBounds.height]
    }
}

/// Continuous arrival steering. Paths come from intentions, never animation clips.
public struct MascotMotionState: Equatable, Sendable {
    public private(set) var position = MascotPoint()
    public private(set) var velocity = CGVector.zero
    public private(set) var memory = MascotLifeMemory()
    public private(set) var intent: MascotIntent = .observe
    public private(set) var reachedGoal = true
    private var targets: [MascotPoint] = []
    private var targetIndex = 0
    private var speed = 24.0
    private var remaining = 0.0
    private var held = 0.0
    private var pointer: MascotPoint?

    public init(position: MascotPoint = MascotPoint(), memory: MascotLifeMemory = MascotLifeMemory()) {
        if position.isValid { self.position = position }
        if memory.isValid { self.memory = memory }
        // An old position/personality is safe to restore; an old plan never resumes.
    }

    @discardableResult
    public mutating func accept(_ plan: MascotIntentPlan) -> Bool {
        guard plan.isValid else { return false }
        targets = plan.targets
        targetIndex = 0
        speed = plan.intent == .rest ? min(plan.speed, 8) : plan.speed
        remaining = plan.durationSeconds
        intent = plan.intent
        memory.lastIntent = plan.intent
        reachedGoal = false
        return true
    }

    public mutating func pointerMoved(_ point: MascotPoint?) {
        pointer = point?.isValid == true ? point : nil
        // Immediate orienting is a reflex; NVIDIA can choose the next longer intention.
        if pointer != nil && (remaining <= 0 || intent == .observe) {
            intent = .approach
            speed = 18
            remaining = 12
            reachedGoal = false
        }
    }

    public mutating func touch() {
        held = 0.8
        memory.affinity = min(1, memory.affinity + 0.04)
        memory.energy = min(1, memory.energy + 0.01)
    }

    public mutating func stop() {
        velocity = .zero
        targets = []
        remaining = 0
        pointer = nil
        held = 0
        intent = .observe
        reachedGoal = true
    }

    public mutating func advance(by elapsed: Double, room: MascotRoom, quiet: Bool = false,
                                 paused: Bool = false, reducedMotion: Bool = false) {
        guard elapsed.isFinite, elapsed > 0 else { return }
        if paused || reducedMotion { velocity = .zero; return }
        // A delayed display frame must never fast-forward through a wall.
        let dt = min(elapsed, 0.05)
        remaining = max(0, remaining - dt)
        held = max(0, held - dt)
        let atRest = remaining == 0 || reachedGoal || intent == .rest
        memory.energy = min(max(memory.energy + dt * (atRest ? 0.001 : -0.0006), 0), 1)
        memory.curiosity = min(max(memory.curiosity + dt * (atRest ? 0.0015 : -0.001), 0), 1)
        memory.affinity = max(0.1, memory.affinity - dt / 86_400)
        let here = room.center(for: position)
        var destination = here
        if remaining > 0 && !reachedGoal && held <= 0 {
            if intent == .approach, let pointer {
                // Don't chase underneath the pointer: leave room to click/pet the body.
                let toward = room.center(for: pointer)
                let dx = toward.x - here.x, dy = toward.y - here.y
                let distance = hypot(dx, dy)
                let stoppingDistance = room.bodySize * 0.25
                if distance > stoppingDistance {
                    destination = CGPoint(x: here.x + dx * (distance - stoppingDistance) / distance,
                                          y: here.y + dy * (distance - stoppingDistance) / distance)
                }
            } else if targetIndex < targets.count {
                destination = room.center(for: targets[targetIndex])
            }
        }
        let dx = destination.x - here.x, dy = destination.y - here.y
        let distance = hypot(dx, dy)
        if distance < 1.2 && hypot(velocity.dx, velocity.dy) < 4 && held <= 0 {
            if intent != .approach || pointer == nil {
                if memory.visits.last.map({ $0.distance(to: position) > 0.08 }) ?? true {
                    memory.visits.append(position)
                    if memory.visits.count > 6 { memory.visits.removeFirst() }
                }
            }
            if targetIndex + 1 < targets.count && intent != .approach {
                targetIndex += 1
            } else if intent != .approach || pointer == nil {
                reachedGoal = true
            }
        }
        let limit = held > 0 ? 0 : speed * (quiet ? 0.3 : 1)
        let desiredSpeed = min(limit, distance * 2.8)
        let desired = distance > 0.01 ? CGVector(dx: dx / distance * desiredSpeed,
                                                dy: dy / distance * desiredSpeed) : .zero
        var ax = (desired.dx - velocity.dx) * 7
        var ay = (desired.dy - velocity.dy) * 7
        let acceleration = hypot(ax, ay)
        if acceleration > 160 { ax *= 160 / acceleration; ay *= 160 / acceleration }
        velocity.dx += ax * dt
        velocity.dy += ay * dt
        let next = CGPoint(x: here.x + velocity.dx * dt, y: here.y + velocity.dy * dt)
        let bounded = room.normalized(next)
        let physical = room.center(for: bounded)
        if abs(physical.x - next.x) > 0.001 { velocity.dx = 0 }
        if abs(physical.y - next.y) > 0.001 { velocity.dy = 0 }
        position = bounded
        if remaining <= 0 && hypot(velocity.dx, velocity.dy) < 0.1 { reachedGoal = true }
    }

    public var bodyTilt: Double { min(max(velocity.dx / 400, -0.08), 0.08) }
}
