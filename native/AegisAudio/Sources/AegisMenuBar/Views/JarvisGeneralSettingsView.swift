import AegisAudioCore
import SwiftUI

struct JarvisGeneralSettingsView: View {
    let model: MenuBarModel
    let store: JarvisSettingsStore
    var interactive = true
    @Environment(\.openWindow) private var openWindow
    @State private var loginEnabled = JarvisLoginItemController.shared.status == .enabled
    @State private var shortcut = JarvisVoiceShortcut.current
    @State private var personalityPreset = "default"
    @State private var personalityCustom = ""
    @FocusState private var personalityFocused: Bool

    var body: some View {
        JarvisSettingsCard(title: String(localized: "Inicio y accesos", bundle: .module)) {
            Toggle(String(localized: "Abrir Jarvis al iniciar sesión", bundle: .module), isOn: Binding(
                get: { loginEnabled }, set: { value in
                    do {
                        let status = try JarvisLoginItemController.shared.setEnabled(value)
                        loginEnabled = status == .enabled
                        if status == .requiresApproval {
                            JarvisLoginItemController.shared.openApprovalSettings()
                            store.message = String(localized: "Aprueba Jarvis en los ítems de inicio de macOS.", bundle: .module)
                        }
                    } catch {
                        loginEnabled = JarvisLoginItemController.shared.status == .enabled
                        store.fail(String(localized: "macOS no pudo cambiar el inicio automático. Usa la app instalada en Aplicaciones.", bundle: .module))
                    }
                }
            ))
            .toggleStyle(JarvisSwitchStyle())
            if JarvisLoginItemController.shared.status == .requiresApproval {
                Button(String(localized: "Aprobar en los ajustes de macOS", bundle: .module)) {
                    JarvisLoginItemController.shared.openApprovalSettings()
                }
            }
            Button(String(localized: "Abrir bienvenida", bundle: .module)) {
                RequestedWindowFocus.open("welcome") { openWindow(id: "welcome") }
            }
            Divider()
            LabeledContent(String(localized: "Atajo para hablar", bundle: .module)) {
                JarvisMenuPicker(title: String(localized: "Atajo para hablar", bundle: .module), selection: Binding(get: { shortcut }, set: { value in
                    if VoiceHotKeyController.shared.setShortcut(value) {
                        shortcut = value
                        model.voiceShortcutAvailable = true
                    } else {
                        store.fail(String(localized: "No se pudo registrar ese atajo. Se conservó el anterior.", bundle: .module))
                    }
                }), options: JarvisVoiceShortcut.allCases.map { .init(value: $0, title: $0.title) })
            }
            Text(String(localized: "Toca el atajo para hablar, o mantenlo pulsado mientras hablas y suéltalo al terminar. ⌃⇧J abre una pregunta rápida por escrito desde cualquier app.", bundle: .module))
                .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        JarvisSettingsCard(title: String(localized: "Personalidad", bundle: .module),
                          detail: String(localized: "El tono con que Jarvis te responde. Las indicaciones solo cambian cómo habla: no le dan permisos.", bundle: .module)) {
            if let personality = store.personality {
                LabeledContent(String(localized: "Estilo", bundle: .module)) {
                    JarvisMenuPicker(title: String(localized: "Estilo", bundle: .module), selection: $personalityPreset,
                                     options: personality.presets.map { .init(value: $0.id, title: $0.label) })
                }
                TextField(String(localized: "Indicaciones propias (opcional), p. ej. «llámame Guille y sé breve»", bundle: .module),
                          text: $personalityCustom, axis: .vertical)
                    .lineLimit(2...4).focused($personalityFocused).jarvisInput(focused: personalityFocused)
                    .accessibilityLabel(String(localized: "Indicaciones de estilo para Jarvis", bundle: .module))
                HStack {
                    Text(String(localized: "\(personalityCustom.count)/600", bundle: .module)).font(.system(size: 11))
                        .foregroundStyle(personalityCustom.count > 600 ? JarvisTheme.warning : JarvisTheme.inkSecondary)
                    Spacer()
                    Button(String(localized: "Guardar", bundle: .module)) {
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
            guard interactive else { return }
            await store.loadPersonality()
            if let personality = store.personality {
                personalityPreset = personality.preset
                personalityCustom = personality.custom
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            loginEnabled = JarvisLoginItemController.shared.status == .enabled
        }
        JarvisSettingsCard(title: String(localized: "Avisos", bundle: .module), detail: String(localized: "Batería, red, agenda y rendimiento.", bundle: .module)) {
            Toggle(String(localized: "Alertas proactivas", bundle: .module), isOn: Binding(
                get: { model.proactiveAlertsEnabled },
                set: { value in Task { await model.setProactiveAlertsEnabled(value) } }
            ))
            .toggleStyle(JarvisSwitchStyle())
        }
        JarvisAboutSettingsView()
        JarvisSettingsCard(title: String(localized: "Apariencia", bundle: .module), detail: String(localized: "Elige un tema o sigue el aspecto de macOS. Jarvis respeta las opciones de reducir transparencia y aumentar contraste.", bundle: .module)) {
            JarvisAppearancePicker()
        }
    }
}
