import AegisAudioCore
import SwiftUI

struct JarvisVoiceSettingsView: View {
    let model: MenuBarModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        JarvisSettingsCard(title: "Identidad local de voz",
                          detail: "Perfiles para personalización y contexto privado. No aprueban acciones.") {
            HStack {
                Label(model.speakerIdentityCapability == .ready ? "Identificación local activa" : "Configuración pendiente",
                      systemImage: "person.wave.2")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Button("Administrar perfiles") {
                    RequestedWindowFocus.open("speaker-enrollment") { openWindow(id: "speaker-enrollment") }
                }
            }
        }
        JarvisSettingsCard(title: "Activación «Jarvis»", detail: "Reconocimiento local de la palabra de activación.") {
            Toggle("Activar escucha por palabra clave", isOn: Binding(
                get: { model.wakeWordOptedIn },
                set: { value in Task { await model.setWakeWordListeningEnabled(value) } }
            ))
            .toggleStyle(JarvisSwitchStyle())
            .disabled(model.wakeWordCapability != .ready || model.wakeWordListeningState == .starting)
            HStack {
                Text(model.wakeWordCapability == .ready ? "Modelo disponible en este Mac" : "Prepara las muestras y el modelo local")
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                Spacer()
                Button("Preparar activación") {
                    RequestedWindowFocus.open("wake-word-enrollment") { openWindow(id: "wake-word-enrollment") }
                }
            }
        }
        JarvisSettingsCard(title: "Voz de Jarvis", detail: "Se mantiene la voz actual de Jarvis.") {
            Label("Ray · español", systemImage: "speaker.wave.2")
                .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
        }
    }
}
