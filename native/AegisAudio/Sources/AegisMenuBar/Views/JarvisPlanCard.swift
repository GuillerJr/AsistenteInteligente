import SwiftUI

/// A reply written in plan mode (Codex's): its numbered steps, and implementing them
/// with one click. Writing in the composer instead keeps refining the plan.
struct JarvisPlanCard: View {
    let text: String
    let implemented: Bool
    let implement: ([String]) -> Void

    /// The first numbered list only: a later list (options, notes) is not the plan.
    static func steps(in text: String) -> [String] {
        var steps: [String] = []
        for line in text.split(separator: "\n") {
            // "1. Paso" or "2) Paso".
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let digits = trimmed.prefix(while: \.isNumber)
            let rest = trimmed.dropFirst(digits.count)
            if (1...2).contains(digits.count), rest.first == "." || rest.first == ")",
               rest.dropFirst().first == " " {
                let step = rest.dropFirst().trimmingCharacters(in: .whitespaces)
                if !step.isEmpty { steps.append(step.replacingOccurrences(of: "**", with: "")) }
            } else if !steps.isEmpty && !trimmed.isEmpty {
                break
            }
        }
        return steps
    }

    var body: some View {
        let steps = Self.steps(in: text)
        if steps.count >= 2 {
            VStack(alignment: .leading, spacing: 8) {
                Label(String(localized: "Plan · \(steps.count) pasos", bundle: .module), systemImage: "list.bullet.clipboard")
                    .font(.system(size: 12.5, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(String(localized: "\(index + 1)", bundle: .module)).font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(JarvisTheme.inkMuted).frame(width: 14, alignment: .trailing)
                        Text(step).font(.system(size: 13)).foregroundStyle(JarvisTheme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                HStack(spacing: 10) {
                    if implemented {
                        Label(String(localized: "Enviado para implementar", bundle: .module), systemImage: "checkmark")
                            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    } else {
                        Button(String(localized: "Implementar plan", bundle: .module)) { implement(steps) }
                            .buttonStyle(JarvisButtonStyle(prominent: true, compact: true))
                        Text(String(localized: "o escribe para ajustarlo", bundle: .module)).font(.system(size: 11.5))
                            .foregroundStyle(JarvisTheme.inkMuted)
                    }
                }
                .padding(.top, 2)
            }
            .padding(14)
            .frame(maxWidth: 560, alignment: .leading)
            .background(JarvisTheme.raised, in: RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: JarvisTheme.radiusCard, style: .continuous)
                .strokeBorder(JarvisTheme.hairline))
        }
    }
}
