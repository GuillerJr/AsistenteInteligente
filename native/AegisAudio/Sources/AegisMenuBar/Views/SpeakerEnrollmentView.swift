import AegisAudioCore
import AppKit
import SwiftUI

struct SpeakerEnrollmentView: View {
    let model: MenuBarModel
    @Environment(\.dismissWindow) private var dismissWindow
    @State private var newIdentifier = ""
    @State private var profilePendingRemoval: String?
    @State private var confirmingSampleDeletion = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            header
            profileCreator

            ScrollView {
                VStack(spacing: 10) {
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
                            description: Text("Añade al menos dos personas distintas.")
                        )
                        .frame(height: 120)
                    }
                }
            }
            .frame(maxHeight: 350)

            status
            if model.speakerEnrollmentProgress.isReady {
                activationHandoff
            }
            footer
        }
        .padding(22)
        .frame(width: 620)
        .task {
            await model.refreshSpeakerEnrollment()
        }
        .onAppear {
            NSApp.activate(ignoringOtherApps: true)
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
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: "person.2.fill")
                .font(.system(size: 24, weight: .medium))
                .foregroundStyle(.cyan)
                .frame(width: 48, height: 48)
                .background(.cyan.opacity(0.1), in: Circle())
            VStack(alignment: .leading, spacing: 4) {
                Text("Identidad local de voz")
                    .font(.title2.weight(.semibold))
                Text("Jarvis usa estos perfiles solo para personalizar respuestas. No autentican ni aprueban acciones.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var profileCreator: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Perfiles de hablante")
                .font(.headline)
            HStack {
                TextField("identificador", text: $newIdentifier)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(addProfile)
                Button("Añadir", action: addProfile)
                    .disabled(!canAddProfile)
            }
            Text("Usa 2–32 caracteres: minúsculas, números, guion o guion bajo. Máximo 8 perfiles.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func sampleRow(
        title: String,
        subtitle: String,
        target: SpeakerEnrollmentTarget,
        removable: Bool
    ) -> some View {
        let count = model.speakerEnrollmentProgress.count(for: target)
        return VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 10) {
                Image(systemName: target == .background ? "waveform" : "person.crop.circle.fill")
                    .font(.system(size: 18, weight: .medium))
                    .foregroundStyle(target == .background ? Color.secondary : Color.cyan)
                    .frame(width: 28)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.headline)
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if removable {
                    Button("Eliminar", systemImage: "trash", role: .destructive) {
                        profilePendingRemoval = target.identifier
                    }
                    .labelStyle(.iconOnly)
                    .disabled(!model.canModifySpeakerEnrollment)
                    .help("Eliminar perfil \(title)")
                }
                Button(recordButtonTitle(for: target)) {
                    Task { await model.recordSpeakerSample(target) }
                }
                .disabled(!model.canRecordSpeakerSample)
            }
            Text("Siguiente: \(SpeakerEnrollmentGuidance.instruction(target: target, acceptedCount: count))")
                .font(.caption)
                .foregroundStyle(.secondary)
            ProgressView(
                value: Double(min(count, SpeakerEnrollmentProgress.targetPerLabel)),
                total: Double(SpeakerEnrollmentProgress.targetPerLabel)
            )
            Text("\(count) de \(SpeakerEnrollmentProgress.targetPerLabel) mínimas")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
        }
        .padding(13)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var status: some View {
        switch model.speakerEnrollmentState {
        case .idle:
            Label(readinessSummary, systemImage: "checkmark.circle")
                .foregroundStyle(.secondary)
        case .loading:
            Label("Validando almacenamiento local…", systemImage: "arrow.triangle.2.circlepath")
                .foregroundStyle(.secondary)
        case .updating:
            Label("Actualizando perfiles locales…", systemImage: "person.crop.circle.badge.clock")
                .foregroundStyle(.secondary)
        case let .arming(target):
            Label("Prepárate para \(targetTitle(target))…", systemImage: "timer")
                .foregroundStyle(.secondary)
        case let .recording(target):
            Label("Grabando \(targetTitle(target))…", systemImage: "record.circle.fill")
                .foregroundStyle(.red)
        case .ready:
            Label("Dataset mínimo listo para entrenamiento local", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case let .failed(error):
            Label(errorTitle(error), systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
        }
    }

    private var activationHandoff: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Entrenar y activar")
                .font(.headline)
            if model.speakerIdentityCapability == .ready {
                Label("Modelo local activo", systemImage: "checkmark.shield.fill")
                    .foregroundStyle(.green)
            } else {
                switch model.speakerModelTrainingState {
                case .ready:
                    Label("Modelo local activo", systemImage: "checkmark.shield.fill")
                        .foregroundStyle(.green)
                case .training:
                    HStack(spacing: 10) {
                        ProgressView().controlSize(.small)
                        Text("Entrenando localmente… Puede tardar varios minutos.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                case let .failed(error):
                    Text(trainingErrorTitle(error))
                        .font(.caption)
                        .foregroundStyle(.orange)
                    Button("Reintentar entrenamiento", systemImage: "arrow.clockwise") {
                        Task { await model.trainSpeakerIdentityModel() }
                    }
                    .disabled(!canTrainModel)
                case .idle:
                    Text(
                        "Create ML procesará las muestras en este Mac. "
                            + "El audio y el modelo no salen del equipo."
                    )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Entrenar modelo local", systemImage: "cpu") {
                        Task { await model.trainSpeakerIdentityModel() }
                    }
                    .disabled(!canTrainModel)
                }
            }
        }
        .padding(13)
        .background(.green.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }

    private var footer: some View {
        HStack {
            Button("Cerrar", role: .cancel) {
                dismissWindow(id: "speaker-enrollment")
            }
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
