@preconcurrency import AVFoundation
import DesktopCore
import Foundation
import Observation

@MainActor protocol MediaPlaybackPlayerDriver: AnyObject {
    var player: AVPlayer? { get }
    var onFailure: (@MainActor () -> Void)? { get set }
    var outputDeviceID: String? { get }
    func prepare(_ source: MemoryMediaAsset)
    func selectOutput(_ id: String)
    func pause()
    func close()
}

@MainActor final class AVMediaPlaybackPlayerDriver: MediaPlaybackPlayerDriver {
    private(set) var player: AVPlayer?
    var onFailure: (@MainActor () -> Void)?
    private var statusObservation: NSKeyValueObservation?
    private var generation = 0
    var outputDeviceID: String? { player?.audioOutputDeviceUniqueID }
    func prepare(_ source: MemoryMediaAsset) {
        close()
        let item = AVPlayerItem(asset: source.asset), epoch = generation
        let player = AVPlayer(playerItem: item)
        player.allowsExternalPlayback = false
        self.player = player
        statusObservation = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            guard item.status == .failed else { return }
            Task { @MainActor [weak self] in
                guard let self, self.generation == epoch, self.player?.currentItem === item else { return }
                self.onFailure?()
            }
        }
    }
    func selectOutput(_ id: String) { player?.audioOutputDeviceUniqueID = id }
    func pause() { player?.pause() }
    func close() {
        generation += 1; statusObservation?.invalidate(); statusObservation = nil
        player?.pause(); player?.replaceCurrentItem(with: nil); player = nil
    }
}

@MainActor @Observable final class MediaPlayback {
    private(set) var player: AVPlayer?
    private(set) var availableOutputs: [LocalMediaOutputDevice] = []
    private(set) var selectedOutputID: String?
    private(set) var resolvedOutputID: String?
    private(set) var issue: String?
    private(set) var resourceID: String?
    private(set) var ready = false
    @ObservationIgnored private let outputs: any LocalMediaOutputDriver
    @ObservationIgnored private let playerDriver: any MediaPlaybackPlayerDriver
    @ObservationIgnored private var observation: LocalMediaOutputObservation?
    @ObservationIgnored private var source: MemoryMediaAsset?
    @ObservationIgnored private var preview: MediaPreview?
    @ObservationIgnored private var generation = 0

    init(outputs: (any LocalMediaOutputDriver)? = nil, playerDriver: (any MediaPlaybackPlayerDriver)? = nil) {
        self.outputs = outputs ?? CoreAudioLocalMediaOutputDriver()
        self.playerDriver = playerDriver ?? AVMediaPlaybackPlayerDriver()
        self.playerDriver.onFailure = { [weak self] in
            guard let self, self.preview != nil else { return }
            self.retirePlayer(); self.issue = "El medio no pudo reproducirse localmente. Se liberó el reproductor."
        }
    }
    func prepare(_ value: MediaPreview) {
        stop(); preview = value
        resourceID = (value.attachment.scope.temporary ? "temporary:" : "persistent:") + value.attachment.scope.sessionID
            + ":" + value.attachment.workspaceID + ":" + value.attachment.id + ":" + value.attachment.sha256
            + ":" + (value.playbackSHA ?? value.attachment.sha256)
        selectOutput(nil)
    }
    func selectOutput(_ id: String?) {
        guard let preview else { return }
        // An explicit selection never resumes automatically or changes the
        // selected resource, seek position, or system default output.
        playerDriver.pause()
        selectedOutputID = id
        do {
            let metadata = try MediaPreviewMetadata(preview), snapshot = try outputs.snapshot()
            availableOutputs = snapshot.devices
            let device = try LocalMediaOutputPolicy.resolve(selectedID: id, snapshot: snapshot)
            try installObserver()
            if !ready {
                let source = try MemoryMediaAsset(data: preview.data, mimeType: metadata.playbackMIME)
                self.source = source; playerDriver.prepare(source)
            }
            playerDriver.selectOutput(device.id)
            guard playerDriver.outputDeviceID == device.id,
                  LocalMediaOutputPolicy.isCurrent(selectedID: id, resolvedID: device.id, snapshot: try outputs.snapshot()) else { throw LocalMediaOutputIssue.changed }
            resolvedOutputID = device.id; player = playerDriver.player; ready = true; issue = nil
        } catch { retirePlayer(); issue = (error as? LocalizedError)?.errorDescription ?? "No se pudo preparar la reproducción local." }
    }
    private func installObserver() throws {
        observation?.cancel(); observation = nil
        let epoch = generation
        observation = try outputs.observe { [weak self] in
            guard let self, self.generation == epoch, self.preview != nil else { return }
            self.outputsChanged()
        }
    }
    private func outputsChanged() {
        do {
            let snapshot = try outputs.snapshot(); availableOutputs = snapshot.devices
            if let resolvedOutputID, !LocalMediaOutputPolicy.isCurrent(selectedID: selectedOutputID, resolvedID: resolvedOutputID, snapshot: snapshot) {
                retirePlayer(); issue = LocalMediaOutputIssue.changed.errorDescription
            }
            try installObserver()
        } catch { retirePlayer(); observation?.cancel(); observation = nil; issue = LocalMediaOutputIssue.monitoringUnavailable.errorDescription }
    }
    private func retirePlayer() {
        playerDriver.close(); player = nil; source?.close(); source = nil; ready = false; resolvedOutputID = nil
    }
    func stop() {
        generation += 1; observation?.cancel(); observation = nil
        retirePlayer(); preview = nil; resourceID = nil; selectedOutputID = nil; availableOutputs = []; issue = nil
    }
    deinit {
        let observation = observation, driver = playerDriver, source = source
        observation?.cancel()
        Task { @MainActor in driver.close(); source?.close() }
    }
}
