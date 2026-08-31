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
        .executable(name: "jarvis-mlx-engine", targets: ["JarvisMLXEngine"]),
    ],
    dependencies: [
        .package(
            url: "https://github.com/ml-explore/mlx-swift-lm",
            exact: "3.31.4"
        ),
        .package(
            url: "https://github.com/ml-explore/mlx-swift",
            exact: "0.31.6"
        ),
        .package(
            url: "https://github.com/huggingface/swift-huggingface",
            from: "0.9.0"
        ),
        .package(
            url: "https://github.com/huggingface/swift-transformers",
            from: "1.3.0"
        ),
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
        .executableTarget(
            name: "JarvisLocalBrain",
            dependencies: ["AegisAudioCore"]
        ),
        .executableTarget(name: "JarvisLocalEmbedding"),
        .executableTarget(
            name: "JarvisMLXEngine",
            dependencies: [
                "AegisAudioCore",
                .product(name: "MLXLLM", package: "mlx-swift-lm"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
                .product(name: "MLXHuggingFace", package: "mlx-swift-lm"),
                .product(name: "HuggingFace", package: "swift-huggingface"),
                .product(name: "MLX", package: "mlx-swift"),
                .product(name: "Tokenizers", package: "swift-transformers"),
            ]
        ),
        .testTarget(
            name: "AegisAudioCoreTests",
            dependencies: ["AegisAudioCore"]
        ),
    ]
)
