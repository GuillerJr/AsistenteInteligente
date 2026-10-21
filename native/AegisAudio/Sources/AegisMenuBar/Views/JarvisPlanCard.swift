import SwiftUI

/// A reply written in plan mode (Codex's): its numbered steps, and implementing them
/// with one click. Writing in the composer instead keeps refining the plan.
struct JarvisPlanCard: View {
    let text: String
    let implemented: Bool
    let implement: ([String]) -> Void

    static func steps(in text: String) -> [String] {
        text.split(separator: "\n").compactMap { line in
            // "1. Paso" or "2) Paso".
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let digits = trimmed.prefix(while: \.isNumber)
            let rest = trimmed.dropFirst(digits.count)
            guard (1...2).contains(digits.count), rest.first == "." || rest.first == ")",
                  rest.dropFirst().first == " " else { return nil }
            let step = rest.dropFirst().trimmingCharacters(in: .whitespaces)
            return step.isEmpty ? nil : step.replacingOccurrences(of: "**", with: "")
        }
    }

    var body: some View {
        let steps = Self.steps(in: text)
        if steps.count >= 2 {
            VStack(alignment: .leading, spacing: 8) {
                Label("Plan · \(steps.count) pasos", systemImage: "list.bullet.clipboard")
                    .font(.system(size: 12.5, weight: .semibold)).foregroundStyle(JarvisTheme.ink)
                ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(index + 1)").font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(JarvisTheme.inkMuted).frame(width: 14, alignment: .trailing)
                        Text(step).font(.system(size: 13)).foregroundStyle(JarvisTheme.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                HStack(spacing: 10) {
                    if implemented {
                        Label("Enviado para implementar", systemImage: "checkmark")
                            .font(.system(size: 12)).foregroundStyle(JarvisTheme.inkSecondary)
                    } else {
                        Button("Implementar plan") { implement(steps) }
                            .buttonStyle(JarvisButtonStyle(prominent: true, compact: true))
                        Text("o escribe para ajustarlo").font(.system(size: 11.5))
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
