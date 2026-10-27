import SwiftUI

/// One stable accessibility element with an explicit changing value. Child
/// Text/Form/LabeledContent aggregation must not retain an obsolete value.
struct MemoryMetadataRow: View {
    let label: String
    let value: String
    let identifier: String
    init(_ label: String, value: String, id: String) {
        self.label = label; self.value = value; identifier = id
    }
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(verbatim: label)
            Spacer(minLength: 8)
            Text(verbatim: value).multilineTextAlignment(.trailing)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(verbatim: label))
        .accessibilityValue(Text(verbatim: value))
        .accessibilityIdentifier(identifier)
        .accessibilityAddTraits(.isStaticText)
    }
}
