import AegisAudioCore
import SwiftUI

struct JarvisChatResponseView: View {
    private let document: ChatResponseDocument

    init(_ text: String) {
        // The verified file card owns these local links; never open them as web URLs.
        let prose = text.replacingOccurrences(
            of: #"\[[^\]\n]{1,120}\]\(jarvis-artifact://[0-9a-f]{32}\)"#,
            with: "", options: .regularExpression)
        document = ChatResponseDocument(prose)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(document.blocks) { block in
                HStack(alignment: .top, spacing: 8) {
                    if block.quoteDepth > 0 {
                        RoundedRectangle(cornerRadius: 2).fill(Color.primary.opacity(0.25)).frame(width: 3)
                    }
                    if block.listDepth > 0 {
                        Text(verbatim: block.marker ?? "")
                            .font(.system(size: 14)).monospacedDigit()
                            .frame(minWidth: 24, alignment: .trailing)
                            .accessibilityHidden(block.marker == nil)
                    }
                    blockView(block)
                }
                .padding(.leading, CGFloat(max(0, block.listDepth - 1)) * 20)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func blockView(_ block: ChatResponseDocument.Block) -> some View {
        switch block.kind {
        case .code(let language):
            VStack(alignment: .leading, spacing: 8) {
                if let language, !language.isEmpty {
                    Text(verbatim: language).font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                ScrollView(.horizontal) {
                    Text(block.content[0]).font(.system(size: 13, design: .monospaced))
                        .textSelection(.enabled).fixedSize(horizontal: true, vertical: true)
                        .padding(.bottom, 2)
                }
            }
            .padding(12).background(.black.opacity(0.18), in: RoundedRectangle(cornerRadius: 8))
        case .divider:
            Divider()
        case .tableRow(let header):
            HStack(alignment: .top, spacing: 12) {
                ForEach(block.content.indices, id: \.self) { index in
                    formattedText(block.content[index])
                        .font(.system(size: 13, weight: header ? .semibold : .regular))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }.padding(8).background(Color.primary.opacity(header ? 0.06 : 0.02))
        case .heading(let level):
            formattedText(block.content[0])
                .font(.system(size: level == 1 ? 20 : level == 2 ? 17 : 15, weight: .semibold))
        case .paragraph:
            formattedText(block.content[0]).font(.system(size: 14))
        }
    }

    private func formattedText(_ source: AttributedString) -> some View {
        var styled = source
        for run in source.runs where run.inlinePresentationIntent?.contains(.code) == true {
            styled[run.range].font = .system(size: 13, design: .monospaced)
        }
        return Text(styled).lineSpacing(4).textSelection(.enabled)
            .fixedSize(horizontal: false, vertical: true)
    }
}
