import AegisAudioCore
import AppKit
import SceneKit
import SwiftUI

struct NodeSphereView: NSViewRepresentable {
    let activity: [IPCSwarmAgentRole: Int]
    let voiceLevel: Float
    let listeningPulse: Bool
    let interrupting: Bool
    let reduceMotion: Bool

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> SCNView {
        context.coordinator.makeView()
    }

    func updateNSView(_ nsView: SCNView, context: Context) {
        context.coordinator.update(
            activity: activity,
            voiceLevel: voiceLevel,
            listeningPulse: listeningPulse,
            interrupting: interrupting,
            reduceMotion: reduceMotion
        )
    }

    static func dismantleNSView(_ nsView: SCNView, coordinator: Coordinator) {
        nsView.isPlaying = false
        nsView.rendersContinuously = false
        nsView.scene = nil
    }

    @MainActor
    final class Coordinator {
        private let materials: [IPCSwarmAgentRole: SCNMaterial]
        private let coreMaterial = SCNMaterial()
        private let scene = SCNScene()
        private let sphere = SCNNode()
        private weak var sceneView: SCNView?

        init() {
            materials = Dictionary(
                uniqueKeysWithValues: SwarmRoleVisuals.orderedRoles.map { role in
                    let material = SCNMaterial()
                    material.lightingModel = .constant
                    return (role, material)
                }
            )
            buildScene()
            update(
                activity: [:],
                voiceLevel: 0,
                listeningPulse: false,
                interrupting: false,
                reduceMotion: false
            )
        }

        func makeView() -> SCNView {
            let view = SCNView()
            view.scene = scene
            view.backgroundColor = .clear
            view.wantsLayer = true
            view.layer?.isOpaque = false
            view.layer?.backgroundColor = NSColor.clear.cgColor
            view.allowsCameraControl = false
            view.antialiasingMode = .multisampling4X
            view.preferredFramesPerSecond = 24
            view.rendersContinuously = false
            view.isPlaying = false
            view.setAccessibilityElement(false)
            sceneView = view
            return view
        }

        func update(
            activity: [IPCSwarmAgentRole: Int],
            voiceLevel: Float,
            listeningPulse: Bool,
            interrupting: Bool,
            reduceMotion: Bool
        ) {
            for role in SwarmRoleVisuals.orderedRoles {
                guard let material = materials[role] else { continue }

                let color = SwarmRoleVisuals.sceneColor(for: role)
                let count = activity[role, default: 0]
                if count > 0 {
                    material.diffuse.contents = color
                    material.emission.contents = color
                    material.emission.intensity = 1.25 + CGFloat(min(count, 4)) * 0.3
                    material.transparency = 1
                } else {
                    material.diffuse.contents = color.withAlphaComponent(0.62)
                    material.emission.contents = color.withAlphaComponent(0.3)
                    material.emission.intensity = 0.58
                    material.transparency = 0.86
                }
            }

            let boundedLevel = min(max(voiceLevel, 0), 1)
            coreMaterial.emission.intensity = 0.8 + CGFloat(boundedLevel) * 1.8
            let scale = listeningPulse
                ? 0.96 + CGFloat(boundedLevel) * 0.04
                : 1 + CGFloat(boundedLevel) * 0.1

            SCNTransaction.begin()
            SCNTransaction.animationDuration = reduceMotion ? 0 : (interrupting ? 0.15 : 0.08)
            sphere.scale = SCNVector3(scale, scale, scale)
            SCNTransaction.commit()

            let active = activity.values.contains { $0 > 0 }
            sphere.action(forKey: "rotation")?.speed = reduceMotion ? 0 : (active ? 1.2 : 0.72)
            sceneView?.preferredFramesPerSecond = reduceMotion ? 12 : (active ? 30 : 24)
            sceneView?.rendersContinuously = !reduceMotion
            sceneView?.isPlaying = !reduceMotion
            sceneView?.setNeedsDisplay(sceneView?.bounds ?? .zero)
        }

        private func buildScene() {
            let clusters = Self.clusterCenters
            let geometries = Dictionary(
                uniqueKeysWithValues: SwarmRoleVisuals.orderedRoles.map { role in
                    let geometry = SCNSphere(radius: 0.024)
                    geometry.segmentCount = 8
                    geometry.materials = [materials[role]!]
                    return (role, geometry)
                }
            )

            let nodeCount = 210
            let goldenAngle = Double.pi * (3 - sqrt(5))
            for index in 0 ..< nodeCount {
                let y = 1 - (2 * (Double(index) + 0.5) / Double(nodeCount))
                let radial = sqrt(max(0, 1 - y * y))
                let angle = goldenAngle * Double(index)
                let point = SCNVector3(
                    Float(cos(angle) * radial),
                    Float(y),
                    Float(sin(angle) * radial)
                )
                let role = clusters.max { dot(point, $0.value) < dot(point, $1.value) }!.key
                let node = SCNNode(geometry: geometries[role])
                node.position = point * 1.72
                sphere.addChildNode(node)
            }

            addCore()
            addClusterBeacons(clusters)
            sphere.runAction(
                .repeatForever(.rotateBy(x: 0.08, y: 0.34, z: 0.03, duration: 8)),
                forKey: "rotation"
            )
            scene.rootNode.addChildNode(sphere)

            let camera = SCNCamera()
            camera.fieldOfView = 42
            let cameraNode = SCNNode()
            cameraNode.camera = camera
            cameraNode.position = SCNVector3(0, 0, 5.2)
            scene.rootNode.addChildNode(cameraNode)
        }

        private func addCore() {
            let color = NSColor.systemCyan
            coreMaterial.lightingModel = .constant
            coreMaterial.diffuse.contents = color.withAlphaComponent(0.68)
            coreMaterial.emission.contents = color.withAlphaComponent(0.72)

            let geometry = SCNSphere(radius: 0.055)
            geometry.segmentCount = 16
            geometry.materials = [coreMaterial]
            sphere.addChildNode(SCNNode(geometry: geometry))
        }

        private func addClusterBeacons(
            _ clusters: [IPCSwarmAgentRole: SCNVector3]
        ) {
            for role in SwarmRoleVisuals.orderedRoles {
                guard let center = clusters[role] else { continue }
                let geometry = SCNSphere(radius: 0.038)
                geometry.segmentCount = 10
                geometry.materials = [materials[role]!]
                let beacon = SCNNode(geometry: geometry)
                beacon.position = center * 1.75
                sphere.addChildNode(beacon)
            }
        }

        private static let clusterCenters: [IPCSwarmAgentRole: SCNVector3] = [
            .router: normalized(SCNVector3(0, 1, 0)),
            .planner: normalized(SCNVector3(0.9, 0.35, 0.2)),
            .criticalReasoner: normalized(SCNVector3(-0.8, 0.4, 0.35)),
            .codeSecurity: normalized(SCNVector3(0.7, -0.55, 0.4)),
            .vision: normalized(SCNVector3(-0.65, -0.55, 0.5)),
            .omni: normalized(SCNVector3(0.15, 0.05, -1)),
            .synthesizer: normalized(SCNVector3(0, -1, -0.15)),
        ]
    }
}

private func dot(_ left: SCNVector3, _ right: SCNVector3) -> CGFloat {
    let x = left.x * right.x
    let y = left.y * right.y
    let z = left.z * right.z
    return x + y + z
}

private func normalized(_ vector: SCNVector3) -> SCNVector3 {
    let length = sqrt(vector.x * vector.x + vector.y * vector.y + vector.z * vector.z)
    return SCNVector3(vector.x / length, vector.y / length, vector.z / length)
}

private func * (vector: SCNVector3, scalar: CGFloat) -> SCNVector3 {
    SCNVector3(vector.x * scalar, vector.y * scalar, vector.z * scalar)
}
