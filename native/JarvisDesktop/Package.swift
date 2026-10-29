// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "JarvisDesktop",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "Jarvis", targets: ["JarvisApp"]),
               .executable(name: "JarvisQALauncher", targets: ["JarvisQALauncher"]),
               .library(name: "DesktopCore", targets: ["DesktopCore"])],
    targets: [
        .target(name: "DesktopCore"),
        .target(name: "QALaunchSupport"),
        .executableTarget(name: "JarvisApp", dependencies: ["DesktopCore"]),
        .executableTarget(name: "JarvisQALauncher", dependencies: ["QALaunchSupport"]),
        .testTarget(name: "QALaunchSupportTests", dependencies: ["QALaunchSupport"]),
        .testTarget(name: "DesktopCoreTests", dependencies: ["DesktopCore"]),
        .testTarget(name: "AppLayoutTests", dependencies: ["JarvisApp", "DesktopCore"])
    ]
)
