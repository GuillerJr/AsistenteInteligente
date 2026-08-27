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
        .executable(name: "jarvis-speaker-trainer", targets: ["JarvisSpeakerTrainer"]),
        .executable(name: "jarvis-computer-helper", targets: ["JarvisComputerHelper"]),
        .executable(name: "jarvis-local-brain", targets: ["JarvisLocalBrain"]),
        .executable(name: "jarvis-local-embedding", targets: ["JarvisLocalEmbedding"]),
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
        .executableTarget(name: "JarvisSpeakerTrainer"),
        .executableTarget(
            name: "JarvisComputerHelper",
            dependencies: ["AegisAudioCore"]
        ),
        .executableTarget(name: "JarvisLocalBrain"),
        .executableTarget(name: "JarvisLocalEmbedding"),
        .testTarget(
            name: "AegisAudioCoreTests",
            dependencies: ["AegisAudioCore"]
        ),
    ]
)
