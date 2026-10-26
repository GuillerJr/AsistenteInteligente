import SwiftUI

/// Shared actual presentation for all four Settings owner workflows. It remains
/// present after expiry even when no valid ticket/approval button remains.
public struct OwnerDecisionStatusView: View {
    public let notice: OwnerDecisionNotice
    public init(notice: OwnerDecisionNotice) { self.notice = notice }
    public var body: some View {
        TimelineView(.periodic(from: Date(timeIntervalSince1970: 0), by: 1)) { context in
            VStack(alignment: .leading, spacing: 6) {
                Text(notice.title(at: context.date)).font(.headline)
                    .accessibilityIdentifier("owner-decision-state-title")
                Text(notice.message(at: context.date)).font(.callout)
                    .accessibilityIdentifier("owner-decision-state-message")
                if let expires = notice.expiresAt, notice.awaitingDecision, expires > context.date {
                    Text("Caduca en \(max(0, Int(expires.timeIntervalSince(context.date)))) segundos.").font(.caption).foregroundStyle(.secondary)
                }
                if let code = notice.errorCode { Text("Diagnóstico: \(code)").font(.caption.monospaced()).textSelection(.enabled) }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

public struct OwnerDecisionBanner: View {
    public let notice: OwnerDecisionNotice
    public let navigation: SettingsOwnerNavigation
    private let onReveal: (() -> Void)?
    public init(notice: OwnerDecisionNotice, navigation: SettingsOwnerNavigation, onReveal: (() -> Void)? = nil) {
        self.notice = notice; self.navigation = navigation; self.onReveal = onReveal
    }
    public var body: some View {
        TimelineView(.periodic(from: Date(timeIntervalSince1970: 0), by: 1)) { context in
            HStack(spacing: 12) {
                Image(systemName: notice.expired(at: context.date) ? "clock.badge.exclamationmark" : "hand.raised")
                VStack(alignment: .leading, spacing: 3) {
                    Text(notice.title(at: context.date)).font(.headline)
                    Text(notice.resourcesPending ? "La operación sigue bajo supervisión; este aviso no la cancela." : "El resultado anterior se conserva; no se repetirá automáticamente.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(onReveal == nil ? (notice.awaitingDecision ? "Ver decisión" : "Ver resultado") : (notice.awaitingDecision && !notice.expired(at: context.date) ? "Ver decisión pendiente" : "Ver resultado de mantenimiento")) {
                    if let onReveal { onReveal() }
                    else { navigation.reveal(notice) }
                }
                    .accessibilityIdentifier("owner-decision-banner-open")
                if !notice.resourcesPending && !notice.awaitingDecision && !notice.unconfirmed {
                    Button("Ocultar aviso") { navigation.dismiss(notice) }
                }
            }.padding(12).background(.orange.opacity(0.10))
                .accessibilityIdentifier("owner-decision-banner")
        }
    }
}

/// One explicit reveal scrolls once after the destination tab has mounted.
/// Polling never drives scrolling and this modifier has no permission API.
public struct OwnerDecisionScrollModifier: ViewModifier {
    let navigation: SettingsOwnerNavigation
    let tab: String
    public func body(content: Content) -> some View {
        ScrollViewReader { proxy in
            content
                .task(id: navigation.revealSequence) {
                    guard navigation.selectedTab == tab, let id = navigation.focusID else { return }
                    await Task.yield()
                    guard !Task.isCancelled, navigation.selectedTab == tab else { return }
                    proxy.scrollTo(id, anchor: .top)
                }
        }
    }
}
public extension View {
    func ownerDecisionScrolling(_ navigation: SettingsOwnerNavigation, tab: String) -> some View {
        modifier(OwnerDecisionScrollModifier(navigation: navigation, tab: tab))
    }
}
