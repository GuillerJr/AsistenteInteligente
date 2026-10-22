// swift-tools-version: 6.2

import Foundation
import PackageDescription

// SwiftPM's native engine does not supply the localized-string output directory.
// Extraction is opt-in and always names a build-local destination; ordinary builds
// consume the committed catalog/resources without trying to write to `/`.
let localizationSettings: [SwiftSetting] = ProcessInfo.processInfo.environment[
    "AEGIS_LOCALIZATION_OUTPUT_DIR"
].map { [.unsafeFlags(["-emit-localized-strings", "-emit-localized-strings-path", $0])] } ?? []
let coreTestsOnly = ProcessInfo.processInfo.environment["AEGIS_SWIFT_TEST_SCOPE"] == "core"

let products: [Product] = [
    .library(name: "AegisAudioCore", targets: ["AegisAudioCore"]),
    .executable(name: "aegis-audio-helper", targets: ["AegisAudioHelper"]),
    .executable(name: "Jarvis", targets: ["AegisMenuBar"]),
    .executable(name: "jarvis-vad-helper", targets: ["JarvisVADHelper"]),
    .executable(name: "jarvis-wake-word-trainer", targets: ["JarvisWakeWordTrainer"]),
    .executable(name: "jarvis-speaker-trainer", targets: ["JarvisSpeakerTrainer"]),
    .executable(name: "jarvis-computer-helper", targets: ["JarvisComputerHelper"]),
    .executable(
        name: "jarvis-ui-qualification-fixture",
        targets: ["JarvisUIQualificationFixture"]
    ),
    .executable(name: "jarvis-local-embedding", targets: ["JarvisLocalEmbedding"]),
    .executable(name: "jarvis-spotlight-indexer", targets: ["JarvisSpotlightIndexer"]),
    .executable(name: "jarvis-ios-bridge", targets: ["JarvisIOSBridge"]),
    .executable(
        name: "jarvis-biometric-calibrator",
        targets: ["JarvisBiometricCalibrator"]
    ),
]

let dependencies: [Package.Dependency] = [
    .package(
        url: "https://github.com/microsoft/onnxruntime-swift-package-manager",
        exact: "1.24.2"
    ),
]

let targets: [Target] = [
    .target(
        name: "AegisAudioCore",
        // The shared core is linked into each native executable. Optimize its
        // release code for size as well as the UI; debug/test defaults stay intact.
        swiftSettings: [
            .define("AEGIS_NVIDIA_ONLY"),
            .unsafeFlags(["-Osize"], .when(configuration: .release)),
        ]
    ),
    .target(
        name: "AegisVADRuntime",
        dependencies: [.product(name: "onnxruntime", package: "onnxruntime-swift-package-manager")],
        resources: [.copy("Resources/silero-vad.onnx"), .copy("Resources/SILERO_NOTICE.txt")],
        swiftSettings: [.unsafeFlags(["-Osize"], .when(configuration: .release))]
    ),
    .executableTarget(name: "JarvisVADHelper", dependencies: ["AegisVADRuntime"]),
    .executableTarget(
        name: "AegisAudioHelper",
        dependencies: ["AegisAudioCore"]
    ),
    .executableTarget(
        name: "AegisMenuBar",
        dependencies: ["AegisAudioCore"],
        exclude: ["Resources/JarvisMascotBody.png"],
        resources: [
            .copy("Resources/JarvisTricksterDot.png"),
            .copy("Resources/Localizable.xcstrings"),
            .process("Resources/es.lproj"),
        ],
        // Keep the UI executable within the distribution size budget as its
        // SwiftUI composition grows. Debug/test builds retain their defaults.
        swiftSettings: localizationSettings + [
            .unsafeFlags(["-Osize"], .when(configuration: .release)),
        ],
        // Nothing looks symbols up in the app (no dlsym, no native plugins), so its
        // ~190 KB export trie is dead weight; dyld starts it through LC_MAIN.
        linkerSettings: [.unsafeFlags(["-Xlinker", "-no_exported_symbols"],
                                      .when(configuration: .release))]
    ),
    .executableTarget(name: "JarvisWakeWordTrainer", dependencies: ["AegisAudioCore"]),
    .executableTarget(name: "JarvisSpeakerTrainer"),
    .executableTarget(
        name: "JarvisComputerHelper",
        dependencies: ["AegisAudioCore"]
    ),
    .executableTarget(name: "JarvisUIQualificationFixture"),
    .executableTarget(
        name: "JarvisLocalEmbedding",
        dependencies: ["AegisAudioCore"]
    ),
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
    .testTarget(
        name: "AegisMenuBarTests",
        dependencies: ["AegisMenuBar"]
    ),
]

let package = Package(
    name: "AegisAudio",
    defaultLocalization: "es",
    platforms: [
        .macOS(.v14),
    ],
    products: products,
    dependencies: dependencies,
    // The native engine aggregates every test target into AegisAudioPackageTests.
    // CI on Xcode 26 exercises the complete core suite; local/pre-push builds keep
    // both test targets, including the UI suite supported by the local toolchain.
    targets: targets.filter { !coreTestsOnly || $0.name != "AegisMenuBarTests" }
)
