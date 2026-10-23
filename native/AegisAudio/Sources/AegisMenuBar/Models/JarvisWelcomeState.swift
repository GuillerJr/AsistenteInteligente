import Foundation

/// Only records that the owner finished or skipped the introduction. No secrets or permissions.
enum JarvisWelcomeState {
    static let completionKey = "jarvis.welcome.completedVersion"
    static let version = 1

    static func shouldPresent(completedVersion: Int, credential: String?) -> Bool {
        completedVersion < version || (credential != nil && credential != "configured")
    }

    static let example = String(localized: "Hola, Jarvis. ¿En qué puedes ayudarme hoy?", bundle: .module)

    enum ExampleIssue: Equatable {
        case credential, settings, activity, service, submission

        var message: String {
            switch self {
            case .credential:
                String(localized: "Conecta tu cuenta de ChatGPT en Ajustes › Modelos de IA antes de enviar el mensaje de ejemplo.", bundle: .module)
            case .settings:
                String(localized: "Termina los cambios de configuración y aplícalos desde Ajustes antes de enviar el ejemplo.", bundle: .module)
            case .activity:
                String(localized: "Termina la conversación, grabación o aprobación pendiente antes de enviar el ejemplo.", bundle: .module)
            case .service:
                String(localized: "El servicio todavía no está listo. Comprueba la conexión con Codex y vuelve a intentarlo.", bundle: .module)
            case .submission:
                String(localized: "El mensaje de ejemplo no se completó. Revisa el chat y vuelve a intentarlo cuando esté disponible.", bundle: .module)
            }
        }
    }

    @MainActor
    static func exampleIssue(model: MenuBarModel, store: JarvisSettingsStore) -> ExampleIssue? {
        let connected = store.inference.status.map(\.ready)
            ?? (model.hybridBrainReady || store.snapshot?.credential == "configured")
        guard connected else { return .credential }
        guard !store.busy, !store.restartRequired else { return .settings }
        guard model.activeJobID == nil, model.pendingApproval == nil, model.pendingInput == nil,
              model.pendingBrowserSelection == nil, model.pendingRecovery == nil,
              !model.voiceState.isBusy, !model.chat.isEditing, !model.chat.historyLoading,
              !model.meetingRecording, !model.speakerModelTrainingState.isBusy,
              !model.speakerEnrollmentState.isBusy, !model.wakeWordEnrollmentState.isBusy,
              model.queuedChatMessages.isEmpty else { return .activity }
        guard model.userSessionAvailable else { return .service }
        return nil
    }

    /// Refresh before switching conversations, then recheck: voice or an approval can arrive
    /// while IPC is suspended. Refresh the connected account before sending the example.
    @MainActor
    static func sendExample(model: MenuBarModel, store: JarvisSettingsStore,
                            refresh: () async -> Void, send: () async -> Bool) async -> ExampleIssue? {
        if let issue = exampleIssue(model: model, store: store) { return issue }
        await refresh()
        if let issue = exampleIssue(model: model, store: store) { return issue }
        guard !Task.isCancelled, model.canManageChatHistory, model.canSendChatMessage else {
            return .service
        }
        model.chat.newConversation()
        let consumed = await send()
        // sendChatMessage consumes the composer text even when IPC rejects the submission.
        guard consumed, model.userSessionAvailable, model.chat.error == nil,
              model.chat.transcript.messages.last?.interrupted != true else { return .submission }
        return nil
    }
}
