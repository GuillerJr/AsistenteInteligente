import AppKit
import AegisAudioCore
import Carbon
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
        // Diagnose the installed signed VAD with synthetic silence, before any
        // daemon, microphone, login-item migration or normal UI starts.
        if ProcessInfo.processInfo.arguments.contains("--vad-self-test") {
            do {
                let detector = try SileroVADDetector(bundle: .main)
                for _ in 0..<2 {
                    let observations = try detector.append(samples: [Float](repeating: 0, count: 512))
                    guard observations.count == 1,
                          observations[0].speechProbability.isFinite,
                          (0...1).contains(observations[0].speechProbability) else {
                        throw SileroVADError.inferenceFailed
                    }
                    detector.reset()
                }
                print("vad_self_test=passed frames=2 synthetic=true")
                exit(EXIT_SUCCESS)
            } catch {
                FileHandle.standardError.write(Data("vad_self_test=failed\n".utf8))
                exit(EXIT_FAILURE)
            }
        }
        JarvisAppearanceController.shared.start(application: .shared)
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
        if ProcessInfo.processInfo.arguments.contains("--disable-login-item") {
            do { _ = try JarvisLoginItemController.shared.setEnabled(false) }
            catch { return }
            terminationReady = true
            NSApplication.shared.terminate(nil)
            return
        }
        Task { await JarvisLoginItemController.shared.migrateLegacyIfNeeded() }
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
        NotchPanelController.shared.close()
        JarvisAppearanceController.shared.stop()
        daemonSupervisor.stop()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // SwiftUI owns NSApp's actual delegate and can consume reopen events
        // without forwarding applicationShouldHandleReopen to the adaptor.
        // Handle this one event explicitly; URL and document events stay intact.
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(handleReopen(_:withReplyEvent:)),
            forEventClass: AEEventClass(kCoreEventClass),
            andEventID: AEEventID(kAEReopenApplication)
        )
    }

    @objc private func handleReopen(_ event: NSAppleEventDescriptor,
                                    withReplyEvent reply: NSAppleEventDescriptor) {
        JarvisDockController.shared.restoreLastWindow()
    }

    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        JarvisDockController.shared.dockMenu()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        // We handle existing scenes ourselves, so AppKit need not invent a new
        // primary window when Jarvis is running only in the menu bar.
        JarvisDockController.shared.restoreLastWindow()
        return false
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
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
            MenuBarLabel(model: model, settingsStore: appDelegate.settingsStore)
        }
        .menuBarExtraStyle(.window)
        .commands { JarvisSettingsCommands(model: model, interactive: interactive) }

        Window("Bienvenido a Jarvis", id: "welcome") {
            JarvisWelcomeView(model: model, store: appDelegate.settingsStore,
                              restart: { try await appDelegate.restartDaemon() }, interactive: interactive)
                .background(RequestedWindowRegistration(id: "welcome"))
        }
        .defaultSize(width: 650, height: 620)
        .windowResizability(.contentMinSize)

        Window("Chat con Jarvis", id: "chat") {
            JarvisChatView(model: model)
                .background(RequestedWindowRegistration(id: "chat"))
        }
        .defaultSize(width: 940, height: 720)
        .windowResizability(.contentMinSize)

        Window("Configuración de Jarvis", id: "settings") {
            JarvisSettingsView(model: model, store: appDelegate.settingsStore,
                               restart: { try await appDelegate.restartDaemon() }, interactive: interactive)
                .background(RequestedWindowRegistration(id: "settings"))
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
    let settingsStore: JarvisSettingsStore
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        Label("Jarvis", systemImage: "waveform")
            .labelStyle(.iconOnly)
            .onChange(of: model.interactionID) { _, interactionID in
#if DEBUG
                if NotchPreviewMode(arguments: ProcessInfo.processInfo.arguments) != nil { return }
#endif
                guard interactionID != nil else {
                    RequestedWindowFocus.close("approval") { dismissWindow(id: "approval") }
                    return
                }
                NotchPanelController.shared.dismissForInteractionReview()
                RequestedWindowFocus.open("approval") { openWindow(id: "approval") }
            }
            .task {
                let arguments = ProcessInfo.processInfo.arguments
#if DEBUG
                if let preview = NotchPreviewMode(arguments: arguments) {
                    model.applyNotchPreview(preview)
                    NotchPanelController.shared.show(model: model, interactive: false)
                    PresentationPreviewController.shared.show(model: model, mode: preview)
                    if preview == .cycle {
                        await model.runNotchPreviewCycle()
                    }
                    return
                }
#endif
                await settingsStore.refresh()
                if JarvisWelcomeState.shouldPresent(
                    completedVersion: UserDefaults.standard.integer(forKey: JarvisWelcomeState.completionKey),
                    credential: settingsStore.snapshot?.credential
                ) {
                    RequestedWindowFocus.open("welcome") { openWindow(id: "welcome") }
                }
                await model.initializeOperationalEvidence()
                await model.initializeProactiveAlerts()
                model.startPrivacyChangeMonitoring()
                NotchPanelController.shared.show(model: model, reviewInteraction: {
                    guard model.interactionID != nil else { return }
                    NotchPanelController.shared.dismissForInteractionReview()
                    RequestedWindowFocus.open("approval") { openWindow(id: "approval") }
                })
                model.voiceShortcutAvailable = VoiceHotKeyController.shared.install {
                    Task { await model.startVoiceTurn() }
                }
                VoiceHotKeyController.shared.installHold(
                    started: { model.voiceHoldStarted() },
                    ended: { model.voiceHoldEnded() }
                )
                VoiceHotKeyController.shared.installQuickEntry {
                    QuickEntryPanelController.shared.toggle(model: model)
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
