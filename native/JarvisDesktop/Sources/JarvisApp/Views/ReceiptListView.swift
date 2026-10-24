import DesktopCore
import SwiftUI

struct ReceiptListView: View {
    let receipts: [ActionReceipt]

    var body: some View {
        DisclosureGroup("Recibos de herramientas (\(receipts.count))") {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(receipts) { receipt in
                        VStack(alignment: .leading, spacing: 6) {
                            Label(receipt.label, systemImage: receipt.unknown ? "questionmark.circle" : receipt.verified ? "checkmark.seal" : "doc.text")
                                .font(.callout.weight(.semibold))
                            Text(receipt.toolName).font(.caption).foregroundStyle(.secondary)
                            if !receipt.output.isEmpty {
                                if receipt.outputTruncated {
                                    Text("Vista parcial del resultado").font(.caption2).foregroundStyle(.secondary)
                                }
                                Text(receipt.output).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                            }
                            if receipt.error != nil {
                                Text("La herramienta no confirmó un resultado satisfactorio.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(.vertical, 8)
            }.frame(maxHeight: 180)
        }.font(.caption)
    }
}
