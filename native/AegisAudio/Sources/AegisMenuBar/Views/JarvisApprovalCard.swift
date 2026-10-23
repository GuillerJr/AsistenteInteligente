import AegisAudioCore
import SwiftUI

/// The approval as Codex and Claude ask for it: a card where the request was made,
/// with the buttons in it. In the chat it sits under the conversation; for a voice or
/// Dot request it floats under the notch. Nothing modal, no Touch ID sheet.
struct JarvisApprovalCard: View {
    enum Style { case chat, notch }

    let model: MenuBarModel
    let approval: PendingApproval
    var style: Style = .chat
    @State private var expanded = false

    /// "com.apple.Notes" → "Notas", as Finder names the installed app.
    static func appName(_ bundle: String) -> String {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundle) else { return bundle }
        return FileManager.default.displayName(atPath: url.path).replacingOccurrences(of: ".app", with: "")
    }

    private var presentation: ActionReviewPresentation {
        ActionReviewPresentation(toolName: approval.confirmation.toolName)
    }

    private var actionTitle: String {
        ActionApprovalLabel.title(toolName: approval.confirmation.toolName,
                                  action: approval.confirmation.preview?.fields.first(where: { $0.label == "Acción" })?.value)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: presentation.icon).foregroundStyle(JarvisTheme.clay)
                Text(presentation.title).font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(JarvisTheme.ink)
                Spacer(minLength: 8)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let left = Int(approval.confirmation.expiresAt.timeIntervalSince(context.date))
                    Text(left > 0 ? String(localized: "\(left) s", bundle: .module)
                                  : String(localized: "Caducó", bundle: .module))
                        .font(.system(size: 11)).monospacedDigit().foregroundStyle(JarvisTheme.inkMuted)
                }
            }
            Text(approval.confirmation.summary)
                .font(.system(size: 13)).foregroundStyle(JarvisTheme.ink)
                .lineLimit(style == .notch ? 4 : nil)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if let preview = approval.confirmation.preview, !preview.fields.isEmpty {
                DisclosureGroup(isExpanded: $expanded) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(Array(preview.fields.enumerated()), id: \.offset) { _, field in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(field.label).font(.system(size: 11, weight: .medium))
                                    .foregroundStyle(JarvisTheme.inkMuted)
                                Text(field.value).font(.system(size: 12)).foregroundStyle(JarvisTheme.ink)
                                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        Text(presentation.warning).font(.system(size: 11))
                            .foregroundStyle(JarvisTheme.inkSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(.top, 6)
                } label: {
                    Text(String(localized: "Detalles", bundle: .module)).font(.system(size: 12))
                        .foregroundStyle(JarvisTheme.inkSecondary)
                }
            }
            if model.voiceApprovalListens(for: approval) {
                Label(String(localized: "También puedes decir «Aprobado».", bundle: .module), systemImage: "mic")
                    .font(.system(size: 11)).foregroundStyle(JarvisTheme.inkSecondary)
            }
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let expired = context.date >= approval.confirmation.expiresAt
                HStack(spacing: 8) {
                    Button(String(localized: "Rechazar", bundle: .module), role: .cancel) {
                        Task { await model.denyPending(expected: approval) }
                    }
                    Spacer(minLength: 8)
                    if let bundle = approval.confirmation.appGrantBundle {
                        Menu(String(localized: "Permitir \(Self.appName(bundle))", bundle: .module)) {
                            Button(String(localized: "En esta conversación", bundle: .module)) {
                                Task { await model.approvePending(expected: approval, appScope: "conversation") }
                            }
                            Button(String(localized: "Siempre", bundle: .module)) {
                                Task { await model.approvePending(expected: approval, appScope: "always") }
                            }
                        }
                        .fixedSize()
                        .disabled(expired)
                    }
                    if approval.confirmation.standingGrantAvailable {
                        // Only read-only tools: writes and sends keep asking every time.
                        Button(String(localized: "Aprobar siempre", bundle: .module)) {
                            Task { await model.approvePending(expected: approval, always: true) }
                        }
                        .help(String(localized: "Solo lee datos. Puedes revocarlo en Ajustes › Permisos.", bundle: .module))
                        .disabled(expired)
                    }
                    Button(actionTitle) { Task { await model.approvePending(expected: approval) } }
                        .buttonStyle(HUDActionStyle(accent: HUDStyle.accent(for: .active), prominent: true))
                        .disabled(expired)
                }
                .controlSize(.small)
                .disabled(model.approvalActionInProgress || model.interactionSubmissionInProgress)
            }
        }
        .jarvisDecisionSurface(style)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(String(localized: "Aprobación pendiente", bundle: .module))
    }
}
