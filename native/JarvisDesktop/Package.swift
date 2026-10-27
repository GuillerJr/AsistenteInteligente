// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "JarvisDesktop",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "Jarvis", targets: ["JarvisApp"]),
               .library(name: "DesktopCore", targets: ["DesktopCore"])],
    targets: [
        .target(name: "DesktopCore"),
        .executableTarget(name: "JarvisApp", dependencies: ["DesktopCore"]),
        .testTarget(name: "DesktopCoreTests", dependencies: ["DesktopCore"]),
        .testTarget(name: "AppLayoutTests", dependencies: ["JarvisApp", "DesktopCore"])
    ]
)
