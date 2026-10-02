import AegisAudioCore
import AppKit
import OSLog
import QuartzCore
import SwiftUI

/// The physical loop and remote intentions have separate clocks and lifetimes.
@MainActor
@Observable
final class MascotAutonomyController: NSObject {
    var position = MascotPoint()
    var tilt = 0.0
    var intent: MascotIntent = .observe
    @ObservationIgnored private var motion = MascotMotionState()
    @ObservationIgnored private var room = MascotRoom(width: 180, height: 40, expansion: 0)
    @ObservationIgnored private var displayLink: CADisplayLink?
    @ObservationIgnored private var planningLoop: Task<Void, Never>?
    @ObservationIgnored private var pendingTask: Task<Void, Never>?
    @ObservationIgnored private var pendingRevision: UUID?
    @ObservationIgnored private var epoch = 0
    @ObservationIgnored private var nextDecision = 0.0
    @ObservationIgnored private var lastTimestamp = 0.0
    @ObservationIgnored private var lastSave = 0.0
    @ObservationIgnored private var lastContact = -Double.infinity
    @ObservationIgnored private var pointer: MascotPoint?
    @ObservationIgnored private var lastPhase: MascotBehaviorState.Phase = .resting
    @ObservationIgnored private var event = "started"
    @ObservationIgnored private var reduceMotion = false
    @ObservationIgnored private weak var presentation: NotchPresentationState?
    @ObservationIgnored private weak var model: MenuBarModel?
    @ObservationIgnored private let logger = Logger(subsystem: "ai.aegis.menubar", category: "MascotAutonomy")
    private static let memoryKey = "jarvis.mascot.physical-memory.v1"

    private struct SavedLife: Codable {
        let position: MascotPoint
        let memory: MascotLifeMemory
    }

    func start(in view: NSView, presentation: NotchPresentationState, model: MenuBarModel,
               interactive: Bool) {
        stop()
        self.presentation = presentation
        self.model = model
        guard interactive else { publish(); return }
        if let data = UserDefaults.standard.data(forKey: Self.memoryKey), data.count <= 2_048,
           let saved = try? JSONDecoder().decode(SavedLife.self, from: data),
           saved.position.isValid, saved.memory.isValid {
            motion = MascotMotionState(position: saved.position, memory: saved.memory)
        }
        publish()
        lastTimestamp = CACurrentMediaTime()
        let link = view.displayLink(target: self, selector: #selector(tick(_:)))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 30, maximum: 60, preferred: 60)
        displayLink = link
        link.add(to: .main, forMode: .common)
        planningLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                self?.considerDecision()
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
    }

    func updateRoom(size: CGSize, expansion: Double) {
        let next = MascotRoom(width: size.width, height: size.height, expansion: expansion)
        guard next != room else { return }
        let changedPresentation = (next.expansion > 0.95) != (room.expansion > 0.95)
        room = next
        if changedPresentation { event = "space_changed" }
    }

    func pointerMoved(x: Double, y: Double) {
        guard x.isFinite, y.isFinite, model?.mascotSessionAvailable == true else { return }
        let physical = CGPoint(x: (min(max(x, -1), 1) + 1) * room.width / 2,
                               y: (min(max(y, -1), 1) + 1) * room.height / 2)
        pointer = room.normalized(physical)
        motion.pointerMoved(pointer)
    }

    func hover(_ inside: Bool) {
        if !inside { pointer = nil; motion.pointerMoved(nil) }
        event = "interaction"
        nextDecision = min(nextDecision, CACurrentMediaTime() + 1)
    }

    func touch() {
        let now = CACurrentMediaTime()
        guard now - lastContact >= 0.45 else { return }
        lastContact = now
        motion.touch()
        event = "interaction"
        save()
    }

    func setReducedMotion(_ value: Bool) {
        guard reduceMotion != value else { return }
        reduceMotion = value
        displayLink?.preferredFrameRateRange = value
            ? CAFrameRateRange(minimum: 1, maximum: 1, preferred: 1)
            : CAFrameRateRange(minimum: 30, maximum: 60, preferred: 60)
        if value {
            cancelPending()
            motion.stop()
            presentation?.behavior.setAutonomousIntent(nil)
            publish()
        }
    }

    func stop() {
        planningLoop?.cancel(); planningLoop = nil
        cancelPending()
        displayLink?.invalidate(); displayLink = nil
        if model != nil { save() }
        motion.stop()
        presentation?.behavior.setAutonomousIntent(nil)
        presentation = nil
        model = nil
    }

    private func cancelPending() {
        epoch += 1
        pendingTask?.cancel()
        pendingTask = nil
        if let revision = pendingRevision, let model {
            Task { _ = await model.performMascotIPC("mascot.plan.cancel",
                                                  payload: ["revision": revision.uuidString]) }
        }
        pendingRevision = nil
    }

    @objc private func tick(_ link: CADisplayLink) {
        guard let model, let presentation else { return }
        let now = CACurrentMediaTime()
        let dt = now - lastTimestamp
        lastTimestamp = now
        let pose = presentation.behavior.pose(at: Date().timeIntervalSinceReferenceDate)
        if pose.phase != lastPhase {
            if [.listening, .thinking, .speaking, .attention, .paused].contains(pose.phase) {
                cancelPending()
            }
            lastPhase = pose.phase
        }
        if !model.mascotSessionAvailable {
            if pendingTask != nil { cancelPending() }
            motion.stop()
            pointer = nil
            presentation.behavior.setAutonomousIntent(nil)
        }
        motion.advance(by: dt, room: room,
                       quiet: [.listening, .thinking, .speaking, .attention].contains(pose.phase),
                       paused: !model.mascotSessionAvailable, reducedMotion: reduceMotion)
        publish()
        if now - lastSave > 30 && model.mascotSessionAvailable { save() }
    }

    private func publish() {
        if position != motion.position { position = motion.position }
        if abs(tilt - motion.bodyTilt) > 0.0002 { tilt = motion.bodyTilt }
        if intent != motion.intent { intent = motion.intent }
    }

    private func save() {
        lastSave = CACurrentMediaTime()
        if let data = try? JSONEncoder().encode(SavedLife(position: motion.position, memory: motion.memory)),
           data.count <= 2_048 {
            UserDefaults.standard.set(data, forKey: Self.memoryKey)
        }
    }

    private func considerDecision() {
        guard let model, let presentation, model.canPlanMascotIntent, !reduceMotion,
              pendingTask == nil, CACurrentMediaTime() >= nextDecision else { return }
        let phase = presentation.behavior.pose(at: Date().timeIntervalSinceReferenceDate).phase
        guard ![.listening, .thinking, .speaking, .attention, .paused].contains(phase) else { return }
        let revision = UUID()
        let capturedEpoch = epoch
        var geometry: [String: Any] = room.payload
        geometry["expanded"] = room.expansion > 0.95
        let cursor = pointer ?? MascotPoint()
        guard let memoryData = try? JSONEncoder().encode(motion.memory),
              let memoryObject = try? JSONSerialization.jsonObject(with: memoryData) else { return }
        let payload: [String: Any] = [
            "revision": revision.uuidString,
            "room": geometry, "position": ["x": position.x, "y": position.y],
            "pointer": ["present": pointer != nil, "x": cursor.x, "y": cursor.y],
            "phase": String(describing: phase), "memory": memoryObject,
            "event": motion.reachedGoal && event == "idle" ? "goal_reached" : event,
        ]
        event = "idle"
        pendingRevision = revision
        nextDecision = CACurrentMediaTime() + 60
        pendingTask = Task { @MainActor [weak self, weak model] in
            guard let self, let model else { return }
            defer {
                if pendingRevision == revision { pendingTask = nil; pendingRevision = nil }
            }
            guard let data = await model.performMascotIPC("mascot.plan.request", payload: payload),
                  let response = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
            if let retry = response["retry_after"] as? Double {
                nextDecision = max(nextDecision, CACurrentMediaTime() + min(max(retry, 15), 3_600))
            }
            guard response["status"] as? String == "pending" else { return }
            for _ in 0..<16 {
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
                guard !Task.isCancelled, capturedEpoch == epoch, model.canPlanMascotIntent else { return }
                guard let data = await model.performMascotIPC("mascot.plan.status",
                                                             payload: ["revision": revision.uuidString]),
                      let status = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
                if status["status"] as? String == "pending" { continue }
                guard status["status"] as? String == "ready",
                      (status["revision"] as? String)?.lowercased() == revision.uuidString.lowercased(),
                      status["provider"] as? String == "nvidia", let object = status["plan"],
                      let planData = try? JSONSerialization.data(withJSONObject: object),
                      let plan = try? JSONDecoder().decode(MascotIntentPlan.self, from: planData),
                      !Task.isCancelled, capturedEpoch == epoch, motion.accept(plan) else {
                    logger.info("mascot_plan unavailable_or_invalid")
                    nextDecision = max(nextDecision, CACurrentMediaTime() + 90)
                    return
                }
                presentation.behavior.setAutonomousIntent(plan.intent)
                save()
                logger.info("mascot_plan accepted provider=nvidia intent=\(plan.intent.rawValue, privacy: .public) targets=\(plan.targets.count)")
                return
            }
        }
    }
}
