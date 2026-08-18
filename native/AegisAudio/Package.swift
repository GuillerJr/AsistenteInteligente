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
    ],
    targets: [
        .target(name: "AegisAudioCore"),
        .executableTarget(
            name: "AegisAudioHelper",
            dependencies: ["AegisAudioCore"]
        ),
        .testTarget(
            name: "AegisAudioCoreTests",
            dependencies: ["AegisAudioCore"]
        ),
    ]
)
