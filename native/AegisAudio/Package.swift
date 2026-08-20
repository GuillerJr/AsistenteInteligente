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
        .testTarget(
            name: "AegisAudioCoreTests",
            dependencies: ["AegisAudioCore"]
        ),
    ]
)
