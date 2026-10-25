import DesktopCore
import SwiftUI

struct SchedulesView: View {
    @Bindable var store: ChatStore
    @State private var editing: ScheduleDescriptor?
    @State private var showEditor = false
    @State private var showingHistory: ScheduleDescriptor?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Programador").font(.title2.weight(.semibold))
                Text("Las tareas se crean deshabilitadas. Habilitarlas y activar el programador son decisiones separadas; ninguna aprueba herramientas.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("Nueva tarea…") { editing = nil; showEditor = true }.disabled(!store.canChangeSession)
                    Button("Actualizar") { Task { await store.management.refreshFromUI() } }
                }
                if let snapshot = store.management.scheduler {
                    Label(snapshot.running ? "Programador activo" : "Programador detenido", systemImage: snapshot.running ? "clock.badge.checkmark" : "pause.circle")
                    if snapshot.running {
                        Button("Detener programador…") { Task { await store.management.stopScheduler() } }
                            .disabled(store.management.isChanging)
                    } else {
                        Button("Activar programador…") { Task { await store.management.startScheduler() } }
                            .disabled(!store.canChangeSession)
                    }
                    ForEach(snapshot.schedules) { schedule in
                        VStack(alignment: .leading, spacing: 10) {
                            Text(schedule.title).font(.headline)
                            Text("\(schedule.enabled ? "Habilitada" : "Pausada") · \(schedule.ruleDescription)")
                                .font(.caption).foregroundStyle(.secondary)
                            if let next = schedule.nextAt {
                                Text("Próxima: \(next.formatted(date: .abbreviated, time: .standard))")
                                    .font(.caption)
                            } else { Text("Sin otra ocurrencia pendiente.").font(.caption).foregroundStyle(.secondary) }
                            Text("Agente: \(store.management.agents.first(where: { $0.id == schedule.agentID })?.name ?? schedule.agentID)")
                                .font(.caption)
                            DisclosureGroup("Prompt y carpeta vinculada") {
                                Text(schedule.prompt).textSelection(.enabled)
                                Text("Identidad de carpeta: \(schedule.workspaceID)")
                                    .font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
                                if schedule.workspaceID != snapshot.workspaceID {
                                    Text("La carpeta actual es diferente. Esta tarea no se ejecutará sobre ella automáticamente.")
                                        .font(.caption).foregroundStyle(.orange)
                                }
                            }
                            HStack {
                                Button(schedule.enabled ? "Pausar…" : "Habilitar…") { Task { await store.management.enableSchedule(schedule) } }
                                Button("Editar…") { editing = schedule; showEditor = true }
                                Button("Quitar…", role: .destructive) { Task { await store.management.removeSchedule(schedule) } }
                            }.disabled(!store.canChangeSession)
                            Button("Ver ejecuciones") { showingHistory = schedule; Task { await store.management.loadHistory(schedule) } }
                        }.padding(14).background(.quaternary, in: .rect(cornerRadius: 10))
                    }
                    if snapshot.schedules.isEmpty { Text("No hay tareas configuradas.").foregroundStyle(.secondary) }
                    if snapshot.errorCode != nil { Text("El programador se detuvo por un error. Revisa sus ejecuciones antes de activarlo otra vez.").foregroundStyle(.red) }
                }
                Text("Los intervalos y días perdidos se agrupan en una ocurrencia. Una hora inexistente por horario de verano se omite; una hora duplicada usa su primera aparición. Al cerrar Jarvis se detiene el programador; no se activa al reiniciar.")
                    .font(.caption).foregroundStyle(.secondary)
                if let error = store.management.errorMessage { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            }.padding(20)
        }
        .task { await store.management.refreshFromUI() }
        .sheet(isPresented: $showEditor) { ScheduleEditorView(schedule: editing, store: store) }
        .sheet(item: $showingHistory) { value in ScheduleHistoryView(schedule: value, store: store) }
    }
}

private struct ScheduleEditorView: View {
    let schedule: ScheduleDescriptor?
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    @State private var title: String
    @State private var prompt: String
    @State private var agentID: String
    @State private var kind: String
    @State private var date: Date
    @State private var interval: String
    @State private var dailyTime: String
    @State private var timezone: String

    init(schedule: ScheduleDescriptor?, store: ChatStore) {
        self.schedule = schedule; self.store = store
        _title = State(initialValue: schedule?.title ?? "")
        _prompt = State(initialValue: schedule?.prompt ?? "")
        _agentID = State(initialValue: schedule?.agentID ?? store.newAgentID)
        _kind = State(initialValue: schedule?.spec["kind"].string ?? "once")
        _date = State(initialValue: schedule.flatMap { ManagementValidation.timestamp($0.spec["at"]) }.map(Date.init(timeIntervalSince1970:)) ?? Date().addingTimeInterval(300))
        _interval = State(initialValue: String(schedule?.spec["interval_seconds"].integer ?? 3600))
        _dailyTime = State(initialValue: schedule?.spec["local_time"].string ?? "09:00")
        _timezone = State(initialValue: schedule?.spec["timezone"].string ?? TimeZone.current.identifier)
    }
    private var spec: RPCValue? {
        var fields: [String: RPCValue] = ["kind": .string(kind), "timezone": .string(timezone)]
        guard TimeZone(identifier: timezone) != nil else { return nil }
        if kind == "once" { fields["at"] = .number(date.timeIntervalSince1970) }
        else if kind == "interval" {
            guard let seconds = Int(interval), (1...31_536_000).contains(seconds) else { return nil }
            fields["interval_seconds"] = .number(Double(seconds))
        } else {
            guard dailyTime.range(of: "^([01][0-9]|2[0-3]):[0-5][0-9]$", options: .regularExpression) != nil else { return nil }
            fields["local_time"] = .string(dailyTime)
        }
        return .object(fields)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(schedule == nil ? "Nueva tarea deshabilitada" : "Editar tarea").font(.title2.weight(.semibold))
            TextField("Título", text: $title)
            Picker("Agente", selection: $agentID) { ForEach(store.management.agents) { Text($0.name).tag($0.id) } }
                .disabled(schedule != nil)
            TextEditor(text: $prompt).frame(height: 160).border(.quaternary).accessibilityLabel("Prompt completo de la tarea")
            Picker("Frecuencia", selection: $kind) {
                Text("Una vez").tag("once"); Text("Intervalo").tag("interval"); Text("Diaria").tag("daily")
            }
            if kind == "once" {
                DatePicker("Fecha y hora UTC", selection: $date, displayedComponents: [.date, .hourAndMinute])
                    .environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
            } else if kind == "interval" { TextField("Intervalo en segundos", text: $interval) }
            else { TextField("Hora local (HH:MM)", text: $dailyTime) }
            TextField("Zona horaria IANA", text: $timezone)
            Text((schedule == nil ? "Se vincula a la carpeta actual. " : "Conserva la carpeta vinculada al crearla. ") + "Cambiar de carpeta no cambia ese vínculo. Los prompts usan Codex; cada herramienta mantiene su aprobación.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = store.management.errorMessage { Text(error).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Revisar y guardar…") {
                    guard let spec else { return }
                    Task { if await store.management.saveSchedule(schedule, title: title, prompt: prompt, agentID: agentID, spec: spec) { dismiss() } }
                }.disabled(!store.canChangeSession || spec == nil || !ManagementValidation.text(title, limit: 512) || !ManagementValidation.text(prompt, limit: 8000))
            }
        }.padding(24).frame(width: 640)
    }
}

private struct ScheduleHistoryView: View {
    let schedule: ScheduleDescriptor
    @Bindable var store: ChatStore
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Ejecuciones: \(schedule.title)").font(.title2.weight(.semibold))
                Spacer()
                Button("Actualizar") { Task { await store.management.loadHistory(schedule) } }
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(store.management.occurrences) { occurrence in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(occurrence.scheduledAt.formatted(date: .abbreviated, time: .standard)).font(.headline)
                            Text("Estado: \(ManagementPresentation.state(occurrence.status)) · resultado: \(occurrence.outcome == "unknown" ? "sin confirmar" : occurrence.outcome == "pending" ? "pendiente" : "registrado")")
                            if let run = occurrence.runID { Text("Run: \(run)").font(.system(.caption, design: .monospaced)).textSelection(.enabled) }
                            if let session = occurrence.sessionID {
                                Button("Abrir conversación") { dismiss(); Task { await store.openScheduledSession(session) } }
                                    .disabled(!store.canChangeSession)
                            }
                            if occurrence.errorCode != nil { Text("La ejecución conserva un error; no se repite automáticamente.").foregroundStyle(.secondary) }
                        }.padding(12).background(.quaternary, in: .rect(cornerRadius: 8))
                    }
                    if store.management.occurrences.isEmpty { Text("Sin ejecuciones registradas.") }
                }
            }.frame(height: 320)
            Button("Cerrar") { dismiss() }.keyboardShortcut(.cancelAction)
        }.padding(24).frame(width: 660)
        .task { await store.management.loadHistory(schedule) }
    }
}
