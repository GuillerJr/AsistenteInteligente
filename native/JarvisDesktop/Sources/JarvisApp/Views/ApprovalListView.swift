import DesktopCore
import SwiftUI

struct ApprovalListView: View {
    @Bindable var access: AccessStore

    var body: some View {
        if !access.approvals.isEmpty {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(access.approvals) { operation in
                        ApprovalCard(operation: operation, access: access)
                    }
                }.padding(16)
            }.frame(maxHeight: 350)
        }
    }
}

private struct ApprovalCard: View {
    let operation: ApprovalOperation
    @Bindable var access: AccessStore

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { timeline in
            let expired = operation.isExpired(at: timeline.date)
            VStack(alignment: .leading, spacing: 10) {
                Label("Tu aprobación es necesaria", systemImage: "hand.raised")
                    .font(.headline)
                Text(operation.toolName).font(.callout.weight(.semibold))
                Text("Autoriza únicamente esta operación con estos argumentos:")
                    .font(.caption).foregroundStyle(.secondary)
                ScrollView([.horizontal, .vertical]) {
                    Text(operation.argumentsJSON)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }.frame(maxHeight: 130)
                HStack {
                    Text(expired ? "Solicitud caducada" : "Válida hasta \(operation.expiresAt.formatted(date: .omitted, time: .standard))")
                        .font(.caption2).foregroundStyle(.secondary)
                    Spacer()
                    Button("Rechazar") { Task { await access.decide(operation, allow: false) } }
                        .disabled(access.decidingIDs.contains(operation.id))
                    Button("Permitir esta operación") { Task { await access.decide(operation, allow: true) } }
                        .buttonStyle(.borderedProminent)
                        .disabled(expired || access.decidingIDs.contains(operation.id))
                    Menu("Recordar permiso…") {
                        Button("En esta conversación") { Task { await access.decide(operation, allow: true, remember: "session") } }
                        if !operation.scope.temporary {
                            Button("Siempre para este destino") { Task { await access.decide(operation, allow: true, remember: "always") } }
                        }
                    }.disabled(expired || access.decidingIDs.contains(operation.id))
                }
            }
            .padding(14).background(.quaternary, in: .rect(cornerRadius: 12))
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("jarvis.approval.\(operation.id)")
        }
    }
}
