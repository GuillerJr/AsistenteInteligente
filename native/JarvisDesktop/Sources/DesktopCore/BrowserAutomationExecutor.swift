import AppKit
import CryptoKit
import Foundation
import Network
import WebKit

/// Own browser and explicitly attached page transports. The host admits the
/// operation before calling this executor; no operation is retried here.
@MainActor
public final class BrowserAutomationExecutor {
    public var beforeEffect: (@MainActor (String) async throws -> Void)?
    private let policy: BrowserAutomationPolicy
    private let showWindows: Bool
    private var tabs: [String: BrowserAutomationTab] = [:]
    private var submitted: Set<String> = []
    private var cancelled: Set<String> = []
    private var activeOperation: String?
    private var activeTab: String?
    private var epoch = 0
    private var proxy: BrowserAutomationProxy?
    private var proxyConfiguration: ProxyConfiguration?
    private var contentRules: WKContentRuleList?
    private var ruleStore: WKContentRuleListStore?
    private var rulesDirectory: URL?
    private let worldName = "JarvisBrowser." + UUID().uuidString
    private lazy var contentWorld: WKContentWorld = .world(name: worldName)
    private var downloadSession: URLSession?

    public init(testOrigins: Set<String> = [], showWindows: Bool = true) {
        policy = BrowserAutomationPolicy(testOrigins: testOrigins)
        self.showWindows = showWindows
    }

    public func execute(_ command: String, arguments: [String: RPCValue]) async throws -> RPCValue {
        if command == "browser_status" {
            guard arguments.isEmpty || Set(arguments.keys) == ["operation_id"] else {
                throw BrowserAutomationError.invalidArguments
            }
            return .object(["tabs": .array(tabs.values.sorted { $0.id < $1.id }.map { tab in
                .object(["tab_id": .string(tab.id), "mode": .string(tab.cdp == nil ? "managed" : "attached"),
                         "connected": .bool(true), "snapshot_available": .bool(tab.snapshotID != nil)])
            }), "active": .bool(activeOperation != nil), "private_managed_profile": .bool(true)])
        }
        guard let operation = arguments["operation_id"]?.string,
              operation.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              submitted.count < 512 else { throw BrowserAutomationError.invalidArguments }
        guard !submitted.contains(operation) else { throw BrowserAutomationError.alreadySubmitted }
        guard activeOperation == nil else { throw BrowserAutomationError.busy }
        submitted.insert(operation); activeOperation = operation
        let currentEpoch = epoch
        defer { activeOperation = nil; activeTab = nil; cancelled.remove(operation) }
        do {
            let result = try await perform(command, arguments: arguments)
            guard !cancelled.contains(operation), currentEpoch == epoch, !Task.isCancelled else {
                throw BrowserAutomationError.uncertainEffect
            }
            return result
        } catch {
            if cancelled.contains(operation) || currentEpoch != epoch || Task.isCancelled {
                throw BrowserAutomationError.uncertainEffect
            }
            throw error
        }
    }

    public func cancel(operationID: String) async {
        guard activeOperation == operationID else { return }
        cancelled.insert(operationID)
        if let activeTab, let tab = tabs[activeTab] {
            tab.snapshotID = nil
            tab.page?.stop()
            tab.cdp?.close()
            if tab.cdp != nil { tabs.removeValue(forKey: activeTab) }
        }
        downloadSession?.invalidateAndCancel(); downloadSession = nil
    }

    public func reset() async {
        epoch += 1
        if let activeOperation { cancelled.insert(activeOperation) }
        let closing = Array(tabs.values)
        for tab in closing { tab.page?.close(); tab.cdp?.close() }
        for tab in closing { await tab.page?.waitForEvaluationCleanup() }
        tabs.removeAll()
        downloadSession?.invalidateAndCancel(); downloadSession = nil
        await proxy?.stop(); proxy = nil; proxyConfiguration = nil
        contentRules = nil; ruleStore = nil
        if let rulesDirectory { try? FileManager.default.removeItem(at: rulesDirectory) }
        rulesDirectory = nil
        // Submitted IDs stay consumed across reset. The durable host ledger owns
        // rediscovery; reset cannot make an uncertain operation replayable.
    }

    private func checkEffect() async throws {
        guard let operation = activeOperation, !cancelled.contains(operation), !Task.isCancelled,
              activeTab.map({ tabs[$0] != nil }) != false else {
            throw BrowserAutomationError.cancelled
        }
        try await beforeEffect?(operation)
        guard activeOperation == operation, !cancelled.contains(operation), !Task.isCancelled,
              activeTab.map({ tabs[$0] != nil }) != false else {
            throw BrowserAutomationError.cancelled
        }
    }

    func networkStatistics() async -> Int { await proxy?.authorizedCount() ?? 0 }

    private func check(_ args: [String: RPCValue], _ allowed: Set<String>, required: Set<String>) throws {
        guard Set(args.keys).isSubset(of: allowed.union(["operation_id"])),
              required.isSubset(of: Set(args.keys)) else { throw BrowserAutomationError.invalidArguments }
    }

    private func string(_ args: [String: RPCValue], _ key: String, max: Int = 4096) throws -> String {
        guard let text = args[key]?.string, !text.isEmpty, text.utf8.count <= max,
              !text.contains("\0") else { throw BrowserAutomationError.invalidArguments }
        return text
    }

    private func selected(_ args: [String: RPCValue]) throws -> BrowserAutomationTab {
        let id = try string(args, "tab_id", max: 32)
        guard let tab = tabs[id] else { throw BrowserAutomationError.unknownTab }
        activeTab = id
        return tab
    }

    private func networkConfiguration() async throws -> ProxyConfiguration {
        if let proxyConfiguration { return proxyConfiguration }
        let candidate = BrowserAutomationProxy(policy: policy)
        let config = try await candidate.start()
        guard !Task.isCancelled, activeOperation.map({ !cancelled.contains($0) }) != false else {
            await candidate.stop(); throw BrowserAutomationError.cancelled
        }
        proxy = candidate; proxyConfiguration = config
        return config
    }

    private func networkRules() async throws -> WKContentRuleList {
        if let contentRules { return contentRules }
        // CFNetwork deliberately bypasses CONNECT proxies for loopback HTTP.
        // The exact fixture exceptions must therefore be enforced in WebKit too.
        var rules: [[String: Any]] = [["trigger": ["url-filter": ".*"], "action": ["type": "block"]]]
        func append(_ pattern: String, action: String) {
            rules.append(["trigger": ["url-filter": pattern], "action": ["type": action]])
        }
        // WebKit's content-filter grammar deliberately omits disjunctions.
        for scheme in ["https", "wss", "data", "blob"] {
            append("^" + scheme + ":", action: "ignore-previous-rules")
        }
        for scheme in ["https", "wss"] {
            let prefix = "^" + scheme + "://"
            append(prefix + #"\["#, action: "block")
            for boundary in [":", "/", "$"] {
                append(prefix + "[0-9.]+" + boundary, action: "block")
                append(prefix + "localhost" + boundary, action: "block")
                for domain in ["local", "localhost", "internal"] {
                    append(prefix + #"[^/]*\."# + domain + boundary, action: "block")
                }
            }
        }
        for origin in policy.testOrigins.sorted() {
            let prefix = "^" + NSRegularExpression.escapedPattern(for: origin)
            append(prefix + "/", action: "ignore-previous-rules")
            append(prefix + "$", action: "ignore-previous-rules")
        }
        let directory = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("jarvis-browser-rules-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        rulesDirectory = directory
        guard let store = WKContentRuleListStore(url: directory),
              let json = String(data: try JSONSerialization.data(withJSONObject: rules), encoding: .utf8)
        else { throw BrowserAutomationError.unavailable }
        ruleStore = store
        let list: WKContentRuleList = try await withCheckedThrowingContinuation { continuation in
            store.compileContentRuleList(forIdentifier: "jarvis-browser-network", encodedContentRuleList: json) { value, error in
                if error != nil || value == nil { continuation.resume(throwing: BrowserAutomationError.unavailable) }
                else { continuation.resume(returning: value!) }
            }
        }
        contentRules = list
        return list
    }

    private func perform(_ command: String, arguments args: [String: RPCValue]) async throws -> RPCValue {
        switch command {
        case "browser_open":
            try check(args, ["url"], required: ["url"])
            guard tabs.count < 8 else { throw BrowserAutomationError.busy }
            let url = try policy.validate(string(args, "url"))
            let config = try await networkConfiguration()
            let rules = try await networkRules()
            try await checkEffect()
            let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
            let tab = BrowserAutomationTab(id: id)
            tab.page = BrowserManagedPage(policy: policy, proxy: config, rules: rules, windowID: id, showWindow: showWindows,
                onNavigation: { [weak tab] in tab?.snapshotID = nil },
                onClosed: { [weak self] in self?.tabs.removeValue(forKey: id) })
            tabs[id] = tab; activeTab = id
            do { try await tab.page!.navigate(url) }
            catch {
                tab.page?.close(); await tab.page?.waitForEvaluationCleanup()
                tabs.removeValue(forKey: id); throw error
            }
            return try await observe(tab, expectedOrigin: BrowserAutomationPolicy.origin(url))
        case "browser_attach":
            try check(args, ["endpoint", "target_id"], required: ["endpoint", "target_id"])
            guard tabs.count < 8, let url = URL(string: try string(args, "endpoint")) else {
                throw BrowserAutomationError.invalidArguments
            }
            let target = try string(args, "target_id", max: 128)
            try await checkEffect()
            let connection = try BrowserAutomationCDP(endpoint: url, targetID: target)
            do {
                let info = try await connection.call("Target.getTargetInfo", params: ["targetId": .string(target)])
                guard info["targetInfo"]["targetId"].string == target,
                      info["targetInfo"]["type"].string == "page",
                      let location = info["targetInfo"]["url"].string else { throw BrowserAutomationError.unknownTab }
                _ = try policy.validate(location)
                _ = try await connection.call("Page.enable")
                let id = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
                let tab = BrowserAutomationTab(id: id); tab.cdp = connection
                tabs[id] = tab; activeTab = id
                return try await observe(tab, expectedOrigin: BrowserAutomationPolicy.origin(policy.validate(location)))
            } catch { connection.close(); throw error }
        case "browser_observe":
            try check(args, ["tab_id", "expected_origin"], required: ["tab_id", "expected_origin"])
            return try await observe(selected(args), expectedOrigin: admittedOrigin(args))
        case "browser_navigate":
            try check(args, ["tab_id", "url", "expected_origin"], required: ["tab_id", "url", "expected_origin"])
            let tab = try selected(args), url = try policy.validate(string(args, "url"))
            try await validatePage(tab, expectedOrigin: admittedOrigin(args))
            tab.snapshotID = nil
            if let page = tab.page { try await checkEffect(); try await page.navigate(url) }
            else if let cdp = tab.cdp {
                try await assertAttachedTarget(tab)
                try await checkEffect()
                let response = try await cdp.call("Page.navigate", params: ["url": .string(url.absoluteString)])
                guard response["errorText"] == .null else { throw BrowserAutomationError.unavailable }
                tab.contextID = nil
                try await waitAttachedReady(tab, expectedURL: url)
            }
            return try await observe(tab, expectedOrigin: BrowserAutomationPolicy.origin(url))
        case "browser_search":
            try check(args, ["query", "search_url_template"], required: ["query", "search_url_template"])
            let query = try string(args, "query", max: 1024)
            let template = try string(args, "search_url_template")
            guard template.components(separatedBy: "{query}").count == 2,
                  let encoded = query.addingPercentEncoding(withAllowedCharacters: .alphanumerics) else {
                throw BrowserAutomationError.invalidArguments
            }
            let url = try policy.validate(template.replacingOccurrences(of: "{query}", with: encoded))
            return try await perform("browser_open", arguments: ["url": .string(url.absoluteString)])
        case "browser_action":
            try check(args, ["tab_id", "snapshot_id", "ref", "action", "text", "value", "key", "expected_origin"],
                required: ["tab_id", "snapshot_id", "ref", "action", "expected_origin"])
            let tab = try selected(args), action = try string(args, "action", max: 16)
            guard ["click", "fill", "select", "scroll", "press"].contains(action),
                  tab.snapshotID == (try string(args, "snapshot_id", max: 32)) else {
                throw BrowserAutomationError.staleSnapshot
            }
            _ = try string(args, "ref", max: 16)
            if action == "fill" { guard let text = args["text"]?.string, text.utf8.count <= 8000 else { throw BrowserAutomationError.invalidArguments } }
            if action == "select" { _ = try string(args, "value", max: 2048) }
            if action == "press" {
                guard let key = args["key"]?.string,
                      ["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].contains(key)
                else { throw BrowserAutomationError.invalidArguments }
            }
            let origin = try admittedOrigin(args)
            try await validatePage(tab, expectedOrigin: origin)
            var actionArguments = args; actionArguments["expected_origin"] = .string(origin)
            let inspected = try await evaluate(BrowserAutomationScripts.inspect, payload: actionArguments, tab: tab)
            guard inspected["error"] == .null else { throw BrowserAutomationError.staleSnapshot }
            if action == "click", let href = inspected["element"]["href"].string { _ = try policy.validate(href) }
            if action == "press", args["key"]?.string == "Enter", let form = inspected["form_action"].string {
                _ = try policy.validate(form)
            }
            // Revalidate the committed host lease after all asynchronous observation.
            try await checkEffect()
            // Final origin/DOM CAS inside the isolated script is adjacent to the effect.
            let result: RPCValue
            do { result = try await evaluate(BrowserAutomationScripts.action, payload: actionArguments, tab: tab) }
            catch { tab.snapshotID = nil; throw BrowserAutomationError.uncertainEffect }
            tab.snapshotID = nil
            guard result["error"] == .null else { throw BrowserAutomationError.staleSnapshot }
            let after = try? await observe(tab, expectedOrigin: origin)
            return .object(["tab_id": .string(tab.id), "action_dispatched": result["dispatched"],
                            "verified": result["verified"], "observation": after ?? .null,
                            "external_unverified": .bool(true)])
        case "browser_history":
            try check(args, ["tab_id", "direction", "expected_origin"], required: ["tab_id", "direction", "expected_origin"])
            let tab = try selected(args), direction = try string(args, "direction", max: 8)
            guard direction == "back" || direction == "forward" else { throw BrowserAutomationError.invalidArguments }
            let origin = try admittedOrigin(args)
            try await validatePage(tab, expectedOrigin: origin)
            tab.snapshotID = nil
            if let page = tab.page { try await checkEffect(); try await page.history(direction) }
            else if let cdp = tab.cdp {
                try await assertAttachedTarget(tab)
                let history = try await cdp.call("Page.getNavigationHistory")
                guard let current = history["currentIndex"].integer, let entries = history["entries"].array else {
                    throw BrowserAutomationError.unavailable
                }
                let index = current + (direction == "back" ? -1 : 1)
                guard entries.indices.contains(index), let url = entries[index]["url"].string,
                      let entry = entries[index]["id"].integer else { throw BrowserAutomationError.unsupported }
                _ = try policy.validate(url)
                try await checkEffect()
                _ = try await cdp.call("Page.navigateToHistoryEntry", params: ["entryId": .number(Double(entry))])
                tab.contextID = nil
                try await waitAttachedReady(tab, expectedURL: policy.validate(url))
            }
            if let value = try? await observe(tab, expectedOrigin: origin) { return value }
            return .object(["tab_id": .string(tab.id), "navigation_dispatched": .bool(true),
                "observation": .null, "verified": .bool(false), "origin_changed": .bool(true)])
        case "browser_close":
            try check(args, ["tab_id", "expected_origin"], required: ["tab_id"])
            let tab = try selected(args)
            if args["expected_origin"] != nil { try await validatePage(tab, expectedOrigin: admittedOrigin(args)) }
            try await checkEffect()
            tab.page?.close(); tab.cdp?.close()
            await tab.page?.waitForEvaluationCleanup()
            tabs.removeValue(forKey: tab.id)
            return .object(["tab_id": .string(tab.id), "closed": .bool(tab.page != nil),
                            "detached": .bool(tab.cdp != nil), "verified": .bool(true)])
        case "browser_download":
            try check(args, ["tab_id", "url", "expected_origin"], required: ["tab_id", "url", "expected_origin"])
            let tab = try selected(args), origin = try admittedOrigin(args)
            try await validatePage(tab, expectedOrigin: origin)
            let url = try policy.validate(string(args, "url"))
            guard BrowserAutomationPolicy.origin(url) == origin else { throw BrowserAutomationError.staleSnapshot }
            return try await download(url)
        default: throw BrowserAutomationError.unsupported
        }
    }

    private func admittedOrigin(_ args: [String: RPCValue]) throws -> String {
        let url = try policy.validate(string(args, "expected_origin"))
        guard url.path.isEmpty || url.path == "/", url.query == nil, url.fragment == nil else { throw BrowserAutomationError.invalidArguments }
        return BrowserAutomationPolicy.origin(url)
    }

    private func validatePage(_ tab: BrowserAutomationTab, expectedOrigin: String) async throws {
        if let page = tab.page {
            guard let url = page.webView.url else { throw BrowserAutomationError.unavailable }
            _ = try policy.validate(url.absoluteString)
            guard BrowserAutomationPolicy.origin(url) == expectedOrigin else { throw BrowserAutomationError.staleSnapshot }
        } else { try await assertAttachedTarget(tab)
            guard let url = tab.currentURL, BrowserAutomationPolicy.origin(url) == expectedOrigin else { throw BrowserAutomationError.staleSnapshot }
        }
    }

    private func assertAttachedTarget(_ tab: BrowserAutomationTab) async throws {
        guard let cdp = tab.cdp else { throw BrowserAutomationError.unknownTab }
        let info = try await cdp.call("Target.getTargetInfo", params: ["targetId": .string(cdp.targetID)])
        guard info["targetInfo"]["targetId"].string == cdp.targetID,
              info["targetInfo"]["type"].string == "page", let raw = info["targetInfo"]["url"].string else {
            throw BrowserAutomationError.unknownTab
        }
        tab.currentURL = try policy.validate(raw)
        if let old = tab.snapshotURL, old != raw { tab.snapshotID = nil; tab.contextID = nil }
    }

    private func observe(_ tab: BrowserAutomationTab, expectedOrigin: String) async throws -> RPCValue {
        try await validatePage(tab, expectedOrigin: expectedOrigin)
        let token = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        try await checkEffect()
        let value = try await evaluate(BrowserAutomationScripts.observe, payload: ["snapshot": .string(token), "expected_origin": .string(expectedOrigin)], tab: tab)
        guard value["snapshot_id"].string == token, let url = value["url"].string,
              let rows = value["elements"].array, rows.count <= 128,
              value["external_unverified"] == .bool(true),
              let object = value.object else { throw BrowserAutomationError.unavailable }
        _ = try policy.validate(url)
        let encoded = try JSONEncoder().encode(value)
        guard encoded.count <= 96 * 1024 else { throw BrowserAutomationError.oversized }
        tab.snapshotID = token; tab.snapshotURL = url
        var result = object
        result["tab_id"] = .string(tab.id)
        result["mode"] = .string(tab.cdp == nil ? "managed" : "attached")
        result["network_scope"] = .string(tab.cdp == nil ? "private_pinned_proxy" : "existing_browser_owner_network")
        result["synthetic_origin_exception"] = .bool(policy.testOrigins.contains(BrowserAutomationPolicy.origin(try policy.validate(url))))
        return .object(result)
    }

    private func evaluate(_ script: String, payload: [String: RPCValue], tab: BrowserAutomationTab) async throws -> RPCValue {
        guard !Task.isCancelled, activeOperation.map({ !cancelled.contains($0) }) != false else {
            throw BrowserAutomationError.cancelled
        }
        let expression = try BrowserAutomationScripts.expression(script, payload: payload)
        let raw: String
        if let page = tab.page {
            let result = try await page.evaluate(expression, world: contentWorld)
            guard let text = result as? String else { throw BrowserAutomationError.unavailable }
            raw = text
        } else if let cdp = tab.cdp {
            if tab.contextID == nil {
                let tree = try await cdp.call("Page.getFrameTree")
                guard let frame = tree["frameTree"]["frame"]["id"].string else { throw BrowserAutomationError.unavailable }
                let context = try await cdp.call("Page.createIsolatedWorld", params: [
                    "frameId": .string(frame), "worldName": .string(worldName), "grantUniveralAccess": .bool(false)])
                guard let id = context["executionContextId"].integer else { throw BrowserAutomationError.unavailable }
                tab.contextID = id
            }
            let result = try await cdp.call("Runtime.evaluate", params: ["expression": .string(expression),
                "contextId": .number(Double(tab.contextID!)), "returnByValue": .bool(true),
                "awaitPromise": .bool(false), "includeCommandLineAPI": .bool(false)])
            guard result["exceptionDetails"] == .null, let text = result["result"]["value"].string else {
                tab.contextID = nil; throw BrowserAutomationError.unavailable
            }
            raw = text
        } else { throw BrowserAutomationError.unknownTab }
        guard raw.utf8.count <= 96 * 1024, let bytes = raw.data(using: .utf8) else { throw BrowserAutomationError.oversized }
        return try JSONDecoder().decode(RPCValue.self, from: bytes)
    }

    private func waitAttachedReady(_ tab: BrowserAutomationTab, expectedURL: URL) async throws {
        for _ in 0..<30 {
            try Task.checkCancellation()
            guard let cdp = tab.cdp else { throw BrowserAutomationError.unknownTab }
            let tree = try await cdp.call("Page.getFrameTree")
            if tree["frameTree"]["frame"]["url"].string == expectedURL.absoluteString { return }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        throw BrowserAutomationError.unavailable
    }

    private func download(_ url: URL) async throws -> RPCValue {
        let proxy = try await networkConfiguration()
        let config = URLSessionConfiguration.ephemeral
        config.proxyConfigurations = [proxy]
        config.httpShouldSetCookies = false; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 10; config.timeoutIntervalForResource = 15
        let session = URLSession(configuration: config, delegate: BrowserAutomationNoRedirect(), delegateQueue: nil)
        downloadSession = session
        defer { session.invalidateAndCancel(); downloadSession = nil }
        try await checkEffect()
        let (stream, response) = try await session.bytes(from: url)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode),
              http.url == url, http.expectedContentLength <= 65_536 else { throw BrowserAutomationError.unavailable }
        var data = Data()
        for try await byte in stream {
            try Task.checkCancellation()
            guard data.count < 65_536, activeOperation.map({ !cancelled.contains($0) }) != false else {
                throw BrowserAutomationError.oversized
            }
            data.append(byte)
        }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let suggested = http.suggestedFilename ?? "archivo-descargado"
        let filename = String(suggested.filter { $0.isLetter || $0.isNumber || "._-".contains($0) }.prefix(128))
        return .object(["url": .string(url.absoluteString), "bytes": .number(Double(data.count)),
            "bytes_base64": .string(data.base64EncodedString()), "sha256": .string(digest),
            "filename": .string(filename.isEmpty ? "archivo-descargado" : filename),
            "media_type": .string(http.mimeType ?? "application/octet-stream"),
            "external_unverified": .bool(true), "written_to_workspace": .bool(false)])
    }
}

@MainActor
private final class BrowserAutomationTab {
    let id: String
    var page: BrowserManagedPage?
    var cdp: BrowserAutomationCDP?
    var snapshotID: String?
    var snapshotURL: String?
    var contextID: Int?
    var currentURL: URL?
    init(id: String) { self.id = id }
}

@MainActor
private final class BrowserManagedPage: NSObject, WKNavigationDelegate, WKUIDelegate, NSWindowDelegate {
    let webView: WKWebView
    let policy: BrowserAutomationPolicy
    let onNavigation: () -> Void
    let onClosed: () -> Void
    private(set) var isClosed = false
    private let window: NSWindow
    private var pendingNavigation: (UUID, WKNavigation?, CheckedContinuation<Void, Error>)?
    private var pendingEvaluation: [UUID: CheckedContinuation<Any?, Error>] = [:]
    private var interruptedEvaluations: Set<UUID> = []
    private var evaluationCleanupWaiters: [CheckedContinuation<Void, Never>] = []
    private var alert: NSAlert?

    init(policy: BrowserAutomationPolicy, proxy: ProxyConfiguration, rules: WKContentRuleList, windowID: String, showWindow: Bool,
         onNavigation: @escaping () -> Void, onClosed: @escaping () -> Void) {
        self.policy = policy; self.onNavigation = onNavigation; self.onClosed = onClosed
        _ = NSApplication.shared
        let configuration = WKWebViewConfiguration()
        let store = WKWebsiteDataStore.nonPersistent()
        store.proxyConfigurations = [proxy]
        configuration.websiteDataStore = store
        configuration.userContentController.add(rules)
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: 1000, height: 720), configuration: configuration)
        window = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 1000, height: 720),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        super.init()
        window.title = "Navegador privado de Jarvis"
        window.identifier = NSUserInterfaceItemIdentifier("jarvis.browser." + windowID)
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.contentView = webView
        webView.navigationDelegate = self; webView.uiDelegate = self
        if showWindow { window.makeKeyAndOrderFront(nil) }
    }

    func navigate(_ url: URL) async throws {
        _ = try policy.validate(url.absoluteString)
        try await navigation { webView.load(URLRequest(url: url)) }
    }

    func history(_ direction: String) async throws {
        let item = direction == "back" ? webView.backForwardList.backItem : webView.backForwardList.forwardItem
        guard let item else { throw BrowserAutomationError.unsupported }
        _ = try policy.validate(item.url.absoluteString)
        try await navigation { webView.go(to: item) }
    }

    private func navigation(_ begin: () -> WKNavigation?) async throws {
        guard !isClosed else { throw BrowserAutomationError.unknownTab }
        guard pendingNavigation == nil else { throw BrowserAutomationError.busy }
        let id = UUID()
        try await withCheckedThrowingContinuation { continuation in
            pendingNavigation = (id, nil, continuation)
            guard let navigation = begin() else { pendingNavigation = nil; continuation.resume(throwing: BrowserAutomationError.unavailable); return }
            pendingNavigation = (id, navigation, continuation)
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 12_000_000_000)
                guard let self, pendingNavigation?.0 == id else { return }
                webView.stopLoading(); finishNavigation(BrowserAutomationError.unavailable)
            }
        }
    }

    func evaluate(_ script: String, world: WKContentWorld) async throws -> Any? {
        guard !isClosed else { throw BrowserAutomationError.unknownTab }
        let id = UUID()
        return try await withCheckedThrowingContinuation { continuation in
            pendingEvaluation[id] = continuation
            // Own the page until WebKit's actual reply. stopLoading, a timeout,
            // or closing the window cannot establish that dispatched JS ended.
            webView.evaluateJavaScript(script, in: nil, in: world) { [self] result in
                guard let continuation = pendingEvaluation.removeValue(forKey: id) else { return }
                if interruptedEvaluations.remove(id) != nil {
                    continuation.resume(throwing: BrowserAutomationError.uncertainEffect)
                } else {
                    switch result { case .success(let value): continuation.resume(returning: value)
                    case .failure: continuation.resume(throwing: BrowserAutomationError.unavailable) }
                }
                evaluationDidSettle()
            }
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 8_000_000_000)
                guard let self, pendingEvaluation[id] != nil else { return }
                interruptedEvaluations.insert(id)
                // Keep custody if JavaScript does not return. Its eventual
                // callback or a process-termination notification is required.
                webView.stopLoading()
            }
        }
    }

    func stop() {
        webView.stopLoading(); onNavigation()
        finishNavigation(BrowserAutomationError.cancelled)
        interruptedEvaluations.formUnion(pendingEvaluation.keys)
        if alert != nil { window.endSheet(alert!.window, returnCode: .cancel); alert = nil }
    }

    func close() {
        stop(); window.close()
        if pendingEvaluation.isEmpty { webView.navigationDelegate = nil; webView.uiDelegate = nil }
    }

    func waitForEvaluationCleanup() async {
        guard !pendingEvaluation.isEmpty else { return }
        await withCheckedContinuation { evaluationCleanupWaiters.append($0) }
    }

    private func evaluationDidSettle() {
        guard pendingEvaluation.isEmpty else { return }
        let waiters = evaluationCleanupWaiters; evaluationCleanupWaiters.removeAll()
        if isClosed { webView.navigationDelegate = nil; webView.uiDelegate = nil }
        for waiter in waiters { waiter.resume() }
    }

    func windowWillClose(_ notification: Notification) {
        isClosed = true; stop(); onClosed()
    }

    private func finishNavigation(_ error: Error? = nil) {
        guard let (_, _, continuation) = pendingNavigation else { return }
        pendingNavigation = nil
        if let error { continuation.resume(throwing: error) } else { continuation.resume() }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "about:blank", action.targetFrame?.isMainFrame == false {
            decisionHandler(.allow); return
        }
        do { _ = try policy.validate(url.absoluteString); decisionHandler(.allow) }
        catch { decisionHandler(.cancel); if action.targetFrame?.isMainFrame != false { finishNavigation(BrowserAutomationError.forbiddenURL) } }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
        decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?
            .lowercased().contains("attachment") == true
        guard response.canShowMIMEType, !attachment else {
            decisionHandler(.cancel); if response.isForMainFrame { finishNavigation(BrowserAutomationError.unsupported) }; return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) { onNavigation() }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if pendingNavigation?.1 === navigation { finishNavigation() }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if pendingNavigation?.1 === navigation { finishNavigation(BrowserAutomationError.unavailable) }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if pendingNavigation?.1 === navigation { finishNavigation(BrowserAutomationError.unavailable) }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        stop()
        // Framework evidence that this owned process can no longer execute JS.
        let pending = pendingEvaluation.values; pendingEvaluation.removeAll()
        interruptedEvaluations.removeAll()
        for continuation in pending { continuation.resume(throwing: BrowserAutomationError.uncertainEffect) }
        evaluationDidSettle()
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
        for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { nil }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) { completionHandler(nil) }

    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
        initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
        decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        showDialog(message, confirm: false, frame: frame) { _ in completionHandler() }
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        showDialog(message, confirm: true, frame: frame, completion: completionHandler)
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?, initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping (String?) -> Void) { completionHandler(nil) }

    private func showDialog(_ message: String, confirm: Bool, frame: WKFrameInfo,
                            completion: @escaping (Bool) -> Void) {
        guard frame.isMainFrame, alert == nil else { completion(false); return }
        let dialog = NSAlert()
        dialog.messageText = "La página pide una decisión"
        dialog.informativeText = String(message.prefix(512))
        dialog.addButton(withTitle: confirm ? "Permitir" : "Entendido")
        if confirm { dialog.addButton(withTitle: "No permitir") }
        alert = dialog
        dialog.beginSheetModal(for: window) { [weak self] response in
            self?.alert = nil; completion(response == .alertFirstButtonReturn)
        }
    }
}
