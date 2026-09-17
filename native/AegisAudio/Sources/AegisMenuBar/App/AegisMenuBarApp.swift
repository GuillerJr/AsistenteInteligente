import AppKit
import AegisAudioCore
import Darwin
import SwiftUI

@MainActor
private final class AegisAppDelegate: NSObject, NSApplicationDelegate {
    let model = MenuBarModel()
    let settingsStore = JarvisSettingsStore()
    private let daemonSupervisor = BundledDaemonSupervisor()
    private var terminationTask: Task<Void, Never>?
    private var terminationReady = false
    private var terminationSignal: DispatchSourceSignal?

    func applicationWillFinishLaunching(_ notification: Notification) {
        // The build script uses SIGTERM. Route it through AppKit termination
        // so the bundled daemon drains and seals its audit before the app exits.
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { NSApplication.shared.terminate(nil) }
        source.resume()
        terminationSignal = source
#if DEBUG
        // Visual fixtures must never start the daemon, microphone or monitoring.
        if NotchPreviewMode(arguments: ProcessInfo.processInfo.arguments) != nil { return }
#endif
        do {
            let applicationSupport = try FileManager.default.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )
            try SecureUpdateLaunchGuard.authorizeLaunch(
                arguments: ProcessInfo.processInfo.arguments,
                buildRevision: JarvisBuildIdentity.current(),
                stateDirectory: applicationSupport.appending(
                    path: "Aegis",
                    directoryHint: .isDirectory
                )
            )
            try daemonSupervisor.startIfBundled()
        } catch {
            model.registerBundledDaemonLaunchFailure()
        }
        model.startPowerMonitoring()
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonSupervisor.stop()
    }

    func restartDaemon() async throws {
        await daemonSupervisor.stopAndWait()
        try daemonSupervisor.startIfBundled()
        // Startup includes the audit checks and MCP admission; wait for the
        // authenticated settings endpoint instead of assuming launch is ready.
        for _ in 0..<60 {
            if let _ = try? await Task.detached(priority: .userInitiated, operation: {
                let client = try LocalIPCClient(secretStore: MacOSIPCSecretStore(), timeoutSeconds: 2)
                let response = try client.call(method: "settings.status")
                guard response.ok else { throw LocalIPCError.malformedResponse }
                return true
            }).value { return }
            try await Task.sleep(for: .seconds(1))
        }
        throw BundledDaemonSupervisorError.launchFailed
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminationReady { return .terminateNow }
        guard terminationTask == nil else { return .terminateCancel }
        terminationTask = Task { @MainActor in
            await daemonSupervisor.stopAndWait()
            terminationReady = true
            sender.terminate(nil)
        }
        // Keep the ordinary run loop alive while Swift concurrency drains the
        // child. terminateLater enters AppKit's termination loop, which can
        // prevent MainActor cleanup and presentation callbacks from running.
        return .terminateCancel
    }
}

@main
struct AegisMenuBarApp: App {
    @NSApplicationDelegateAdaptor(AegisAppDelegate.self) private var appDelegate

    private var model: MenuBarModel {
        appDelegate.model
    }

    private var interactive: Bool {
#if DEBUG
        return NotchPreviewMode(arguments: ProcessInfo.processInfo.arguments) == nil
#else
        return true
#endif
    }

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(model: model, interactive: interactive)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.window)

        Window("Chat con Jarvis", id: "chat") {
            JarvisChatView(model: model)
                .background(RequestedWindowRegistration(id: "chat"))
        }
        .defaultSize(width: 940, height: 720)
        .windowResizability(.contentMinSize)

        Settings {
            JarvisSettingsView(model: model, store: appDelegate.settingsStore,
                               restart: { try await appDelegate.restartDaemon() })
                .background(RequestedWindowRegistration(id: "settings", minimumContentSize: JarvisInterfaceStyle.settingsMinimumSize))
        }
        .defaultSize(width: 920, height: 660)
        .windowResizability(.contentMinSize)

        Window("Decisión de Jarvis", id: "approval") {
            ApprovalView(model: model)
                .background(RequestedWindowRegistration(id: "approval"))
        }
        .defaultSize(width: 560, height: 560)
        .windowResizability(.contentMinSize)

        Window("Activación por voz de Jarvis", id: "wake-word-enrollment") {
            WakeWordEnrollmentView(model: model)
                .background(RequestedWindowRegistration(id: "wake-word-enrollment"))
        }
        .defaultSize(width: 560, height: 600)
        .windowResizability(.contentMinSize)

        Window("Identidad de voz de Jarvis", id: "speaker-enrollment") {
            SpeakerEnrollmentView(model: model)
                .background(RequestedWindowRegistration(id: "speaker-enrollment"))
        }
        .defaultSize(width: 620, height: 700)
        .windowResizability(.contentMinSize)
    }
}

private struct MenuBarLabel: View {
    let model: MenuBarModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Label("Jarvis", systemImage: "waveform")
            .labelStyle(.iconOnly)
            .onChange(of: model.interactionID) { _, interactionID in
#if DEBUG
                if NotchPreviewMode(arguments: ProcessInfo.processInfo.arguments) != nil { return }
#endif
                guard interactionID != nil else { return }
                RequestedWindowFocus.open("approval") { openWindow(id: "approval") }
            }
            .task {
                let arguments = ProcessInfo.processInfo.arguments
#if DEBUG
                if let preview = NotchPreviewMode(arguments: arguments) {
                    model.applyNotchPreview(preview)
                    NotchPanelController.shared.show(model: model)
                    PresentationPreviewController.shared.show(model: model, mode: preview)
                    if preview == .cycle {
                        await model.runNotchPreviewCycle()
                    }
                    return
                }
#endif
                await model.initializeOperationalEvidence()
                await model.initializeProactiveAlerts()
                model.startPrivacyChangeMonitoring()
                NotchPanelController.shared.show(model: model)
                model.voiceShortcutAvailable = VoiceHotKeyController.shared.install {
                    Task { await model.startVoiceTurn() }
                }
                if arguments.contains("--hud") {
                    HUDPanelController.shared.show(model: model)
                }
                await model.initializeWakeWordListening()
                if arguments.contains("--enable-wake-word") {
                    await model.setWakeWordListeningEnabled(true)
                }
                if arguments.contains("--request-permissions") {
                    await model.requestAllPrivacyPermissions()
                }
                if arguments.contains("--request-computer-permissions") {
                    model.requestScreenCapture()
                    await model.requestComputerControlAccess()
                }
                if arguments.contains("--voice-turn") {
                    await model.startVoiceTurn()
                }
                await model.runBackgroundMonitoring()
            }
    }
}
