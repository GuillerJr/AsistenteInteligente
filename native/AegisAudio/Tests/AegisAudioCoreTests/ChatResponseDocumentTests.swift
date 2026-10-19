import Foundation
import Testing
@testable import AegisAudioCore

@Suite("Chat response formatting")
struct ChatResponseDocumentTests {
    @Test func emailSummaryKeepsEmphasisAndSeparateNumberedItems() {
        let document = ChatResponseDocument("""
        Consulté **10 mensajes sin leer**.

        1. **Asunto uno** — De: remitente uno
        2. **Asunto dos** — De: remitente dos

        Puedo mostrar su contenido.
        """)
        #expect(document.blocks.count == 4)
        #expect(document.blocks.map(\.marker) == [nil, "1.", "2.", nil])
        let intro = document.blocks[0].content[0]
        #expect(String(intro.characters) == "Consulté 10 mensajes sin leer.")
        #expect(intro.runs.contains {
            $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true
        })
        #expect(String(document.blocks[1].content[0].characters)
                == "Asunto uno — De: remitente uno")
    }

    @Test func nestedListsAndContinuationParagraphsKeepTheirHierarchy() {
        let document = ChatResponseDocument("""
        3. Primero

           Continuación.
           - Anidado
        4. Segundo
        """)
        #expect(document.blocks.map(\.marker) == ["3.", nil, "•", "4."])
        #expect(document.blocks.map(\.listDepth) == [1, 1, 2, 1])
        #expect(Set(document.blocks.map(\.id)).count == document.blocks.count)
    }

    @Test func codeBlocksPreserveLiteralMarkdownAndIndentation() {
        let document = ChatResponseDocument("""
        ```swift
        let value = "**literal**"
            print(value)
        ```
        """)
        #expect(document.blocks.count == 1)
        #expect(document.blocks[0].kind == .code("swift"))
        #expect(String(document.blocks[0].content[0].characters)
                == "let value = \"**literal**\"\n    print(value)\n")
    }

    @Test func headingsQuotesInlineCodeAndLinksKeepTheirNativeAttributes() {
        let document = ChatResponseDocument("""
        ## Resumen

        > *Una nota* con `código` y [enlace](https://example.com).
        """)
        #expect(document.blocks[0].kind == .heading(2))
        #expect(document.blocks[1].quoteDepth == 1)
        let content = document.blocks[1].content[0]
        #expect(content.runs.contains { $0.inlinePresentationIntent?.contains(.code) == true })
        #expect(content.runs.contains { $0.link?.absoluteString == "https://example.com" })
        #expect(content.presentationIntent == nil)
    }

    @Test func tablesKeepAllCellsGroupedByRow() {
        let document = ChatResponseDocument("""
        | Asunto | Estado |
        | --- | --- |
        | **Uno** | Pendiente |
        | Dos | Listo |
        """)
        #expect(document.blocks.count == 3)
        #expect(document.blocks[0].kind == .tableRow(header: true))
        #expect(document.blocks[1].kind == .tableRow(header: false))
        #expect(document.blocks.map { $0.content.map { String($0.characters) } }
                == [["Asunto", "Estado"], ["Uno", "Pendiente"], ["Dos", "Listo"]])
    }

    @Test func partialStreamsNeverLoseTheirText() {
        for source in ["", "Una **respuesta", "Una **respuesta completa**", "```swift\nlet x = 1"] {
            let document = ChatResponseDocument(source)
            let visible = document.blocks.flatMap(\.content).map { String($0.characters) }.joined()
            #expect(source.isEmpty || !visible.isEmpty)
        }
    }
}
