import AegisAudioCore
import ServiceManagement
import SwiftUI

struct JarvisGeneralSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    @State private var loginEnabled = SMAppService.mainApp.status == .enabled
    @State private var shortcut = JarvisVoiceShortcut.current
    @State private var personalityPreset = "default"
    @State private var personalityCustom = ""
    @FocusState private var personalityFocused: Bool

    var body: some View {
        JarvisSettingsCard(title: "Inicio y accesos") {
            Toggle("Abrir Jarvis al iniciar sesión", isOn: Binding(
                get: { loginEnabled }, set: { value in
                    do {
                        if value { try SMAppService.mainApp.register() }
                        else { try SMAppService.mainApp.unregister() }
                        loginEnabled = SMAppService.mainApp.status == .enabled
                        if SMAppService.mainApp.status == .requiresApproval {
                            SMAppService.openSystemSettingsLoginItems()
                            store.message = "Aprueba Jarvis en los ítems de inicio de macOS."
                        }
                    } catch {
                        loginEnabled = SMAppService.mainApp.status == .enabled
                        store.fail("macOS no pudo cambiar el inicio automático. Usa la app instalada en Aplicaciones.")
                    }
                }
            ))
            .toggleStyle(JarvisSwitchStyle())
            Divider()
            LabeledContent("Atajo para hablar") {
                JarvisMenuPicker(title: "Atajo para hablar", selection: Binding(get: { shortcut }, set: { value in
                    if VoiceHotKeyController.shared.setShortcut(value) {
                        shortcut = value
                        model.voiceShortcutAvailable = true
                    } else {
                        store.fail("No se pudo registrar ese atajo. Se conservó el anterior.")
                    }
                }), options: JarvisVoiceShortcut.allCases.map { .init(value: $0, title: $0.title) })
            }
        }
        JarvisSettingsCard(title: "Personalidad",
                          detail: "El tono con que Jarvis te responde. Las indicaciones solo cambian cómo habla: no le dan permisos.") {
            if let personality = store.personality {
                LabeledContent("Estilo") {
                    JarvisMenuPicker(title: "Estilo", selection: $personalityPreset,
                                     options: personality.presets.map { .init(value: $0.id, title: $0.label) })
                }
                TextField("Indicaciones propias (opcional), p. ej. «llámame Guille y sé breve»",
                          text: $personalityCustom, axis: .vertical)
                    .lineLimit(2...4).focused($personalityFocused).jarvisInput(focused: personalityFocused)
                    .accessibilityLabel("Indicaciones de estilo para Jarvis")
                HStack {
                    Text("\(personalityCustom.count)/600").font(.system(size: 11))
                        .foregroundStyle(personalityCustom.count > 600 ? .orange : .secondary)
                    Spacer()
                    Button("Guardar") {
                        Task { await store.savePersonality(preset: personalityPreset, custom: personalityCustom) }
                    }
                    .disabled(personalityCustom.count > 600 || (personalityPreset == personality.preset
                                                               && personalityCustom == personality.custom))
                }
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .task {
            await store.loadPersonality()
            if let personality = store.personality {
                personalityPreset = personality.preset
                personalityCustom = personality.custom
            }
        }
        JarvisSettingsCard(title: "Avisos", detail: "Batería, red, agenda y rendimiento.") {
            Toggle("Alertas proactivas", isOn: Binding(
                get: { model.proactiveAlertsEnabled },
                set: { value in Task { await model.setProactiveAlertsEnabled(value) } }
            ))
            .toggleStyle(JarvisSwitchStyle())
        }
        JarvisSettingsCard(title: "Apariencia", detail: "Elige un tema o sigue el aspecto de macOS. Jarvis respeta las opciones de reducir transparencia y aumentar contraste.") {
            JarvisAppearancePicker()
        }
    }
}
