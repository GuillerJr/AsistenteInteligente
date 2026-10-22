import AegisAudioCore
import AppKit
import SwiftUI

private enum SpeakerOwnerSelectionChange: Equatable {
    case select(String)
    case clear
}

struct SpeakerEnrollmentView: View {
    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow
    @State private var newIdentifier = ""
    @State private var profilePendingRemoval: String?
    @State private var confirmingSampleDeletion = false
    @State private var pendingOwnerSelection: SpeakerOwnerSelectionChange?
    @FocusState private var profileNameFocused: Bool

    var body: some View {
        VoiceEnrollmentPanel(
            title: String(localized: "Identidad local de voz", bundle: .module),
            symbol: "person.2.fill",
            detail: String(localized: "Perfiles para personalización y contexto privado. No autentican ni aprueban acciones. Espera a “Grabando” antes de hablar; cada muestra dura 3 segundos.", bundle: .module),
            width: 620,
            height: 700
        ) {
            profileCreator
            VStack(alignment: .leading, spacing: 10) {
                Text(String(localized: "Muestras de voz", bundle: .module))
                    .font(.system(size: 13, weight: .semibold))
                sampleRow(
                    title: String(localized: "Fondo acústico", bundle: .module),
                    subtitle: String(localized: "Sonidos y voces no enroladas", bundle: .module),
                    target: .background,
                    removable: false
                )
                ForEach(model.speakerEnrollmentProgress.profiles) { profile in
                    sampleRow(
                        title: profile.identifier,
                        subtitle: String(localized: "Perfil local de voz", bundle: .module),
                        target: .speaker(profile.identifier),
                        removable: true
                    )
                }
                if model.speakerEnrollmentProgress.profiles.isEmpty {
                    ContentUnavailableView(
                        String(localized: "Sin perfiles", bundle: .module),
                        systemImage: "person.2.slash",
                        description: Text(String(localized: "Añade al menos un perfil de voz.", bundle: .module))
                    )
                    .frame(height: 120)
                    .foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            if model.speakerIdentityCapability == .ready {
                ownerSelection
            }
            if model.speakerEnrollmentProgress.isReady {
                activationHandoff
            }
        } status: {
            status
        } footer: {
            footer
        }
        .task {
            await model.refreshSpeakerEnrollment()
            await model.refreshSpeakerIdentityConfiguration()
        }
        .confirmationDialog(
            String(localized: "¿Eliminar este perfil local?", bundle: .module),
            isPresented: Binding(
                get: { profilePendingRemoval != nil },
                set: { if !$0 { profilePendingRemoval = nil } }
            )
        ) {
            if let identifier = profilePendingRemoval {
                Button(String(localized: "Eliminar \(identifier)", bundle: .module), role: .destructive) {
                    profilePendingRemoval = nil
                    Task { await model.removeSpeakerProfile(identifier) }
                }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {
                profilePendingRemoval = nil
            }
        } message: {
            Text(String(localized: "Se eliminarán únicamente sus clips de enrolamiento. Un modelo ya entrenado no cambia.", bundle: .module))
        }
        .confirmationDialog(
            String(localized: "¿Eliminar todas las muestras locales?", bundle: .module),
            isPresented: $confirmingSampleDeletion
        ) {
            Button(String(localized: "Eliminar muestras", bundle: .module), role: .destructive) {
                Task { await model.clearSpeakerSamples() }
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {}
        } message: {
            Text(String(localized: "Se conservarán los nombres de perfil. Un modelo ya entrenado no se eliminará.", bundle: .module))
        }
        .confirmationDialog(
            ownerConfirmationTitle,
            isPresented: Binding(
                get: { pendingOwnerSelection != nil },
                set: { if !$0 { pendingOwnerSelection = nil } }
            )
        ) {
            switch pendingOwnerSelection {
            case let .select(identifier):
                Button(String(localized: "Usar \(identifier) como propietario", bundle: .module)) {
                    pendingOwnerSelection = nil
                    model.setSpeakerOwnerIdentifier(identifier)
                }
            case .clear:
                Button(String(localized: "Quitar selección", bundle: .module)) {
                    pendingOwnerSelection = nil
                    model.setSpeakerOwnerIdentifier(nil)
                }
            case nil:
                EmptyView()
            }
            Button(String(localized: "Cancelar", bundle: .module), role: .cancel) {
                pendingOwnerSelection = nil
            }
        } message: {
            Text(
                String(localized: "Esta elección controla quién puede recibir continuidad y memoria privada. ", bundle: .module)
                    + String(localized: "No concede control ni aprueba acciones.", bundle: .module)
            )
        }
    }

    private var profileCreator: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(String(localized: "Perfiles de hablante", bundle: .module))
                .font(.system(size: 13, weight: .semibold))
            HStack {
                TextField(String(localized: "Nombre del perfil · p. ej. guillermo", bundle: .module), text: $newIdentifier)
                    .focused($profileNameFocused).jarvisInput(focused: profileNameFocused)
                    .accessibilityLabel(String(localized: "Identificador del nuevo perfil de voz", bundle: .module))
                    .onSubmit(addProfile)
                Button(String(localized: "Añadir", bundle: .module), action: addProfile)
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active)))
                    .disabled(!canAddProfile)
            }
            Text(String(localized: "Usa 2–32 caracteres: minúsculas, números, guion o guion bajo. Máximo 8 perfiles.", bundle: .module))
                .font(.caption)
                .foregroundStyle(JarvisTheme.inkSecondary)
        }
    }

    private func sampleRow(
        title: String,
        subtitle: String,
        target: SpeakerEnrollmentTarget,
        removable: Bool
    ) -> some View {
        let count = model.speakerEnrollmentProgress.count(for: target)
        let recording: Bool
        if case let .recording(activeTarget) = model.speakerEnrollmentState {
            recording = activeTarget == target
        } else {
            recording = false
        }
        return VoiceEnrollmentSampleRow(
            title: title, subtitle: subtitle,
            symbol: target == .background ? "waveform" : "person.crop.circle.fill",
            guidance: SpeakerEnrollmentGuidance.instruction(target: target, acceptedCount: count),
            count: count, targetCount: SpeakerEnrollmentProgress.targetPerLabel,
            buttonTitle: recordButtonTitle(for: target), canRecord: model.canRecordSpeakerSample,
            isRecording: recording,
            record: { Task { await model.recordSpeakerSample(target) } },
            canRemove: model.canModifySpeakerEnrollment,
            remove: removable ? { profilePendingRemoval = target.identifier } : nil
        )
    }

    @ViewBuilder
    private var status: some View {
        switch model.speakerEnrollmentState {
        case .idle:
            Label(readinessSummary, systemImage: "checkmark.circle")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case .loading:
            Label(String(localized: "Validando almacenamiento local…", bundle: .module), systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case .updating:
            Label(String(localized: "Actualizando perfiles locales…", bundle: .module), systemImage: "person.crop.circle.badge.clock")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case let .arming(target):
            Label(String(localized: "Prepárate para \(targetTitle(target))…", bundle: .module), systemImage: "timer")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case let .recording(target):
            Label(String(localized: "Grabando \(targetTitle(target))…", bundle: .module), systemImage: "record.circle.fill")
                .foregroundStyle(JarvisTheme.failure)
        case .ready:
            Label(String(localized: "Muestras mínimas listas para entrenar", bundle: .module), systemImage: "checkmark.circle.fill")
                .foregroundStyle(HUDStyle.accent(for: .success))
        case let .failed(error):
            Label(errorTitle(error), systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(HUDStyle.accent(for: .warning))
        }
    }

    private var ownerSelection: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label(String(localized: "Contexto privado", bundle: .module), systemImage: "person.crop.circle.badge.checkmark")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                JarvisMenuPicker(
                    title: String(localized: "Perfil propietario", bundle: .module),
                    selection: Binding<String?>(
                        get: { model.selectedSpeakerOwnerIdentifier },
                        set: proposeOwnerSelection
                    ), options: ownerOptions
                )
                .frame(width: 230)
                .disabled(!model.canModifySpeakerEnrollment)
            }
            ownerSelectionStatus
        }
        .padding(13)
        .voiceEnrollmentCard()
    }

    private var ownerOptions: [JarvisSelectionOption<String?>] {
        var options: [JarvisSelectionOption<String?>] = [
            .init(value: nil, title: model.speakerIdentityIdentifiers.count == 1 ? String(localized: "Automático", bundle: .module) : String(localized: "Sin seleccionar", bundle: .module)),
        ]
        options += model.speakerIdentityIdentifiers.map { .init(value: Optional($0), title: $0) }
        if let selected = model.selectedSpeakerOwnerIdentifier,
           !model.speakerIdentityIdentifiers.contains(selected) {
            options.append(.init(value: Optional(selected), title: String(localized: "\(selected) (inactivo)", bundle: .module)))
        }
        return options
    }

    @ViewBuilder
    private var ownerSelectionStatus: some View {
        if model.speakerOwnerSelectionNeedsReconfirmation,
           let selected = model.selectedSpeakerOwnerIdentifier
        {
            VStack(alignment: .leading, spacing: 7) {
                Label(
                    String(localized: "El modelo de voz cambió; confirma de nuevo a \(selected)", bundle: .module),
                    systemImage: "arrow.triangle.2.circlepath.circle.fill"
                )
                .font(.caption)
                .foregroundStyle(HUDStyle.accent(for: .warning))
                Button(String(localized: "Confirmar propietario", bundle: .module)) {
                    pendingOwnerSelection = .select(selected)
                }
                .disabled(!model.canModifySpeakerEnrollment)
            }
        } else if let identifier = model.effectiveSpeakerOwnerIdentifier {
            Label(
                String(localized: "\(identifier) puede recibir continuidad y memoria privada", bundle: .module),
                systemImage: "checkmark.shield.fill"
            )
            .font(.caption)
            .foregroundStyle(HUDStyle.accent(for: .success))
        } else if let selected = model.selectedSpeakerOwnerIdentifier {
            Label(
                String(localized: "El perfil \(selected) no existe en el modelo activo", bundle: .module),
                systemImage: "exclamationmark.triangle.fill"
            )
            .font(.caption)
            .foregroundStyle(HUDStyle.accent(for: .warning))
        } else {
            Label(
                String(localized: "Selecciona quién puede recibir contexto privado", bundle: .module),
                systemImage: "lock.trianglebadge.exclamationmark"
            )
            .font(.caption)
            .foregroundStyle(HUDStyle.accent(for: .warning))
        }
    }

    private var activationHandoff: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(String(localized: "Entrenar y activar", bundle: .module))
                .font(.system(size: 13, weight: .semibold))
            if model.speakerIdentityCapability == .ready {
                Label(String(localized: "Modelo local activo", bundle: .module), systemImage: "checkmark.shield.fill")
                    .foregroundStyle(HUDStyle.accent(for: .success))
            } else {
                switch model.speakerModelTrainingState {
                case .ready:
                    Label(String(localized: "Modelo local activo", bundle: .module), systemImage: "checkmark.shield.fill")
                        .foregroundStyle(HUDStyle.accent(for: .success))
                case .training:
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text(String(localized: "Entrenando localmente… Puede tardar varios minutos.", bundle: .module))
                            .font(.caption)
                            .foregroundStyle(JarvisTheme.inkSecondary)
                    }
                case let .failed(error):
                    Text(trainingErrorTitle(error))
                        .font(.caption)
                        .foregroundStyle(HUDStyle.accent(for: .warning))
                    Button(String(localized: "Reintentar entrenamiento", bundle: .module), systemImage: "arrow.clockwise") {
                        Task { await model.trainSpeakerIdentityModel() }
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!canTrainModel)
                case .idle:
                    Text(
                        String(localized: "Create ML procesará las muestras en este Mac. ", bundle: .module)
                            + String(localized: "El audio y el modelo no salen del equipo.", bundle: .module)
                    )
                        .font(.caption)
                        .foregroundStyle(JarvisTheme.inkSecondary)
                    Button(String(localized: "Entrenar modelo local", bundle: .module), systemImage: "cpu") {
                        Task { await model.trainSpeakerIdentityModel() }
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!canTrainModel)
                }
            }
        }
        .padding(13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .voiceEnrollmentCard()
    }

    private var footer: some View {
        HStack {
            Button(String(localized: "Cerrar", bundle: .module), role: .cancel) {
                dismissWindow(id: "speaker-enrollment")
            }
            .keyboardShortcut(.cancelAction)
            if hasSamples {
                Button(String(localized: "Eliminar muestras…", bundle: .module), role: .destructive) {
                    confirmingSampleDeletion = true
                }
                .disabled(!model.canModifySpeakerEnrollment)
            }
            Spacer()
            if model.microphonePermission != .authorized {
                Button(String(localized: "Permitir micrófono", bundle: .module)) {
                    Task { await model.requestMicrophone() }
                }
            }
        }
    }

    private var normalizedIdentifier: String {
        newIdentifier.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    private var canAddProfile: Bool {
        SpeakerIdentityCapability.isValidSpeakerLabel(normalizedIdentifier)
            && model.speakerEnrollmentProgress.profiles.count
                < SpeakerIdentityCapability.maximumSpeakerCount
            && !model.speakerEnrollmentProgress.profiles.contains {
                $0.identifier == normalizedIdentifier
            }
            && model.canModifySpeakerEnrollment
    }

    private var canTrainModel: Bool {
        model.speakerEnrollmentProgress.isReady
            && model.speakerIdentityCapability != .ready
            && model.canModifySpeakerEnrollment
    }

    private func addProfile() {
        guard canAddProfile else { return }
        let identifier = normalizedIdentifier
        newIdentifier = ""
        Task { await model.addSpeakerProfile(identifier) }
    }

    private func proposeOwnerSelection(_ identifier: String?) {
        guard
            identifier != model.selectedSpeakerOwnerIdentifier
                || model.speakerOwnerSelectionNeedsReconfirmation
        else {
            return
        }
        pendingOwnerSelection = identifier.map(SpeakerOwnerSelectionChange.select) ?? .clear
    }

    private var ownerConfirmationTitle: String {
        switch pendingOwnerSelection {
        case let .select(identifier):
            String(localized: "¿Elegir \(identifier) como propietario?", bundle: .module)
        case .clear:
            String(localized: "¿Quitar el perfil propietario?", bundle: .module)
        case nil:
            String(localized: "Confirmar perfil propietario", bundle: .module)
        }
    }

    private var hasSamples: Bool {
        model.speakerEnrollmentProgress.backgroundCount > 0
            || model.speakerEnrollmentProgress.profiles.contains { $0.sampleCount > 0 }
    }

    private var readinessSummary: String {
        let missing = max(
            0,
            SpeakerIdentityCapability.minimumSpeakerCount
                - model.speakerEnrollmentProgress.profiles.count
        )
        if missing > 0 {
            return String(localized: "Faltan \(missing) perfiles de voz", bundle: .module)
        }
        return String(localized: "Listo para grabar una muestra", bundle: .module)
    }

    private func recordButtonTitle(for target: SpeakerEnrollmentTarget) -> String {
        if case let .recording(activeTarget) = model.speakerEnrollmentState,
           activeTarget == target
        {
            return "Grabando…"
        }
        if case let .arming(activeTarget) = model.speakerEnrollmentState,
           activeTarget == target
        {
            return "Preparando…"
        }
        return String(localized: "Grabar 3 s", bundle: .module)
    }

    private func targetTitle(_ target: SpeakerEnrollmentTarget) -> String {
        target == .background ? String(localized: "el ambiente", bundle: .module) : String(localized: "la voz de \(target.identifier)", bundle: .module)
    }

    private func errorTitle(_ error: SpeakerEnrollmentError) -> String {
        switch error {
        case .sampleTooQuiet:
            String(localized: "Muestra descartada: habla con voz clara", bundle: .module)
        case .sampleClipped:
            String(localized: "Muestra descartada: reduce el volumen o aléjate", bundle: .module)
        case .permissionRequired:
            String(localized: "El micrófono requiere permiso", bundle: .module)
        case .invalidIdentifier:
            String(localized: "El identificador no es válido", bundle: .module)
        case .duplicateProfile:
            String(localized: "Ese perfil ya existe", bundle: .module)
        case .missingProfile:
            String(localized: "El perfil ya no existe", bundle: .module)
        case .capacityReached:
            String(localized: "Se alcanzó el límite local", bundle: .module)
        case .unsafeStorage:
            String(localized: "El almacenamiento local no es seguro", bundle: .module)
        case .invalidConfiguration, .invalidInputFormat, .recordingFailed:
            String(localized: "No fue posible guardar la muestra", bundle: .module)
        }
    }

    private func trainingErrorTitle(_ error: SpeakerModelTrainingError) -> String {
        switch error {
        case .helperUnavailable:
            String(localized: "El entrenador firmado no está disponible en esta instalación", bundle: .module)
        case .modelExists:
            String(localized: "Ya existe un modelo local que Jarvis no debe sobrescribir", bundle: .module)
        case .trainingFailed:
            String(localized: "El dataset no superó el entrenamiento o la validación local", bundle: .module)
        case .unsafeStorage:
            String(localized: "La carpeta privada del modelo no cumple la política de seguridad", bundle: .module)
        case .invalidModel:
            String(localized: "El modelo generado no es compatible con identidad de voz", bundle: .module)
        }
    }
}
