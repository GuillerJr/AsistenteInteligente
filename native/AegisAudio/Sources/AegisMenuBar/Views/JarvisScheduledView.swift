import SwiftUI

/// Scheduled tasks in the chat window (Codex's "Scheduled"): what Jarvis runs on its
/// own, when it runs next and how the last run went, with an editor to create or
/// change one without asking in the chat.
struct JarvisScheduledView: View {
    let model: MenuBarModel
    @State private var tasks: [JarvisScheduledTask]?
    @State private var editing: JarvisScheduleDraft?
    @State private var deleting: JarvisScheduledTask?
    private var chat: JarvisChatStore { model.chat }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                MenuBarIconButton(symbol: "sidebar.left", title: chat.showHistory ? String(localized: "Ocultar historial", bundle: .module) : String(localized: "Mostrar historial", bundle: .module)) {
                    chat.showHistory.toggle()
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(String(localized: "Tareas programadas", bundle: .module)).font(.system(size: 14, weight: .semibold))
                    Text(String(localized: "Jarvis las ejecuta solo y te avisa aquí y por Telegram", bundle: .module)).font(.system(size: 11))
                        .foregroundStyle(JarvisInterfaceStyle.secondaryText)
                }
                Spacer()
                Button(String(localized: "Nueva tarea", bundle: .module), systemImage: "plus") { editing = JarvisScheduleDraft() }
                    .buttonStyle(JarvisButtonStyle(prominent: true, compact: true))
            }.padding(.horizontal, 14).frame(height: 52)
            JarvisSeparator()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let tasks {
                        if tasks.isEmpty {
                            VStack(spacing: 10) {
                                Image(systemName: "clock.badge").font(.system(size: 26)).foregroundStyle(JarvisTheme.inkMuted)
                                Text(String(localized: "Sin tareas programadas", bundle: .module)).font(JarvisTheme.voice(20))
                                Text(String(localized: "Crea una aquí o pídesela a Jarvis: «cada mañana a las 8 resúmeme mis correos», «avísame cuando me escriba Ana».", bundle: .module))
                                    .font(.system(size: 12.5)).foregroundStyle(JarvisTheme.inkSecondary)
                                    .multilineTextAlignment(.center)
                            }.frame(maxWidth: .infinity).padding(.top, 60)
                        }
                        ForEach(tasks) { task in card(task) }
                    } else {
                        ProgressView().controlSize(.small).frame(maxWidth: .infinity).padding(.top, 60)
                    }
                }
                .frame(maxWidth: 700).frame(maxWidth: .infinity).padding(.horizontal, 24).padding(.vertical, 20)
            }
        }
        .frame(minWidth: 480)
        .task {
            while !Task.isCancelled {
                await load()
                do { try await Task.sleep(for: .seconds(20)) } catch { return }
            }
        }
        .sheet(item: $editing) { draft in
            JarvisScheduleEditor(draft: draft) { payload in
                guard await model.performChatIPC("schedules.save", payload: payload) != nil else { return false }
                await load()
                return true
            }
        }
        .confirmationDialog(String(localized: "¿Borrar «\(deleting?.title ?? "")»?", bundle: .module), isPresented: Binding(
            get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible) {
            Button(String(localized: "Borrar tarea", bundle: .module), role: .destructive) {
                guard let task = deleting else { return }
                Task { await act("schedules.delete", task) }
            }
        } message: { Text(String(localized: "Dejará de ejecutarse. Su chat con los resultados se conserva.", bundle: .module)) }
    }

    private func load() async {
        guard let data = await model.performChatIPC("schedules.list"),
              let decoded = try? JSONDecoder().decode(JarvisScheduledTasks.self, from: data) else { return }
        tasks = decoded.tasks.sorted { ($0.enabled ? 0 : 1, $0.next_run_at ?? "") < ($1.enabled ? 0 : 1, $1.next_run_at ?? "") }
    }

    private func act(_ method: String, _ task: JarvisScheduledTask, _ extra: [String: Any] = [:]) async {
        _ = await model.performChatIPC(method, payload: ["task_id": task.task_id].merging(extra) { _, new in new })
        await load()
    }

    private func status(_ task: JarvisScheduledTask) -> (String, Color)? {
        switch task.last_status {
        case nil: return nil
        case "completed": return (String(localized: "Última ejecución correcta", bundle: .module), JarvisTheme.success)
        case "needs_approval": return (String(localized: "Necesita tu aprobación para correr sola", bundle: .module), JarvisTheme.warning)
        default: return (String(localized: "La última ejecución falló", bundle: .module), JarvisTheme.failure)
        }
    }

    private func card(_ task: JarvisScheduledTask) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(task.title).font(.system(size: 14, weight: .semibold)).foregroundStyle(task.enabled ? JarvisTheme.ink : JarvisTheme.inkMuted)
                if task.notify == "when_new" { tag(String(localized: "Monitoreando", bundle: .module)) }
                if task.reminder == true { tag(String(localized: "Recordatorio", bundle: .module)) }
                Spacer()
                if !task.enabled { Text(String(localized: "En pausa", bundle: .module)).font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkMuted) }
                else if let next = task.next_run_at.flatMap(Date.init(jarvisISO:)) {
                    (Text(String(localized: "Próxima ", bundle: .module)) + Text(next, format: .relative(presentation: .named)))
                        .font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            Text(String(localized: "\(task.schedule.prefix(1).uppercased())\(task.schedule.dropFirst()) · \(task.prompt)", bundle: .module))
                .font(.system(size: 12.5)).foregroundStyle(JarvisTheme.inkSecondary).lineLimit(2)
            if let (label, color) = status(task) {
                Label(label, systemImage: "circle.fill").font(.system(size: 11.5)).foregroundStyle(color)
                    .labelStyle(StatusDotLabelStyle())
            }
            if let result = task.last_result, !result.isEmpty {
                Text(result).font(JarvisTheme.voice(12.5)).foregroundStyle(JarvisTheme.inkSecondary).lineLimit(3)
            }
            HStack(spacing: 6) {
                Button(String(localized: "Ejecutar ahora", bundle: .module), systemImage: "play") { Task { await act("schedules.run_now", task) } }
                Button(task.enabled ? String(localized: "Pausar", bundle: .module) : String(localized: "Reanudar", bundle: .module), systemImage: task.enabled ? "pause" : "arrow.clockwise") {
                    Task { await act("schedules.set_enabled", task, ["enabled": !task.enabled]) }
                }
                Button(String(localized: "Editar", bundle: .module), systemImage: "pencil") { editing = JarvisScheduleDraft(task) }
                if let raw = task.conversation_id, let id = UUID(uuidString: raw) {
                    Button(String(localized: "Ver resultados", bundle: .module), systemImage: "bubble.left") {
                        Task { await chat.openAttention(.init(id: raw, title: task.title, reason: "", action: "",
                                                              conversationID: id), model: model) }
                    }
                }
                Spacer()
                Button(role: .destructive) { deleting = task } label: { Image(systemName: "trash") }
                    .help(String(localized: "Borrar tarea", bundle: .module)).accessibilityLabel(String(localized: "Borrar \(task.title)", bundle: .module))
            }
            .buttonStyle(MenuBarSmallButtonStyle()).font(.system(size: 11.5))
            .disabled(!model.canManageChatHistory)
        }
        .padding(14)
        .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous).strokeBorder(JarvisTheme.hairline))
    }

    private func tag(_ text: String) -> some View {
        Text(text).font(.system(size: 10.5, weight: .medium)).foregroundStyle(JarvisTheme.clay)
            .padding(.horizontal, 6).padding(.vertical, 2).background(JarvisTheme.clay.opacity(0.12), in: Capsule())
    }
}

private struct StatusDotLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 6) { configuration.icon.font(.system(size: 6)); configuration.title }
    }
}

/// The editor's fields, from a task or empty for a new one.
struct JarvisScheduleDraft: Identifiable {
    static let weekdays = ["lunes", "martes", String(localized: "miércoles", bundle: .module), "jueves", "viernes", String(localized: "sábado", bundle: .module), "domingo"]
    let id = UUID()
    var taskID: String?
    var title = ""
    var prompt = ""
    var kind = "daily"
    var time = Calendar.current.date(bySettingHour: 8, minute: 0, second: 0, of: .now) ?? .now
    var days: Set<String> = Set(weekdays)
    var everyMinutes = 60
    var at = Date.now.addingTimeInterval(3_600)
    var onlyWhenNew = false
    var reminder = false

    init() {}

    init(_ task: JarvisScheduledTask) {
        taskID = task.task_id
        title = task.title
        prompt = task.prompt
        onlyWhenNew = task.notify == "when_new"
        reminder = task.reminder == true
        guard let spec = task.spec else { return }
        kind = spec.kind
        days = Set(spec.weekdays)
        everyMinutes = spec.every_minutes ?? 60
        if let value = spec.time, let parsed = Self.clock.date(from: value),
           let merged = Calendar.current.date(bySettingHour: Calendar.current.component(.hour, from: parsed),
                                              minute: Calendar.current.component(.minute, from: parsed),
                                              second: 0, of: .now) {
            time = merged
        }
        if let value = spec.at, let parsed = Self.local.date(from: value) { at = parsed }
    }

    static let clock: DateFormatter = { let f = DateFormatter(); f.dateFormat = "HH:mm"; return f }()
    static let local: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd'T'HH:mm"; return f }()

    var payload: [String: Any] {
        var value: [String: Any] = [
            "title": title.trimmingCharacters(in: .whitespacesAndNewlines),
            "prompt": prompt.trimmingCharacters(in: .whitespacesAndNewlines),
            "kind": kind, "notify": onlyWhenNew ? "when_new" : "always", "reminder": reminder,
        ]
        if let taskID { value["task_id"] = taskID }
        switch kind {
        case "interval": value["every_minutes"] = everyMinutes
        case "once": value["at"] = Self.local.string(from: at)
        default:
            value["time"] = Self.clock.string(from: time)
            if days.count < 7 { value["weekdays"] = Self.weekdays.filter(days.contains) }
        }
        return value
    }

    var valid: Bool {
        !title.trimmingCharacters(in: .whitespaces).isEmpty && !prompt.trimmingCharacters(in: .whitespaces).isEmpty
            && (kind != "daily" || !days.isEmpty)
            && (kind != "interval" || (15...10_080).contains(everyMinutes))
    }
}

struct JarvisScheduleEditor: View {
    @State var draft: JarvisScheduleDraft
    let save: @MainActor ([String: Any]) async -> Bool
    @Environment(\.dismiss) private var dismiss
    @State private var saving = false
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(draft.taskID == nil ? String(localized: "Nueva tarea programada", bundle: .module) : String(localized: "Editar tarea", bundle: .module)).font(.system(size: 15, weight: .semibold))
            TextField(String(localized: "Nombre, p. ej. «Resumen de correos»", bundle: .module), text: $draft.title).jarvisInput(focused: false)
            TextField(String(localized: "Qué hará Jarvis en cada ejecución", bundle: .module), text: $draft.prompt, axis: .vertical)
                .lineLimit(3...6).jarvisInput(focused: false)
            JarvisSegmentedPicker(title: String(localized: "Frecuencia", bundle: .module), selection: $draft.kind, options: [
                .init(value: "daily", title: String(localized: "A una hora", bundle: .module)), .init(value: "interval", title: String(localized: "Cada cierto tiempo", bundle: .module)),
                .init(value: "once", title: String(localized: "Una vez", bundle: .module)),
            ])
            switch draft.kind {
            case "interval":
                Stepper(String(localized: "Cada \(draft.everyMinutes) minutos", bundle: .module), value: $draft.everyMinutes, in: 15...10_080)
            case "once":
                DatePicker(String(localized: "Fecha y hora", bundle: .module), selection: $draft.at, in: Date.now...)
            default:
                DatePicker(String(localized: "Hora", bundle: .module), selection: $draft.time, displayedComponents: .hourAndMinute)
                HStack(spacing: 4) {
                    ForEach(JarvisScheduleDraft.weekdays, id: \.self) { day in
                        let on = draft.days.contains(day)
                        Button(String(day.prefix(2)).capitalized) {
                            if on { draft.days.remove(day) } else { draft.days.insert(day) }
                        }
                        .buttonStyle(.plain).font(.system(size: 12, weight: .medium))
                        .frame(width: 34, height: 28)
                        .foregroundStyle(on ? JarvisTheme.canvas : JarvisTheme.inkSecondary)
                        .background(on ? JarvisTheme.ink : JarvisTheme.fill, in: RoundedRectangle(cornerRadius: 7))
                        .accessibilityLabel(day).accessibilityValue(on ? String(localized: "Incluido", bundle: .module) : String(localized: "No incluido", bundle: .module))
                    }
                }
            }
            switchRow(String(localized: "Avisarme solo si hay algo nuevo", bundle: .module), $draft.onlyWhenNew)
            switchRow(String(localized: "Es solo un recordatorio (no ejecuta nada)", bundle: .module), $draft.reminder)
            if failed {
                Text(String(localized: "No se pudo guardar. Revisa la hora y que Jarvis esté conectado.", bundle: .module))
                    .font(.system(size: 12)).foregroundStyle(JarvisTheme.failure)
            }
            HStack {
                Spacer()
                Button(String(localized: "Cancelar", bundle: .module)) { dismiss() }.buttonStyle(JarvisButtonStyle()).keyboardShortcut(.cancelAction)
                Button(String(localized: "Guardar y ejecutar ahora", bundle: .module)) { submit(runNow: true) }
                    .buttonStyle(JarvisButtonStyle()).disabled(!draft.valid || saving || draft.reminder)
                Button(String(localized: "Guardar", bundle: .module)) { submit(runNow: false) }
                    .buttonStyle(JarvisButtonStyle(prominent: true)).keyboardShortcut(.defaultAction)
                    .disabled(!draft.valid || saving)
            }
        }
        .toggleStyle(JarvisToggleControlStyle())
        .padding(22).frame(width: 520)
        .background(JarvisTheme.canvas)
    }

    // The settings switch hides its label, so the text sits beside it.
    private func switchRow(_ title: String, _ isOn: Binding<Bool>) -> some View {
        HStack {
            Text(title).font(.system(size: 13))
            Spacer()
            Toggle(title, isOn: isOn)
        }
    }

    private func submit(runNow: Bool) {
        saving = true
        var payload = draft.payload
        payload["run_now"] = runNow
        Task {
            if await save(payload) { dismiss() } else { failed = true }
            saving = false
        }
    }
}
