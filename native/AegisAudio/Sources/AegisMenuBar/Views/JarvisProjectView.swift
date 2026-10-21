import AegisAudioCore
import SwiftUI

struct JarvisProject: Decodable, Identifiable, Equatable {
    struct File: Decodable, Identifiable, Equatable {
        let file_id: String
        let name: String
        let size: Int
        var id: String { file_id }
    }
    let project_id: String
    let name: String
    let color: String
    let icon: String
    let instructions: String
    let files: [File]
    let chats: Int
    var id: String { project_id }

    static let colors = ["clay", "sage", "sky", "plum", "sand", "slate"]
    static let icons = ["folder", "briefcase", "book", "chart", "code", "heart", "house", "graduation", "plane", "star"]

    var tint: Color { Self.tint(color) }
    var symbol: String { Self.symbol(icon) }

    static func tint(_ name: String) -> Color {
        switch name {
        case "sage": JarvisTheme.adaptive(light: 0x5E7F5A, dark: 0x9CC68F)
        case "sky": JarvisTheme.adaptive(light: 0x3F6E9A, dark: 0x8DB6DC)
        case "plum": JarvisTheme.adaptive(light: 0x7B4E7E, dark: 0xC79BCB)
        case "sand": JarvisTheme.adaptive(light: 0x9A7B3F, dark: 0xD8BC80)
        case "slate": JarvisTheme.adaptive(light: 0x5E6670, dark: 0xA9B2BC)
        default: JarvisTheme.clay
        }
    }

    static func symbol(_ name: String) -> String {
        switch name {
        case "briefcase": "briefcase"
        case "book": "book.closed"
        case "chart": "chart.bar"
        case "code": "chevron.left.forwardslash.chevron.right"
        case "heart": "heart"
        case "house": "house"
        case "graduation": "graduationcap"
        case "plane": "airplane"
        case "star": "star"
        default: "folder"
        }
    }
}

private struct JarvisProjectListing: Decodable {
    let projects: [JarvisProject]
    let error: String?
}

extension JarvisChatStore {
    /// Projects come back with every change, so the sidebar stays current.
    @discardableResult
    func projectCall(_ method: String, _ payload: [String: Any] = [:], model: MenuBarModel) async -> String? {
        guard let data = await model.performChatIPC(method, payload: payload),
              let listing = try? JSONDecoder().decode(JarvisProjectListing.self, from: data) else {
            return "No se pudo actualizar el proyecto."
        }
        projects = listing.projects
        return listing.error
    }

    func project(_ id: String?) -> JarvisProject? { projects.first { $0.id == id } }
}

/// A project's page (Claude's and Codex's projects): instructions and files Jarvis
/// uses in every chat of the project, and those chats.
struct JarvisProjectView: View {
    let model: MenuBarModel
    let projectID: String
    @State private var chats: [ChatConversationSummary] = []
    @State private var instructions = ""
    @State private var editing = false
    @State private var importing = false
    @State private var confirmingDelete = false
    @State private var notice: String?
    private var chat: JarvisChatStore { model.chat }
    private var project: JarvisProject? { chat.project(projectID) }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                MenuBarIconButton(symbol: "sidebar.left", title: chat.showHistory ? "Ocultar historial" : "Mostrar historial") {
                    chat.showHistory.toggle()
                }
                Image(systemName: project?.symbol ?? "folder").foregroundStyle(project?.tint ?? JarvisTheme.clay)
                Text(project?.name ?? "Proyecto").font(.system(size: 14, weight: .semibold))
                Spacer()
                Button("Editar", systemImage: "pencil") { editing = true }
                    .buttonStyle(JarvisIconButtonStyle()).labelStyle(.iconOnly).help("Cambiar nombre, icono y color")
                Button(role: .destructive) { confirmingDelete = true } label: { Image(systemName: "trash") }
                    .buttonStyle(JarvisIconButtonStyle()).help("Borrar proyecto (sus chats se conservan)")
                Button("Nuevo chat", systemImage: "square.and.pencil") {
                    chat.newConversation()
                    chat.newChatProjectID = projectID
                }
                .buttonStyle(JarvisButtonStyle(prominent: true, compact: true))
                .disabled(!model.canManageChatHistory)
            }.padding(.horizontal, 14).frame(height: 52)
            JarvisSeparator()
            ScrollView {
                if let project { content(project) }
            }
        }
        .frame(minWidth: 480)
        .task(id: projectID) {
            instructions = project?.instructions ?? ""
            await loadChats()
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: ChatAttachmentLoader.allowedTypes,
                      allowsMultipleSelection: true) { result in
            guard case .success(let urls) = result else { return }
            Task { await addFiles(urls) }
        }
        .sheet(isPresented: $editing) {
            JarvisProjectEditor(project: project) { name, color, icon in
                await chat.projectCall("projects.save", ["project_id": projectID, "name": name, "color": color,
                                                         "icon": icon, "instructions": instructions], model: model)
            }
        }
        .confirmationDialog("¿Borrar «\(project?.name ?? "")»?", isPresented: $confirmingDelete, titleVisibility: .visible) {
            Button("Borrar proyecto", role: .destructive) {
                Task {
                    await chat.projectCall("projects.delete", ["project_id": projectID], model: model)
                    chat.pane = .chat
                }
            }
        } message: { Text("Se borran sus instrucciones y archivos. Sus chats siguen en Recientes.") }
    }

    private func content(_ project: JarvisProject) -> some View {
        VStack(alignment: .leading, spacing: 22) {
            if let notice { Text(notice).font(.system(size: 12)).foregroundStyle(JarvisTheme.warning) }
            card("Instrucciones", detail: "Jarvis las sigue en cada chat de este proyecto.") {
                TextEditor(text: $instructions).font(.system(size: 13)).scrollContentBackground(.hidden)
                    .frame(minHeight: 80, maxHeight: 180)
                    .padding(6).background(JarvisTheme.canvas, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusControl))
                    .accessibilityLabel("Instrucciones del proyecto")
                HStack {
                    Text("\(instructions.count)/4000").font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                    Spacer()
                    Button("Guardar") {
                        Task {
                            notice = await chat.projectCall("projects.save", [
                                "project_id": projectID, "name": project.name, "color": project.color,
                                "icon": project.icon, "instructions": instructions], model: model)
                        }
                    }
                    .buttonStyle(JarvisButtonStyle(compact: true))
                    .disabled(instructions == project.instructions || instructions.count > 4_000)
                }
            }
            card("Archivos", detail: "Conocimiento de referencia para los chats del proyecto: texto, código, CSV o PDF.") {
                ForEach(project.files) { file in
                    HStack {
                        Label(file.name, systemImage: "doc.text").font(.system(size: 13))
                        Spacer()
                        Text(ByteCountFormatter.string(fromByteCount: Int64(file.size), countStyle: .file))
                            .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkMuted)
                        Button { Task { notice = await chat.projectCall("projects.file.remove", [
                            "project_id": projectID, "file_id": file.file_id], model: model) } } label: {
                            Image(systemName: "xmark")
                        }.buttonStyle(JarvisIconButtonStyle()).help("Quitar archivo")
                    }
                }
                Button("Agregar archivos…", systemImage: "plus") { importing = true }
                    .buttonStyle(JarvisButtonStyle(compact: true)).disabled(project.files.count >= 8)
            }
            card("Chats", detail: nil) {
                if chats.isEmpty {
                    Text("Aún no hay chats. Empieza uno con «Nuevo chat» o mueve uno desde Recientes.")
                        .font(.system(size: 12.5)).foregroundStyle(JarvisTheme.inkMuted)
                }
                ForEach(chats) { entry in
                    Button { Task { await chat.open(entry, model: model) } } label: {
                        HStack {
                            Text(entry.title).font(.system(size: 13)).lineLimit(1)
                            Spacer()
                        }
                    }.buttonStyle(JarvisRowButtonStyle())
                }
            }
        }
        .frame(maxWidth: 700, alignment: .leading).frame(maxWidth: .infinity)
        .padding(.horizontal, 24).padding(.vertical, 24)
    }

    private func card<Content: View>(_ title: String, detail: String?, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.system(size: 13, weight: .semibold))
            if let detail { Text(detail).font(.system(size: 11.5)).foregroundStyle(JarvisTheme.inkSecondary) }
            content()
        }
        .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous).strokeBorder(JarvisTheme.hairline))
    }

    private func loadChats() async {
        guard let data = await model.performChatIPC("projects.conversations", payload: ["project_id": projectID]),
              let page = try? ChatConversationSummary.page(data) else { return }
        chats = page.0
    }

    private func addFiles(_ urls: [URL]) async {
        for url in urls {
            let accessed = url.startAccessingSecurityScopedResource()
            defer { if accessed { url.stopAccessingSecurityScopedResource() } }
            guard let document = (try? ChatAttachmentLoader.load(url))?.document else {
                notice = "No pude leer «\(url.lastPathComponent)» como texto."
                continue
            }
            notice = await chat.projectCall("projects.file.add", [
                "project_id": projectID, "name": document.name, "text": document.content], model: model)
        }
    }
}

struct JarvisProjectEditor: View {
    let project: JarvisProject?
    let save: @MainActor (String, String, String) async -> String?
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var color = "clay"
    @State private var icon = "folder"
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(project == nil ? "Nuevo proyecto" : "Editar proyecto").font(.system(size: 15, weight: .semibold))
            TextField("Nombre del proyecto", text: $name).jarvisInput(focused: false)
            HStack(spacing: 8) {
                ForEach(JarvisProject.colors, id: \.self) { value in
                    Button { color = value } label: {
                        Circle().fill(JarvisProject.tint(value)).frame(width: 20, height: 20)
                            .overlay(Circle().strokeBorder(JarvisTheme.ink, lineWidth: color == value ? 2 : 0).padding(-3))
                    }.buttonStyle(.plain).accessibilityLabel("Color \(value)")
                }
            }
            HStack(spacing: 4) {
                ForEach(JarvisProject.icons, id: \.self) { value in
                    Button { icon = value } label: {
                        Image(systemName: JarvisProject.symbol(value)).frame(width: 30, height: 28)
                            .foregroundStyle(icon == value ? JarvisProject.tint(color) : JarvisTheme.inkSecondary)
                            .background(icon == value ? JarvisTheme.fillSelected : .clear, in: RoundedRectangle(cornerRadius: 7))
                    }.buttonStyle(.plain).accessibilityLabel("Icono \(value)")
                }
            }
            if let error { Text(error).font(.system(size: 12)).foregroundStyle(JarvisTheme.failure) }
            HStack {
                Spacer()
                Button("Cancelar") { dismiss() }.buttonStyle(JarvisButtonStyle()).keyboardShortcut(.cancelAction)
                Button(project == nil ? "Crear" : "Guardar") {
                    Task {
                        error = await save(name, color, icon)
                        if error == nil { dismiss() }
                    }
                }
                .buttonStyle(JarvisButtonStyle(prominent: true)).keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(22).frame(width: 440).background(JarvisTheme.canvas)
        .onAppear {
            name = project?.name ?? ""
            color = project?.color ?? "clay"
            icon = project?.icon ?? "folder"
        }
    }
}
