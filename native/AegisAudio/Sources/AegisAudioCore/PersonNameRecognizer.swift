import Foundation
import NaturalLanguage

/// On-device person-name recognition used to mask private names before a request
/// leaves the Mac. Organisation and place names are intentionally not returned:
/// they are usually the subject of a question rather than private data.
public enum PersonNameRecognizer {
    public static let maximumTextBytes = 16_384
    public static let maximumNames = 32

    public static func personNames(in text: String) -> [String] {
        guard
            !text.isEmpty,
            text.utf8.count <= maximumTextBytes,
            !text.unicodeScalars.contains(where: { $0.value == 0 })
        else {
            return []
        }
        let tagger = NLTagger(tagSchemes: [.nameType])
        tagger.string = text
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        if let language = recognizer.dominantLanguage {
            tagger.setLanguage(language, range: text.startIndex ..< text.endIndex)
        }
        var names: [String] = []
        tagger.enumerateTags(
            in: text.startIndex ..< text.endIndex,
            unit: .word,
            scheme: .nameType,
            options: [.omitPunctuation, .omitWhitespace, .joinNames]
        ) { tag, range in
            guard tag == .personalName else { return true }
            let name = String(text[range]).trimmingCharacters(in: .whitespacesAndNewlines)
            if name.count >= 2, !names.contains(name) {
                names.append(name)
            }
            return names.count < maximumNames
        }
        return names
    }
}
