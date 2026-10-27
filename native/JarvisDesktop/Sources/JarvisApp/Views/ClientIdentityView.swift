import DesktopCore
import SwiftUI

struct ClientIdentityView: View {
    @Bindable var store: ChatStore
    @State private var publicKey = ""
    @State private var scope = "read"
    @State private var revoke: LocalClientRow?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Clientes locales firmados").font(.title3.bold())
                Text("Cada petición prueba la posesión de una clave admitida para este perfil. El token de conexión no concede identidad ni permisos.")
                    .font(.callout).foregroundStyle(.secondary)
                Button("Consultar clientes") { Task { await store.clientIdentities.refresh() } }
                    .disabled(store.clientIdentities.isRequesting || !store.ready)
                if store.clientIdentities.isRequesting { ProgressView() }
                if let snapshot = store.clientIdentities.snapshot {
                    Text("Revisión \(snapshot.revision)").font(.caption)
                    ForEach(snapshot.clients) { row in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(row.clientID).font(.caption.monospaced()).textSelection(.enabled)
                            HStack {
                                Text(row.revoked ? "Revocado" : row.scope == "owner" ? "Gestión y decisiones" : "Sólo lectura")
                                    .font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                if row.clientID == snapshot.clientID { Text("Este cliente").font(.caption) }
                                else {
                                    Button("Revocar") { revoke = row }
                                        .disabled(row.revoked || store.clientIdentities.blocksInteraction)
                                }
                            }
                        }.padding(10).background(.quaternary, in: .rect(cornerRadius: 8))
                    }
                    Divider()
                    Text("Admitir otro cliente").font(.headline)
                    Text("Introduce únicamente su clave pública Ed25519: 64 caracteres hexadecimales. Conserva su clave privada fuera del chat y del modelo.")
                        .font(.caption).foregroundStyle(.secondary)
                    TextField("Clave pública", text: $publicKey).font(.caption.monospaced())
                    Picker("Alcance", selection: $scope) {
                        Text("Sólo lectura").tag("read")
                        Text("Gestión y decisiones").tag("owner")
                    }
                    Button("Preparar revisión") {
                        Task { await store.clientIdentities.prepare(publicKey: publicKey, scope: scope) }
                    }.disabled(store.clientIdentities.blocksInteraction || !ManagementValidation.hash(publicKey))
                }
                if let review = store.clientIdentities.review {
                    TimelineView(.periodic(from: .now, by: 1)) { timeline in
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Revisa esta identidad").font(.headline)
                            Text(review.clientID).font(.caption.monospaced()).textSelection(.enabled)
                            Text(review.scope == "owner" ? "Podrá gestionar el perfil y tomar decisiones de autorización." : "Sólo podrá consultar los métodos de lectura admitidos.")
                                .font(.callout)
                            if timeline.date < review.expiresAt {
                                Button("Autorizar este cliente") { Task { await store.clientIdentities.admit() } }
                                    .buttonStyle(.borderedProminent).disabled(store.clientIdentities.isRequesting)
                            } else { Text("La revisión caducó. Prepara otra si deseas continuar.").foregroundStyle(.orange) }
                            Button("Descartar revisión") { store.clientIdentities.discardReview() }
                                .disabled(store.clientIdentities.isRequesting)
                        }.padding(12).background(.quaternary, in: .rect(cornerRadius: 10))
                    }
                }
                if let notice = store.clientIdentities.observationNotice { Text(notice).font(.caption) }
                if let error = store.clientIdentities.errorMessage { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
                if store.clientIdentities.responseUnconfirmed {
                    Button("Conservar decisión sin confirmar") { store.clientIdentities.retainUnconfirmed() }
                        .disabled(store.clientIdentities.isRequesting)
                }
            }.padding(20)
        }
        .task { await store.clientIdentities.refresh() }
        .onDisappear { store.clientIdentities.discardReview() }
        .confirmationDialog("Revocar este cliente local", isPresented: Binding(get: { revoke != nil }, set: { if !$0 { revoke = nil } }), presenting: revoke) { row in
            Button("Revocar este cliente", role: .destructive) { Task { await store.clientIdentities.revoke(row); revoke = nil } }
            Button("Cancelar", role: .cancel) { revoke = nil }
        } message: { row in
            Text("Se rechazará la clave \(row.clientID). El cliente deberá dejar de actuar con esa identidad; el perfil y sus datos se conservan.")
        }
    }
}
