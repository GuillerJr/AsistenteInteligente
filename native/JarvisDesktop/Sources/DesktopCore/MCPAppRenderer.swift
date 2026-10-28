import AppKit
import Foundation
import WebKit

/// A native MCP Apps host, not a browser with the owner's cookies. Untrusted
/// source runs only in an opaque-origin iframe and never receives an RPC client.
@MainActor public final class MCPAppRenderer: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    public private(set) var webView: WKWebView?
    public var onError: (@MainActor (String) -> Void)?
    private let document: MCPAppDocument
    private let isCurrent: @MainActor () -> Bool
    private let request: @MainActor (RPCValue) async throws -> RPCValue
    private let initialInput: RPCValue?
    private let initialResult: RPCValue?
    private let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    private var proxy: BrowserAutomationProxy?
    private var scheme: MCPAppSchemeHandler?
    private var epoch = 0
    private var closed = false
    private var starting = false
    private var initialized = false
    private var initializing = false
    private var seenIDs: Set<String> = []
    private var tasks: [UUID: Task<Void, Never>] = [:]
    private var hostURL: URL { URL(string: "jarvis-mcp-host://\(nonce)/host")! }
    private var sourceURL: URL { URL(string: "jarvis-mcp-resource://\(nonce)/view")! }

    public init(document: MCPAppDocument, toolInput: RPCValue? = nil, toolResult: RPCValue? = nil,
                isCurrent: @escaping @MainActor () -> Bool,
                request: @escaping @MainActor (RPCValue) async throws -> RPCValue) {
        self.document = document; initialInput = toolInput; initialResult = toolResult
        self.isCurrent = isCurrent; self.request = request
    }
    public func start() async throws {
        guard !closed, !starting, webView == nil, isCurrent() else { throw MCPAppFailure.closed }
        starting = true
        defer { starting = false }
        let generation = epoch
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        configuration.allowsAirPlayForMediaPlayback = false
        configuration.mediaTypesRequiringUserActionForPlayback = .all
        if !document.policy.connectDomains.isEmpty || !document.policy.resourceDomains.isEmpty {
            let proxy = BrowserAutomationProxy(policy: BrowserAutomationPolicy())
            self.proxy = proxy
            do { configuration.websiteDataStore.proxyConfigurations = [try await proxy.start()] }
            catch { await proxy.stop(); self.proxy = nil; throw error }
        }
        guard generation == epoch, !closed, isCurrent() else { await proxy?.stop(); proxy = nil; throw MCPAppFailure.closed }
        let source = MCPAppSchemeHandler(host: hostURL, resource: sourceURL,
            hostHTML: hostHTML, resourceHTML: resourceHTML)
        scheme = source
        configuration.setURLSchemeHandler(source, forURLScheme: "jarvis-mcp-host")
        configuration.setURLSchemeHandler(source, forURLScheme: "jarvis-mcp-resource")
        // WebRTC data channels do not use the HTTP CONNECT proxy. No such
        // capability is advertised; disable constructors before any app script.
        configuration.userContentController.addUserScript(WKUserScript(source: Self.restrictedAPIs,
            injectionTime: .atDocumentStart, forMainFrameOnly: false))
        configuration.userContentController.add(self, name: "jarvisMCPApp")
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self; view.uiDelegate = self
        view.allowsBackForwardNavigationGestures = false
        webView = view
        view.load(URLRequest(url: hostURL))
    }
    public func close() async {
        guard !closed else { return }
        closed = true; epoch += 1
        for task in tasks.values { task.cancel() }
        tasks.removeAll()
        // Revocation happens before stopping WebKit. JS that remains alive has
        // neither an admitted bridge nor a live network proxy.
        if let view = webView {
            view.configuration.userContentController.removeScriptMessageHandler(forName: "jarvisMCPApp")
            view.stopLoading(); view.navigationDelegate = nil; view.uiDelegate = nil
            view.removeFromSuperview()
        }
        webView = nil; scheme = nil
        await proxy?.stop(); proxy = nil
    }
    public func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard !closed, isCurrent(), message.webView === webView, message.frameInfo.isMainFrame,
              message.frameInfo.request.url == hostURL,
              let envelope = message.body as? [String: Any], envelope["channel"] as? String == nonce,
              let raw = envelope["message"], JSONSerialization.isValidJSONObject(raw),
              let bytes = try? JSONSerialization.data(withJSONObject: raw), bytes.count <= 65536,
              let value = try? JSONDecoder().decode(RPCValue.self, from: bytes),
              let parsed = try? MCPAppBridgeMessage(value) else { return }
        guard tasks.count < 4 else { return }
        let id = UUID(), generation = epoch
        tasks[id] = Task { [weak self] in
            guard let self else { return }
            defer { tasks.removeValue(forKey: id) }
            await handle(value, parsed: parsed, generation: generation)
        }
    }
    private func handle(_ raw: RPCValue, parsed: MCPAppBridgeMessage, generation: Int) async {
        guard !closed, generation == epoch, isCurrent() else { return }
        if let id = parsed.id {
            let key = ManagementValidation.json(id)
            guard seenIDs.count < 512, seenIDs.insert(key).inserted else { return }
        }
        do {
            switch parsed.method {
            case "ui/initialize":
                guard !initializing, !initialized, parsed.id != nil,
                      parsed.params["protocolVersion"].string == "2026-01-26",
                      parsed.params["appInfo"].object != nil else { throw MCPAppFailure.invalidMessage }
                initializing = true
                await reply(parsed.id, result: .object(["protocolVersion": .string("2026-01-26"),
                    "hostInfo": .object(["name": .string("Jarvis"), "version": .string("1")]),
                    "hostCapabilities": .object(["serverTools": .object([:]), "serverResources": .object([:]),
                        "sandbox": .object(["csp": .object([
                            "connectDomains": .array(document.policy.connectDomains.map(RPCValue.string)),
                            "resourceDomains": .array(document.policy.resourceDomains.map(RPCValue.string))]), "permissions": .object([:])])]),
                    "hostContext": .object(["platform": .string("desktop"), "displayMode": .string("inline"),
                        "locale": .string("es"), "theme": .string("light")])]))
            case "ui/notifications/initialized":
                guard initializing, !initialized, parsed.id == nil else { throw MCPAppFailure.invalidMessage }
                initialized = true
                if let initialInput { await notify("ui/notifications/tool-input", params: initialInput) }
                if let initialResult { await notify("ui/notifications/tool-result", params: initialResult) }
            case "ping":
                guard initialized else { throw MCPAppFailure.invalidMessage }
                await reply(parsed.id, result: .object([:]))
            case "tools/call", "resources/read", "tools/list", "resources/list", "resources/templates/list":
                guard initialized, parsed.id != nil else { throw MCPAppFailure.invalidMessage }
                let result = try await request(raw)
                guard !closed, generation == epoch, isCurrent(), !Task.isCancelled else { return }
                await reply(parsed.id, result: result)
            case "ui/notifications/size-changed":
                // The native panel owns its dimensions. A content size request
                // cannot resize windows or escape the declared viewport.
                guard initialized, parsed.id == nil else { throw MCPAppFailure.invalidMessage }
            default: throw MCPAppFailure.unsupported
            }
        } catch {
            guard !closed, generation == epoch else { return }
            await reply(parsed.id, error: error is MCPAppFailure ? -32601 : -32000)
        }
    }
    private func reply(_ id: RPCValue?, result: RPCValue? = nil, error: Int? = nil) async {
        guard let id else { return }
        var value: [String: RPCValue] = ["jsonrpc": .string("2.0"), "id": id]
        if let error { value["error"] = .object(["code": .number(Double(error)), "message": .string("Solicitud no disponible o sin confirmar")]) }
        else { value["result"] = result ?? .object([:]) }
        await deliver(.object(value))
    }
    private func notify(_ method: String, params: RPCValue) async {
        await deliver(.object(["jsonrpc": .string("2.0"), "method": .string(method), "params": params]))
    }
    private func deliver(_ value: RPCValue) async {
        guard !closed, isCurrent(), let view = webView,
              let data = try? JSONEncoder().encode(value), data.count <= 524288,
              let object = try? JSONSerialization.jsonObject(with: data) else { return }
        do { _ = try await view.callAsyncJavaScript("window.jarvisMCPDeliver(message);", arguments: ["message": object], in: nil, contentWorld: .page) }
        catch { if !closed { onError?("La App MCP perdió la comunicación. No se reenviarán solicitudes.") } }
    }
    public func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard !closed, isCurrent(), let url = action.request.url else { decisionHandler(.cancel); return }
        // No external top-level navigation, file URL, popup, form submission or
        // arbitrary child origin. Resource loads are separately bounded by CSP.
        let expected = action.targetFrame?.isMainFrame == true ? hostURL : sourceURL
        decisionHandler(url == expected && action.navigationType == .other ? .allow : .cancel)
    }
    public func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(!closed && isCurrent() && response.canShowMIMEType ? .allow : .cancel)
    }
    public func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        onError?("El proceso de la App MCP terminó. Ninguna solicitud se repetirá automáticamente.")
        Task { await close() }
    }
    public func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                        for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? { nil }
    public func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                        completionHandler: @escaping () -> Void) { completionHandler() }
    public func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo,
                        completionHandler: @escaping (Bool) -> Void) { completionHandler(false) }
    public func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) { completionHandler(nil) }
    public func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo,
                        completionHandler: @escaping ([URL]?) -> Void) { completionHandler(nil) }
    public func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin, initiatedByFrame frame: WKFrameInfo,
                        type: WKMediaCaptureType, decisionHandler: @escaping (WKPermissionDecision) -> Void) { decisionHandler(.deny) }

    private var resourceHTML: String {
        "<!doctype html><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\""
            + document.policy.contentSecurityPolicy + "\">" + document.html
    }
    private var hostHTML: String {
        """
        <!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-src jarvis-mcp-resource:; base-uri 'none'; object-src 'none'">
        <style>html,body,iframe{margin:0;width:100%;height:100%;border:0}iframe{display:block}</style>
        <iframe id="app" sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
        <script>
        (()=>{'use strict';const frame=document.getElementById('app');
        window.addEventListener('message',event=>{if(event.source!==frame.contentWindow||event.origin!=='null')return;
        const message=event.data;if(!message||typeof message!=='object'||message.jsonrpc!=='2.0')return;
        try{if(JSON.stringify(message).length>65536)return;window.webkit.messageHandlers.jarvisMCPApp.postMessage({channel:'\(nonce)',message});}catch{}});
        Object.defineProperty(window,'jarvisMCPDeliver',{value:message=>frame.contentWindow.postMessage(message,'*'),writable:false,configurable:false});
        frame.src='\(sourceURL.absoluteString)';})();
        </script>
        """
    }
    private static let restrictedAPIs = """
    (()=>{'use strict';for(const key of ['RTCPeerConnection','webkitRTCPeerConnection','RTCDataChannel']){
      try{Object.defineProperty(window,key,{value:undefined,writable:false,configurable:false});}catch{}}
    })();
    """
}

private final class MCPAppSchemeHandler: NSObject, WKURLSchemeHandler {
    let host: URL, resource: URL
    let hostHTML: String, resourceHTML: String
    init(host: URL, resource: URL, hostHTML: String, resourceHTML: String) {
        self.host = host; self.resource = resource; self.hostHTML = hostHTML; self.resourceHTML = resourceHTML
    }
    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url, url == host || url == resource,
              task.request.httpMethod == nil || task.request.httpMethod == "GET" else { task.didFailWithError(URLError(.unsupportedURL)); return }
        let bytes = Data((url == host ? hostHTML : resourceHTML).utf8)
        task.didReceive(URLResponse(url: url, mimeType: "text/html", expectedContentLength: bytes.count, textEncodingName: "utf-8"))
        task.didReceive(bytes); task.didFinish()
    }
    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}
