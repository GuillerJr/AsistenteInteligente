import Darwin
import Foundation

public enum BrowserAutomationError: Error, LocalizedError, Equatable, Sendable {
    case invalidArguments, forbiddenURL, deniedAddress, unavailable, unknownTab, staleSnapshot
    case cancelled, uncertainEffect, alreadySubmitted, busy, unsupported, oversized

    public var errorDescription: String? {
        switch self {
        case .invalidArguments: return "Los argumentos del navegador no son válidos."
        case .forbiddenURL: return "Esa dirección no está permitida en este navegador."
        case .deniedAddress: return "El destino de red no es público o cambió de identidad."
        case .unavailable: return "El navegador no confirmó su respuesta. No se reintentará la operación."
        case .unknownTab: return "La pestaña elegida ya no está adjunta a Jarvis."
        case .staleSnapshot: return "La página cambió. Observa de nuevo antes de actuar."
        case .cancelled: return "La operación de navegador fue cancelada."
        case .uncertainEffect: return "La respuesta se perdió después del despacho; el efecto queda sin confirmar."
        case .alreadySubmitted: return "Esta identidad de operación ya se envió; consulta su recibo, sin repetirla."
        case .busy: return "Espera a que cierre la operación anterior del navegador."
        case .unsupported: return "Ese control no está disponible para esta pestaña."
        case .oversized: return "La respuesta del navegador supera el límite permitido."
        }
    }
}

/// Exact synthetic loopback origins can only be admitted by the native host.
/// Model arguments cannot add origins or relax this network policy.
public struct BrowserAutomationPolicy: Sendable {
    public let testOrigins: Set<String>

    public init(testOrigins: Set<String> = []) {
        self.testOrigins = Set(testOrigins.compactMap { value in
            guard let components = URLComponents(string: value), components.scheme == "http",
                  ["127.0.0.1", "[::1]", "::1"].contains(components.host ?? ""),
                  let port = components.port, (1...65535).contains(port),
                  components.user == nil, components.password == nil,
                  components.query == nil, components.fragment == nil,
                  components.path.isEmpty || components.path == "/",
                  let url = components.url else { return nil }
            return Self.origin(url)
        })
    }

    public func validate(_ raw: String) throws -> URL {
        guard raw.utf8.count <= 4096, !raw.contains("\0"),
              let components = URLComponents(string: raw),
              components.user == nil, components.password == nil,
              let host = components.host?.lowercased(), !host.isEmpty,
              let url = components.url else { throw BrowserAutomationError.forbiddenURL }
        if testOrigins.contains(Self.origin(url)), components.scheme == "http" { return url }
        guard components.scheme == "https", components.port == nil || components.port == 443,
              !host.hasSuffix("."), !host.hasSuffix(".local"), !host.hasSuffix(".localhost"),
              !host.hasSuffix(".internal"), host != "localhost",
              host.range(of: "^[a-z0-9.:-]+$", options: .regularExpression) != nil
        else { throw BrowserAutomationError.forbiddenURL }
        // Literal addresses are screened before opening any connection. Domain
        // addresses are resolved and pinned by the mandatory CONNECT proxy.
        if host.contains(":"), !Self.publicAddress(host.replacingOccurrences(of: "[", with: "")
            .replacingOccurrences(of: "]", with: "")) { throw BrowserAutomationError.deniedAddress }
        if host.allSatisfy({ $0.isNumber || $0 == "." }), !Self.publicAddress(host) {
            throw BrowserAutomationError.deniedAddress
        }
        return url
    }

    public func pinnedAddress(for url: URL) throws -> String {
        let host = (url.host ?? "").replacingOccurrences(of: "[", with: "")
            .replacingOccurrences(of: "]", with: "")
        if testOrigins.contains(Self.origin(url)) { return host }
        _ = try validate(url.absoluteString)
        let addresses = try Self.resolve(host)
        guard !addresses.isEmpty, addresses.allSatisfy(Self.publicAddress), let first = addresses.first
        else { throw BrowserAutomationError.deniedAddress }
        return first
    }

    public static func origin(_ url: URL) -> String {
        let scheme = url.scheme?.lowercased() ?? ""
        let host = url.host?.lowercased() ?? ""
        let port = url.port ?? (scheme == "https" ? 443 : 80)
        return "\(scheme)://\(host)" + ((scheme == "https" && port == 443 || scheme == "http" && port == 80) ? "" : ":\(port)")
    }

    public static func publicAddress(_ address: String) -> Bool {
        var v4 = in_addr()
        if inet_pton(AF_INET, address, &v4) == 1 {
            let bytes = withUnsafeBytes(of: &v4) { Array($0) }
            let a = bytes[0], b = bytes[1], c = bytes[2]
            return !(a == 0 || a == 10 || a == 127 || a >= 224 ||
                (a == 100 && (64...127).contains(b)) || (a == 169 && b == 254) ||
                (a == 172 && (16...31).contains(b)) || (a == 192 && b == 168) ||
                (a == 192 && b == 0) || (a == 192 && b == 2) ||
                (a == 198 && (b == 18 || b == 19 || b == 51 && c == 100)) ||
                (a == 203 && b == 0 && c == 113))
        }
        var v6 = in6_addr()
        guard inet_pton(AF_INET6, address, &v6) == 1 else { return false }
        let bytes = withUnsafeBytes(of: &v6) { Array($0) }
        // Global unicast 2000::/3 only; deny documentation and transition forms.
        guard bytes[0] & 0xe0 == 0x20 else { return false }
        if bytes[0...3].elementsEqual([0x20, 0x01, 0x0d, 0xb8]) { return false }
        if bytes[0...3].elementsEqual([0x20, 0x01, 0x00, 0x00]) { return false }
        if bytes[0] == 0x20 && bytes[1] == 0x02 { return false }
        return true
    }

    private static func resolve(_ host: String) throws -> [String] {
        var hints = addrinfo()
        hints.ai_family = AF_UNSPEC
        hints.ai_socktype = SOCK_STREAM
        hints.ai_protocol = IPPROTO_TCP
        var result: UnsafeMutablePointer<addrinfo>?
        guard getaddrinfo(host, nil, &hints, &result) == 0, let head = result else {
            throw BrowserAutomationError.deniedAddress
        }
        defer { freeaddrinfo(head) }
        var current: UnsafeMutablePointer<addrinfo>? = head
        var addresses: [String] = []
        var count = 0
        while let row = current {
            count += 1
            guard count <= 64 else { throw BrowserAutomationError.deniedAddress }
            var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(row.pointee.ai_addr, row.pointee.ai_addrlen, &buffer,
                socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 {
                let value = String(cString: buffer)
                if !addresses.contains(value) { addresses.append(value) }
            }
            current = row.pointee.ai_next
        }
        return addresses
    }
}
