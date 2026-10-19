import CoreGraphics
import Testing
@testable import AegisAudioCore

@Suite("Mascot spatial autonomy")
struct MascotMotionTests {
    let expanded = MascotRoom(width: 280, height: 208, expansion: 1)

    @Test func exploresWaypointsWithoutClippingBodyOrLabels() {
        var state = MascotMotionState()
        let acceptance1 = state.accept(.init(intent: .explore, targets: [.init(x: 0, y: 0), .init(x: 1, y: 1)]))
        #expect(acceptance1)
        var greatestDistance = 0.0
        for _ in 0..<3_600 {
            state.advance(by: 1.0 / 60, room: expanded)
            let center = expanded.center(for: state.position)
            let half = expanded.bodySize / 2
            #expect(center.x - half >= 0 && center.x + half <= expanded.width)
            #expect(center.y - half >= 0 && center.y + half <= expanded.height - expanded.reservedBottom)
            greatestDistance = max(greatestDistance, state.position.distance(to: MascotPoint()))
        }
        #expect(greatestDistance > 0.5)
        #expect(state.reachedGoal)
        #expect(state.memory.visits.count >= 2)
        #expect(state.memory.isValid)
    }

    @Test func retargetingAndRoomExpansionPreservePositionAndVelocity() {
        var state = MascotMotionState()
        state.accept(.init(intent: .explore, targets: [.init(x: 1, y: 0)]))
        for _ in 0..<30 { state.advance(by: 1.0 / 60, room: expanded) }
        let before = state
        state.accept(.init(intent: .explore, targets: [.init(x: 0, y: 1)]))
        #expect(state.position == before.position)
        #expect(state.velocity == before.velocity)
        var lastRoom = MascotRoom(width: 180, height: 40, expansion: 0)
        for step in 0...60 {
            let t = Double(step) / 60
            lastRoom = MascotRoom(width: 180 + 100 * t, height: 40 + 168 * t, expansion: t)
            state.advance(by: 1.0 / 60, room: lastRoom)
            #expect(state.position.isValid)
            let center = lastRoom.center(for: state.position)
            #expect(center.y + lastRoom.bodySize / 2 <= lastRoom.height - lastRoom.reservedBottom + 0.001)
        }
    }

    @Test func invalidPlansAndLongFrameGapsCannotTeleport() {
        var state = MascotMotionState()
        let acceptance2 = state.accept(.init(intent: .explore, targets: [.init(x: .nan, y: 1)]))
        #expect(!acceptance2)
        let acceptance3 = state.accept(.init(intent: .explore, targets: [.init(x: 2, y: 1)]))
        #expect(!acceptance3)
        let acceptance4 = state.accept(.init(intent: .explore, targets: [], speed: 100))
        #expect(!acceptance4)
        state.accept(.init(intent: .explore, targets: [.init(x: 1, y: 1)]))
        let prior = expanded.center(for: state.position)
        state.advance(by: 600, room: expanded)
        let next = expanded.center(for: state.position)
        #expect(hypot(next.x - prior.x, next.y - prior.y) < 1)
        let frozen = state
        state.advance(by: .nan, room: expanded)
        #expect(state == frozen)
        for room in [MascotRoom(width: 1, height: 1, expansion: 1),
                     MascotRoom(width: .infinity, height: .nan, expansion: .nan)] {
            state.advance(by: 0.05, room: room)
            #expect(state.position.isValid)
            #expect(room.center(for: state.position).y == 0.5)
        }
    }

    @Test func cursorReflexStopsNearbyAndTouchMakesContactContinuous() {
        var state = MascotMotionState()
        state.pointerMoved(.init(x: 1, y: 0.5))
        for _ in 0..<300 { state.advance(by: 1.0 / 60, room: expanded) }
        #expect(state.position.x > 0.7 && state.position.x < 1)
        let affinity = state.memory.affinity
        state.touch()
        #expect(state.memory.affinity > affinity)
        for _ in 0..<40 { state.advance(by: 1.0 / 60, room: expanded) }
        #expect(hypot(state.velocity.dx, state.velocity.dy) < 1)
        let position = state.position
        state.advance(by: 0.05, room: expanded, reducedMotion: true)
        #expect(state.position == position && state.velocity == .zero)
        state.advance(by: 0.05, room: expanded, paused: true)
        #expect(state.position == position)
    }

    @Test func restoringMemoryNeverResumesOldIntentionsAndRealWorkOverridesThem() {
        var memory = MascotLifeMemory()
        memory.lastIntent = .explore
        let restored = MascotMotionState(position: .init(x: 0.8, y: 0.3), memory: memory)
        #expect(restored.intent == .observe && restored.reachedGoal)
        #expect(restored.memory.lastIntent == .explore)
        var behavior = MascotBehaviorState(now: 0)
        behavior.setAutonomousIntent(.rest)
        #expect(behavior.pose(at: 1).phase == .sleeping)
        behavior.update(kind: .processing, sessionAvailable: true, at: 2)
        #expect(behavior.pose(at: 2).phase == .thinking)
        behavior.update(kind: .idle, sessionAvailable: false, at: 3)
        #expect(behavior.pose(at: 3).phase == .paused)
        behavior.update(kind: .idle, sessionAvailable: true, at: 4)
        #expect(behavior.pose(at: 4).phase == .resting)
    }
}
