import Testing
@testable import AegisAudioCore

@Suite("Mascot behavior")
struct MascotBehaviorTests {
    @Test func sameEventsProduceTheSameBehavior() {
        var first = MascotBehaviorState(now: 0)
        var second = first
        for time in [1.0, 2, 3] {
            first.hover(true, at: time)
            second.hover(true, at: time)
            first.movePointer(x: 0.8, y: -0.5, at: time)
            second.movePointer(x: 0.8, y: -0.5, at: time)
            first.touch(at: time)
            second.touch(at: time)
        }
        #expect(first.pose(at: 3.2) == second.pose(at: 3.2))
        #expect(first.pose(at: 3.2).phase == .acknowledging)
    }

    @Test func gazeFollowsLocalPointerContinuouslyAndReturnsOnExit() {
        var state = MascotBehaviorState()
        state.hover(true, at: 1)
        state.movePointer(x: 9, y: -9, at: 1)
        #expect(state.pose(at: 1).gazeX == 0)
        let prior = state.pose(at: 1.1)
        #expect(prior.gazeX > 0 && prior.gazeX < 0.025)
        #expect(prior.gazeY < 0 && prior.gazeY > -0.015)
        state.movePointer(x: -1, y: 1, at: 1.1)
        #expect(state.pose(at: 1.1).gazeX == prior.gazeX)
        #expect(state.pose(at: 1.7).gazeX < 0)
        state.hover(false, at: 2)
        #expect(abs(state.pose(at: 3).gazeX) < 0.00001)
    }

    @Test func restsAfterInactivityAndGreetsOnlyWhenReturningFromRest() {
        var state = MascotBehaviorState()
        #expect(state.pose(at: 74).phase == .resting)
        #expect(state.pose(at: 75).phase == .sleeping)
        #expect(state.pose(at: 72).eyeOpenness > state.pose(at: 74).eyeOpenness)
        state.hover(true, at: 76)
        #expect(state.pose(at: 76.2).phase == .greeting)
        #expect(state.pose(at: 76).eyeOpenness < state.pose(at: 76.2).eyeOpenness)
        #expect(state.pose(at: 78).phase == .curious)
        state.hover(false, at: 79)
        state.hover(true, at: 80)
        #expect(state.pose(at: 80).phase == .curious)
    }

    @Test func realWorkAndDecisionsTakePriorityOverTouchAndNotices() {
        var state = MascotBehaviorState()
        state.hover(true, at: 1)
        state.touch(at: 1)
        state.update(kind: .listening, sessionAvailable: true, hasNotice: true, at: 1)
        #expect(state.pose(at: 1.2).phase == .listening)
        state.update(kind: .processing, sessionAvailable: true, at: 2)
        #expect(state.pose(at: 2).phase == .thinking)
        state.update(kind: .approval, sessionAvailable: true, at: 3)
        #expect(state.pose(at: 3).phase == .attention)
        state.update(kind: .speaking, sessionAvailable: true, at: 4)
        #expect(state.pose(at: 4).phase == .speaking)
    }

    @Test func celebratesOnlyConfirmedCompletionAndNeverFailureOrStartup() {
        var state = MascotBehaviorState()
        state.update(kind: .completed, sessionAvailable: true, at: 1)
        #expect(state.pose(at: 1).phase == .resting)
        state.update(kind: .processing, sessionAvailable: true, at: 2)
        state.update(kind: .failure, sessionAvailable: true, at: 3)
        #expect(state.pose(at: 3).phase == .attention)
        state.update(kind: .processing, sessionAvailable: true, at: 4)
        state.update(kind: .completed, sessionAvailable: true, at: 5)
        #expect(state.pose(at: 5.2).phase == .celebrating)
        state.update(kind: .completed, sessionAvailable: true, at: 7)
        #expect(state.pose(at: 8).phase == .resting)
    }

    @Test func repeatedContactIsBoundedAndLockClearsInteractionMemory() {
        var state = MascotBehaviorState()
        state.touch(at: 1)
        state.touch(at: 1.1)
        #expect(state.contactCount == 1)
        for time in 2...20 { state.touch(at: Double(time)) }
        #expect(state.contactCount == 8)
        state.update(kind: .speaking, sessionAvailable: false, at: 21)
        state.hover(true, at: 22)
        state.touch(at: 22)
        #expect(state.pose(at: 22).phase == .paused)
        #expect(state.contactCount == 0)
        #expect(!state.isHovered)
        state.update(kind: .idle, sessionAvailable: true, at: 23)
        #expect(state.pose(at: 23).phase == .resting)
    }

    @Test func reducedMotionPreservesMeaningWithoutMovingTheBody() {
        var state = MascotBehaviorState()
        state.hover(true, at: 1)
        state.movePointer(x: 1, y: 1, at: 1)
        state.touch(at: 1)
        #expect(state.pose(at: 1, reducedMotion: true).eyeOpenness < 1)
        let pose = state.pose(at: 1.2, reducedMotion: true)
        #expect(pose.phase == .acknowledging)
        #expect(pose.nod == 0 && pose.tilt == 0)
        #expect(pose.eyeOpenness < 1)
    }

    @Test func invalidTimesAndPointerValuesCannotCorruptThePose() {
        var state = MascotBehaviorState(now: .nan)
        state.movePointer(x: .nan, y: .infinity, at: 1)
        state.touch(at: .nan)
        #expect(state.contactCount == 0)
        state.hover(true, at: 2)
        state.touch(at: 2)
        state.touch(at: 1)
        #expect(state.contactCount == 1)
        for time in [Double.nan, .infinity, -1, 2, 900] {
            let pose = state.pose(at: time)
            #expect(pose.gazeX.isFinite && pose.gazeY.isFinite && pose.nod.isFinite && pose.tilt.isFinite)
        }
    }
}
