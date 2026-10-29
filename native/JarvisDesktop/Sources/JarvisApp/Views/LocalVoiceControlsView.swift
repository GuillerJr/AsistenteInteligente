import DesktopCore
import SwiftUI

struct LocalVoiceControlsView: View {
    @Bindable var voice: LocalVoiceController
    let canUseVoice: Bool
    let start: () -> Void
    let cancel: () -> Void
    @State private var showingPreferences = false

    private var startLabel: String { voice.preferences.mode == .wakePhrase ? "Iniciar escucha local" : "Dictar" }

    var body: some View {
        HStack {
            if voice.canStopDictation {
                Button(voice.phase == .listening ? "Terminar dictado" : "Cancelar dictado", systemImage: "stop.circle") { voice.stopDictation() }
                Button("Descartar", role: .cancel, action: cancel)
            } else if voice.phase == .speaking {
                Button("Detener lectura", systemImage: "speaker.slash", action: cancel)
            } else {
                Button(startLabel, systemImage: "mic", action: start).disabled(!canUseVoice)
            }
            Button("Opciones de voz", systemImage: "slider.horizontal.3") { showingPreferences = true }
                .labelStyle(.iconOnly).help("Idioma, voz instalada y modo de captura local")
                .disabled(!canUseVoice || voice.phase != .idle)
                .popover(isPresented: $showingPreferences) { LocalVoicePreferencesView(voice: voice) }
        }
    }
}

private struct LocalVoicePreferencesView: View {
    @Bindable var voice: LocalVoiceController
    @State private var installedLocales: [String] = []
    @State private var voices: [LocalVoiceChoice] = []
    @State private var loading = true

    private var localeChoices: [String] { Array(Set(installedLocales + [voice.preferences.localeIdentifier])).sorted() }

    var body: some View {
        Form {
            Text("Voz en este Mac").font(.headline)
            Picker("Idioma", selection: $voice.preferences.localeIdentifier) {
                ForEach(localeChoices, id: \.self) { locale in
                    Text(Locale(identifier: "es").localizedString(forIdentifier: locale) ?? locale).tag(locale)
                }
            }
            Picker("Voz de lectura", selection: $voice.preferences.voiceID) {
                Text("Elegir una voz local compatible").tag(String?.none)
                ForEach(voices) { value in Text("\(value.name) · \(value.language)").tag(Optional(value.id)) }
            }
            Picker("Captura", selection: $voice.preferences.mode) {
                ForEach(LocalVoiceCaptureMode.allCases, id: \.self) { Text($0.label).tag($0) }
            }
            if voice.preferences.mode == .wakePhrase {
                TextField("Frase local", text: $voice.preferences.wakePhrase)
                Text("Sólo al pulsar «Iniciar escucha local» se abrirá el micrófono, hasta cinco minutos. El audio anterior a la frase se descarta; el texto posterior se añade al borrador para revisión. No envía mensajes.")
                    .font(.caption).foregroundStyle(.secondary)
                if !LocalWakePhrasePolicy.isValid(voice.preferences.wakePhrase) {
                    Text("Escribe una frase de una a cuatro palabras, hasta 64 bytes.").font(.caption).foregroundStyle(.red)
                }
            } else {
                Text("El reconocimiento es local y siempre añade texto al borrador, sin enviarlo. Cambiar estas opciones no activa el micrófono ni descarga modelos.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if loading { ProgressView("Consultando idiomas instalados…").controlSize(.small) }
            else if !installedLocales.contains(where: { LocalSpeechPolicy.canonicalLocale($0) == LocalSpeechPolicy.canonicalLocale(voice.preferences.localeIdentifier) }) {
                Text("El idioma elegido no figura entre los modelos locales disponibles. El dictado se bloqueará si no está instalado.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("La conversación por voz es un modo separado y requiere revisar el envío de texto a Codex con ChatGPT.")
                .font(.caption2).foregroundStyle(.secondary)
        }
        .formStyle(.grouped).frame(width: 420, height: voice.preferences.mode == .wakePhrase ? 470 : 390)
        .task {
            let available = await MacLocalVoiceDriver.installedRecognitionLocales()
            guard !Task.isCancelled else { return }
            installedLocales = available; voices = voice.availableVoices(localeIdentifier: voice.preferences.localeIdentifier)
            loading = false
        }
        .onChange(of: voice.preferences.localeIdentifier) { _, locale in
            voice.preferences.voiceID = nil
            voices = voice.availableVoices(localeIdentifier: locale)
        }
    }
}
