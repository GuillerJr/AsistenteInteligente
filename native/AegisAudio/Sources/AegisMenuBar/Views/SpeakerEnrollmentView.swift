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
            title: "Identidad local de voz",
            symbol: "person.2.fill",
            detail: "Perfiles para personalización y contexto privado. No autentican ni aprueban acciones. Espera a “Grabando” antes de hablar; cada muestra dura 3 segundos.",
            width: 620,
            height: 700
        ) {
            profileCreator
            VStack(alignment: .leading, spacing: 10) {
                Text("Muestras de voz")
                    .font(.system(size: 13, weight: .semibold))
                sampleRow(
                    title: "Fondo acústico",
                    subtitle: "Sonidos y voces no enroladas",
                    target: .background,
                    removable: false
                )
                ForEach(model.speakerEnrollmentProgress.profiles) { profile in
                    sampleRow(
                        title: profile.identifier,
                        subtitle: "Perfil local de voz",
                        target: .speaker(profile.identifier),
                        removable: true
                    )
                }
                if model.speakerEnrollmentProgress.profiles.isEmpty {
                    ContentUnavailableView(
                        "Sin perfiles",
                        systemImage: "person.2.slash",
                        description: Text("Añade al menos un perfil de voz.")
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
            "¿Eliminar este perfil local?",
            isPresented: Binding(
                get: { profilePendingRemoval != nil },
                set: { if !$0 { profilePendingRemoval = nil } }
            )
        ) {
            if let identifier = profilePendingRemoval {
                Button("Eliminar \(identifier)", role: .destructive) {
                    profilePendingRemoval = nil
                    Task { await model.removeSpeakerProfile(identifier) }
                }
            }
            Button("Cancelar", role: .cancel) {
                profilePendingRemoval = nil
            }
        } message: {
            Text("Se eliminarán únicamente sus clips de enrolamiento. Un modelo ya entrenado no cambia.")
        }
        .confirmationDialog(
            "¿Eliminar todas las muestras locales?",
            isPresented: $confirmingSampleDeletion
        ) {
            Button("Eliminar muestras", role: .destructive) {
                Task { await model.clearSpeakerSamples() }
            }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Se conservarán los nombres de perfil. Un modelo ya entrenado no se eliminará.")
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
                Button("Usar \(identifier) como propietario") {
                    pendingOwnerSelection = nil
                    model.setSpeakerOwnerIdentifier(identifier)
                }
            case .clear:
                Button("Quitar selección") {
                    pendingOwnerSelection = nil
                    model.setSpeakerOwnerIdentifier(nil)
                }
            case nil:
                EmptyView()
            }
            Button("Cancelar", role: .cancel) {
                pendingOwnerSelection = nil
            }
        } message: {
            Text(
                "Esta elección controla quién puede recibir continuidad y memoria privada. "
                    + "No concede control ni aprueba acciones."
            )
        }
    }

    private var profileCreator: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Perfiles de hablante")
                .font(.system(size: 13, weight: .semibold))
            HStack {
                TextField("Nombre del perfil · p. ej. guillermo", text: $newIdentifier)
                    .focused($profileNameFocused).jarvisInput(focused: profileNameFocused)
                    .accessibilityLabel("Identificador del nuevo perfil de voz")
                    .onSubmit(addProfile)
                Button("Añadir", action: addProfile)
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active)))
                    .disabled(!canAddProfile)
            }
            Text("Usa 2–32 caracteres: minúsculas, números, guion o guion bajo. Máximo 8 perfiles.")
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
            Label("Validando almacenamiento local…", systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case .updating:
            Label("Actualizando perfiles locales…", systemImage: "person.crop.circle.badge.clock")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case let .arming(target):
            Label("Prepárate para \(targetTitle(target))…", systemImage: "timer")
                .foregroundStyle(JarvisTheme.inkSecondary)
        case let .recording(target):
            Label("Grabando \(targetTitle(target))…", systemImage: "record.circle.fill")
                .foregroundStyle(JarvisTheme.failure)
        case .ready:
            Label("Muestras mínimas listas para entrenar", systemImage: "checkmark.circle.fill")
                .foregroundStyle(HUDStyle.accent(for: .success))
        case let .failed(error):
            Label(errorTitle(error), systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(HUDStyle.accent(for: .warning))
        }
    }

    private var ownerSelection: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Label("Contexto privado", systemImage: "person.crop.circle.badge.checkmark")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                JarvisMenuPicker(
                    title: "Perfil propietario",
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
            .init(value: nil, title: model.speakerIdentityIdentifiers.count == 1 ? "Automático" : "Sin seleccionar"),
        ]
        options += model.speakerIdentityIdentifiers.map { .init(value: Optional($0), title: $0) }
        if let selected = model.selectedSpeakerOwnerIdentifier,
           !model.speakerIdentityIdentifiers.contains(selected) {
            options.append(.init(value: Optional(selected), title: "\(selected) (inactivo)"))
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
                    "El modelo de voz cambió; confirma de nuevo a \(selected)",
                    systemImage: "arrow.triangle.2.circlepath.circle.fill"
                )
                .font(.caption)
                .foregroundStyle(HUDStyle.accent(for: .warning))
                Button("Confirmar propietario") {
                    pendingOwnerSelection = .select(selected)
                }
                .disabled(!model.canModifySpeakerEnrollment)
            }
        } else if let identifier = model.effectiveSpeakerOwnerIdentifier {
            Label(
                "\(identifier) puede recibir continuidad y memoria privada",
                systemImage: "checkmark.shield.fill"
            )
            .font(.caption)
            .foregroundStyle(HUDStyle.accent(for: .success))
        } else if let selected = model.selectedSpeakerOwnerIdentifier {
            Label(
                "El perfil \(selected) no existe en el modelo activo",
                systemImage: "exclamationmark.triangle.fill"
            )
            .font(.caption)
            .foregroundStyle(HUDStyle.accent(for: .warning))
        } else {
            Label(
                "Selecciona quién puede recibir contexto privado",
                systemImage: "lock.trianglebadge.exclamationmark"
            )
            .font(.caption)
            .foregroundStyle(HUDStyle.accent(for: .warning))
        }
    }

    private var activationHandoff: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Entrenar y activar")
                .font(.system(size: 13, weight: .semibold))
            if model.speakerIdentityCapability == .ready {
                Label("Modelo local activo", systemImage: "checkmark.shield.fill")
                    .foregroundStyle(HUDStyle.accent(for: .success))
            } else {
                switch model.speakerModelTrainingState {
                case .ready:
                    Label("Modelo local activo", systemImage: "checkmark.shield.fill")
                        .foregroundStyle(HUDStyle.accent(for: .success))
                case .training:
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text("Entrenando localmente… Puede tardar varios minutos.")
                            .font(.caption)
                            .foregroundStyle(JarvisTheme.inkSecondary)
                    }
                case let .failed(error):
                    Text(trainingErrorTitle(error))
                        .font(.caption)
                        .foregroundStyle(HUDStyle.accent(for: .warning))
                    Button("Reintentar entrenamiento", systemImage: "arrow.clockwise") {
                        Task { await model.trainSpeakerIdentityModel() }
                    }
                    .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                    .disabled(!canTrainModel)
                case .idle:
                    Text(
                        "Create ML procesará las muestras en este Mac. "
                            + "El audio y el modelo no salen del equipo."
                    )
                        .font(.caption)
                        .foregroundStyle(JarvisTheme.inkSecondary)
                    Button("Entrenar modelo local", systemImage: "cpu") {
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
            Button("Cerrar", role: .cancel) {
                dismissWindow(id: "speaker-enrollment")
            }
            .keyboardShortcut(.cancelAction)
            if hasSamples {
                Button("Eliminar muestras…", role: .destructive) {
                    confirmingSampleDeletion = true
                }
                .disabled(!model.canModifySpeakerEnrollment)
            }
            Spacer()
            if model.microphonePermission != .authorized {
                Button("Permitir micrófono") {
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
            "¿Elegir \(identifier) como propietario?"
        case .clear:
            "¿Quitar el perfil propietario?"
        case nil:
            "Confirmar perfil propietario"
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
            return "Faltan \(missing) perfiles de voz"
        }
        return "Listo para grabar una muestra"
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
        return "Grabar 3 s"
    }

    private func targetTitle(_ target: SpeakerEnrollmentTarget) -> String {
        target == .background ? "el ambiente" : "la voz de \(target.identifier)"
    }

    private func errorTitle(_ error: SpeakerEnrollmentError) -> String {
        switch error {
        case .sampleTooQuiet:
            "Muestra descartada: habla con voz clara"
        case .sampleClipped:
            "Muestra descartada: reduce el volumen o aléjate"
        case .permissionRequired:
            "El micrófono requiere permiso"
        case .invalidIdentifier:
            "El identificador no es válido"
        case .duplicateProfile:
            "Ese perfil ya existe"
        case .missingProfile:
            "El perfil ya no existe"
        case .capacityReached:
            "Se alcanzó el límite local"
        case .unsafeStorage:
            "El almacenamiento local no es seguro"
        case .invalidConfiguration, .invalidInputFormat, .recordingFailed:
            "No fue posible guardar la muestra"
        }
    }

    private func trainingErrorTitle(_ error: SpeakerModelTrainingError) -> String {
        switch error {
        case .helperUnavailable:
            "El entrenador firmado no está disponible en esta instalación"
        case .modelExists:
            "Ya existe un modelo local que Jarvis no debe sobrescribir"
        case .trainingFailed:
            "El dataset no superó el entrenamiento o la validación local"
        case .unsafeStorage:
            "La carpeta privada del modelo no cumple la política de seguridad"
        case .invalidModel:
            "El modelo generado no es compatible con identidad de voz"
        }
    }
}
