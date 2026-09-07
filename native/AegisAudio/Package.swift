// swift-tools-version: 6.2

import Foundation
import PackageDescription

let buildMLX = ProcessInfo.processInfo.environment["AEGIS_BUILD_MLX"] == "1"

var products: [Product] = [
    .library(name: "AegisAudioCore", targets: ["AegisAudioCore"]),
    .executable(name: "aegis-audio-helper", targets: ["AegisAudioHelper"]),
    .executable(name: "Jarvis", targets: ["AegisMenuBar"]),
    .executable(name: "jarvis-wake-word-trainer", targets: ["JarvisWakeWordTrainer"]),
    .executable(name: "jarvis-speaker-trainer", targets: ["JarvisSpeakerTrainer"]),
    .executable(name: "jarvis-computer-helper", targets: ["JarvisComputerHelper"]),
    .executable(
        name: "jarvis-ui-qualification-fixture",
        targets: ["JarvisUIQualificationFixture"]
    ),
    .executable(name: "jarvis-local-brain", targets: ["JarvisLocalBrain"]),
    .executable(name: "jarvis-local-embedding", targets: ["JarvisLocalEmbedding"]),
    .executable(name: "jarvis-spotlight-indexer", targets: ["JarvisSpotlightIndexer"]),
    .executable(name: "jarvis-ios-bridge", targets: ["JarvisIOSBridge"]),
    .executable(
        name: "jarvis-biometric-calibrator",
        targets: ["JarvisBiometricCalibrator"]
    ),
]

var dependencies: [Package.Dependency] = [
    .package(
        url: "https://github.com/microsoft/onnxruntime-swift-package-manager",
        exact: "1.24.2"
    ),
]

var targets: [Target] = [
    .target(
        name: "AegisAudioCore",
        dependencies: [
            .product(name: "onnxruntime", package: "onnxruntime-swift-package-manager"),
        ],
        resources: [
            .copy("Resources/silero-vad.onnx"),
            .copy("Resources/SILERO_NOTICE.txt"),
        ]
    ),
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
    .executableTarget(name: "JarvisUIQualificationFixture"),
    .executableTarget(
        name: "JarvisLocalBrain",
        dependencies: ["AegisAudioCore"]
    ),
    .executableTarget(name: "JarvisLocalEmbedding"),
    .executableTarget(
        name: "JarvisSpotlightIndexer",
        dependencies: ["AegisAudioCore"]
    ),
    .executableTarget(
        name: "JarvisIOSBridge",
        dependencies: ["AegisAudioCore"]
    ),
    .executableTarget(
        name: "JarvisBiometricCalibrator",
        dependencies: ["AegisAudioCore"]
    ),
    .testTarget(
        name: "AegisAudioCoreTests",
        dependencies: ["AegisAudioCore"]
    ),
]

if buildMLX {
    dependencies.append(contentsOf: [
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
    ])
    products.append(contentsOf: [
        .executable(name: "jarvis-mlx-engine", targets: ["JarvisMLXEngine"]),
        .executable(name: "jarvis-mlx-vlm", targets: ["JarvisMLXVLM"]),
    ])
    targets.append(contentsOf: [
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
        .executableTarget(
            name: "JarvisMLXVLM",
            dependencies: [
                "AegisAudioCore",
                .product(name: "MLXVLM", package: "mlx-swift-lm"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
                .product(name: "MLXHuggingFace", package: "mlx-swift-lm"),
                .product(name: "MLX", package: "mlx-swift"),
                .product(name: "Tokenizers", package: "swift-transformers"),
            ]
        ),
    ])
}

let package = Package(
    name: "AegisAudio",
    platforms: [
        .macOS(.v14),
    ],
    products: products,
    dependencies: dependencies,
    targets: targets
)
