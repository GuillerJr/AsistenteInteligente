import AegisAudioCore
import AppKit
import SceneKit
import SwiftUI

struct NodeSphereView: NSViewRepresentable {
    let activity: [IPCSwarmAgentRole: Int]

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeNSView(context: Context) -> SCNView {
        context.coordinator.makeView()
    }

    func updateNSView(_ nsView: SCNView, context: Context) {
        context.coordinator.update(activity: activity)
    }

    @MainActor
    final class Coordinator {
        private let materials: [IPCSwarmAgentRole: SCNMaterial]
        private let colors: [IPCSwarmAgentRole: NSColor]
        private let scene = SCNScene()

        init() {
            colors = [
                .router: .systemCyan,
                .planner: .systemBlue,
                .criticalReasoner: .systemPink,
                .codeSecurity: .systemOrange,
                .vision: .systemPurple,
                .omni: .systemTeal,
                .synthesizer: .systemGreen,
            ]
            materials = Dictionary(
                uniqueKeysWithValues: IPCSwarmAgentRole.all.map { role in
                    let material = SCNMaterial()
                    material.lightingModel = .constant
                    return (role, material)
                }
            )
            buildScene()
            update(activity: [:])
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
            view.preferredFramesPerSecond = 30
            view.rendersContinuously = true
            view.isPlaying = true
            return view
        }

        func update(activity: [IPCSwarmAgentRole: Int]) {
            for role in IPCSwarmAgentRole.all {
                guard let material = materials[role], let color = colors[role] else { continue }
                let count = activity[role, default: 0]
                if count > 0 {
                    material.diffuse.contents = color
                    material.emission.contents = color
                    material.emission.intensity = 1 + CGFloat(min(count, 4)) * 0.35
                } else {
                    material.diffuse.contents = color.withAlphaComponent(0.58)
                    material.emission.contents = color.withAlphaComponent(0.28)
                    material.emission.intensity = 0.55
                }
            }
        }

        private func buildScene() {
            let sphere = SCNNode()
            let clusters = Self.clusterCenters
            let geometries = Dictionary(
                uniqueKeysWithValues: IPCSwarmAgentRole.all.map { role in
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

            sphere.runAction(
                .repeatForever(.rotateBy(x: 0.08, y: 0.34, z: 0.03, duration: 8))
            )
            scene.rootNode.addChildNode(sphere)

            let camera = SCNCamera()
            camera.fieldOfView = 42
            let cameraNode = SCNNode()
            cameraNode.camera = camera
            cameraNode.position = SCNVector3(0, 0, 5.2)
            scene.rootNode.addChildNode(cameraNode)
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

private extension IPCSwarmAgentRole {
    static let all: [IPCSwarmAgentRole] = [
        .router,
        .planner,
        .criticalReasoner,
        .codeSecurity,
        .vision,
        .omni,
        .synthesizer,
    ]
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
