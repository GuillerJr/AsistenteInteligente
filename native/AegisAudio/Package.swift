// swift-tools-version: 6.2

import PackageDescription

let package = Package(
    name: "AegisAudio",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "AegisAudioCore", targets: ["AegisAudioCore"]),
        .executable(name: "aegis-audio-helper", targets: ["AegisAudioHelper"]),
        .executable(name: "Jarvis", targets: ["AegisMenuBar"]),
        .executable(name: "jarvis-wake-word-trainer", targets: ["JarvisWakeWordTrainer"]),
    ],
    targets: [
        .target(name: "AegisAudioCore"),
        .executableTarget(
            name: "AegisAudioHelper",
            dependencies: ["AegisAudioCore"]
        ),
        .executableTarget(
            name: "AegisMenuBar",
            dependencies: ["AegisAudioCore"]
        ),
        .executableTarget(name: "JarvisWakeWordTrainer"),
        .testTarget(
            name: "AegisAudioCoreTests",
            dependencies: ["AegisAudioCore"]
        ),
    ]
)
