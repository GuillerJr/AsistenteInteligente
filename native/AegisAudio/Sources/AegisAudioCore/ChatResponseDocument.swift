import Foundation

/// Display-only Markdown. The original message remains available for copying and history.
public struct ChatResponseDocument: Sendable {
    public struct Block: Identifiable, Sendable {
        public enum Kind: Equatable, Sendable {
            case paragraph
            case heading(Int)
            case code(String?)
            case tableRow(header: Bool)
            case divider
        }

        public let id: Int
        public var content: [AttributedString]
        public let kind: Kind
        public let marker: String?
        public let listDepth: Int
        public let quoteDepth: Int
    }

    public let blocks: [Block]

    public init(_ source: String) {
        guard let parsed = try? AttributedString(markdown: source, options: .init(
            allowsExtendedAttributes: false,
            interpretedSyntax: .full,
            failurePolicy: .returnPartiallyParsedIfPossible
        )) else {
            blocks = [Block(id: 0, content: [AttributedString(source)], kind: .paragraph,
                            marker: nil, listDepth: 0, quoteDepth: 0)]
            return
        }
        var result: [Block] = []
        var displayedItems: Set<Int> = []
        for (intent, range) in parsed.runs[\.presentationIntent] {
            let components = intent?.components ?? []
            var content = AttributedString(parsed[range])
            content.presentationIntent = nil
            var kind: Block.Kind = .paragraph
            var identity = components.first?.identity ?? result.count
            var marker: String?
            var listDepth = 0
            var quoteDepth = 0
            for (index, component) in components.enumerated() {
                switch component.kind {
                case .header(let level): kind = .heading(level)
                case .codeBlock(let language): kind = .code(language)
                case .thematicBreak: kind = .divider
                case .tableHeaderRow:
                    kind = .tableRow(header: true)
                    identity = component.identity
                case .tableRow:
                    kind = .tableRow(header: false)
                    identity = component.identity
                case .blockQuote: quoteDepth += 1
                case .listItem(let ordinal):
                    listDepth += 1
                    if listDepth == 1, displayedItems.insert(component.identity).inserted {
                        let parent = components.dropFirst(index + 1).first?.kind
                        marker = parent == .orderedList ? "\(ordinal)." : "•"
                    }
                default: break
                }
            }
            if case .tableRow = kind, result.last?.id == identity {
                result[result.count - 1].content.append(content)
            } else {
                result.append(Block(id: identity, content: [content], kind: kind,
                                    marker: marker, listDepth: listDepth, quoteDepth: quoteDepth))
            }
        }
        // Empty/incomplete Markdown must not make a nonempty response disappear.
        blocks = result.isEmpty && !source.isEmpty
            ? [Block(id: 0, content: [AttributedString(source)], kind: .paragraph,
                     marker: nil, listDepth: 0, quoteDepth: 0)] : result
    }
}
