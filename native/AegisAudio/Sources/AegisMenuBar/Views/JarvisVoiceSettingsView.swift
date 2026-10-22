import AegisAudioCore
import SwiftUI

struct JarvisVoiceSettingsView: View {
    let model: MenuBarModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        JarvisSettingsCard(title: String(localized: "Identidad local de voz", bundle: .module),
                          detail: String(localized: "Perfiles para personalización y contexto privado. No aprueban acciones.", bundle: .module)) {
            HStack {
                Label(model.speakerIdentityCapability == .ready ? String(localized: "Identificación local activa", bundle: .module) : String(localized: "Configuración pendiente", bundle: .module),
                      systemImage: "person.wave.2")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Button(String(localized: "Administrar perfiles", bundle: .module)) {
                    RequestedWindowFocus.open("speaker-enrollment") { openWindow(id: "speaker-enrollment") }
                }
            }
        }
        JarvisSettingsCard(title: String(localized: "Activación «Jarvis»", bundle: .module), detail: String(localized: "Reconocimiento local de la palabra de activación.", bundle: .module)) {
            Toggle(String(localized: "Activar escucha por palabra clave", bundle: .module), isOn: Binding(
                get: { model.wakeWordOptedIn },
                set: { value in Task { await model.setWakeWordListeningEnabled(value) } }
            ))
            .toggleStyle(JarvisSwitchStyle())
            .disabled(model.wakeWordCapability != .ready || model.wakeWordListeningState == .starting)
            HStack {
                Text(model.wakeWordCapability == .ready ? String(localized: "Modelo disponible en este Mac", bundle: .module) : String(localized: "Prepara las muestras y el modelo local", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Button(String(localized: "Preparar activación", bundle: .module)) {
                    RequestedWindowFocus.open("wake-word-enrollment") { openWindow(id: "wake-word-enrollment") }
                }
            }
        }
        JarvisSettingsCard(title: String(localized: "Voz de Jarvis", bundle: .module), detail: String(localized: "Se mantiene la voz actual de Jarvis.", bundle: .module)) {
            Label(String(localized: "Ray · español", bundle: .module), systemImage: "speaker.wave.2")
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
        }
    }
}
