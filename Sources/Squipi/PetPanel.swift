import AppKit
import WebKit

/// The pet lives in a borderless, transparent, always-on-top panel that never takes focus.
/// Left-drag moves it, double-click starts a conversation, right-click opens settings, and dropping a data file
/// or a document on it starts an analysis.
final class PetWebView: WKWebView {
    var onRightClick: (() -> Void)?
    var onGrab: (() -> Void)?
    var onDrop: (([URL]) -> Void)?
    var onDragHover: ((Bool) -> Void)?
    static let dataExtensions: Set<String> = ["csv", "tsv", "txt", "xlsx", "xlsm", "xls", "parquet", "pq", "pdf", "docx", "pptx", "md"]

    override var acceptsFirstResponder: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    // Mouse clicks are handled in PetPanel.sendEvent: WKWebView routes them to an internal view, so overriding
    // mouseDown/rightMouseDown here is not reliable (the panel moved only because it is movable by background).

    // MARK: file drops (the panel handles them too; whichever WebKit lets through first wins)
    private func fileURLs(in info: NSDraggingInfo) -> [URL] { FileDrop.files(in: info, site: "pet view") }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        let ok = !fileURLs(in: sender).isEmpty
        NSLog("DROP pet web view entered: %@", ok ? "accept" : "refuse")
        onDragHover?(ok)
        return ok ? .copy : []
    }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        fileURLs(in: sender).isEmpty ? [] : .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { onDragHover?(false) }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { !fileURLs(in: sender).isEmpty }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = fileURLs(in: sender)
        onDragHover?(false)
        guard !urls.isEmpty else { return false }
        NSLog("DROP pet view: %@", urls.map { $0.lastPathComponent }.joined(separator: ", "))
        onDrop?(urls)
        return true
    }
}

final class PetPanel: NSPanel, WKNavigationDelegate, NSDraggingDestination {
    static let size = NSSize(width: 320, height: 320)
    let webView: PetWebView
    var onRightClick: (() -> Void)? { didSet { webView.onRightClick = onRightClick } }
    var onGrab: (() -> Void)? { didSet { webView.onGrab = onGrab } }
    var onDrop: (([URL]) -> Void)? { didSet { webView.onDrop = onDrop } }
    var onDragHover: ((Bool) -> Void)? { didSet { webView.onDragHover = onDragHover } }
    var onDoubleClick: (() -> Void)?
    private var saveWork: DispatchWorkItem?
    var onLoaded: (() -> Void)?
    private var loaded = false
    private var pendingJS: [String] = []

    init() {
        let cfg = WKWebViewConfiguration()
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        cfg.mediaTypesRequiringUserActionForPlayback = []
        webView = PetWebView(frame: NSRect(origin: .zero, size: Self.size), configuration: cfg)
        super.init(contentRect: NSRect(origin: .zero, size: Self.size),
                   styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        isMovableByWindowBackground = true
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        animationBehavior = .none

        webView.setValue(false, forKey: "drawsBackground")
        webView.underPageBackgroundColor = .clear
        webView.navigationDelegate = self
        registerForDraggedTypes([.fileURL])
        let container = NSView(frame: NSRect(origin: .zero, size: Self.size))
        webView.frame = container.bounds
        webView.autoresizingMask = [.width, .height]
        container.addSubview(webView)
        let overlay = DropOverlay(site: "pet")
        overlay.frame = container.bounds
        overlay.onDrop = { [weak self] urls in self?.onDrop?(urls) }
        overlay.onHover = { [weak self] over in self?.onDragHover?(over) }
        container.addSubview(overlay, positioned: .above, relativeTo: webView)
        contentView = container
        webView.stripDragRegistrations(log: "pet at init")
        // WebKit's web process comes up later and registers drag types on views it creates then; keep stripping
        Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.webView.stripDragRegistrations(log: "pet (periodic)") }
        }
        NSLog("DRAG pet window registered for file drops")

        if let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "web") {
            webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
        } else {
            NSLog("web/index.html missing from bundle")
        }
        restorePosition()
        NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: self, queue: .main) { [weak self] _ in
            guard let self else { return }
            self.saveWork?.cancel()
            let w = DispatchWorkItem { UserDefaults.standard.set(NSStringFromPoint(self.frame.origin), forKey: "petOrigin") }
            self.saveWork = w
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: w)
        }
    }

    func show() { orderFrontRegardless() }

    // window-level drop, for the case where WebKit's internal view takes the drag before PetWebView sees it
    func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        let ok = !FileDrop.files(in: sender, site: "pet window").isEmpty
        onDragHover?(ok)
        return ok ? .copy : []
    }
    func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { FileDrop.files(in: sender, site: "pet window").isEmpty ? [] : .copy }
    func draggingExited(_ sender: NSDraggingInfo?) { onDragHover?(false) }
    func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { !FileDrop.files(in: sender, site: "pet window").isEmpty }
    func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = FileDrop.files(in: sender, site: "pet window")
        onDragHover?(false)
        guard !urls.isEmpty else { return false }
        NSLog("DROP pet window: %@", urls.map { $0.lastPathComponent }.joined(separator: ", "))
        onDrop?(urls)
        return true
    }

    /// Every event for this window passes through here before WebKit sees it, so clicks are reliable whatever
    /// WKWebView does internally. Right-click → settings; left-click → grab (boop) and let the window drag.
    override func sendEvent(_ event: NSEvent) {
        switch event.type {
        case .rightMouseDown:
            NSLog("PET right-click")
            onRightClick?()
            return                                   // no WebKit context menu
        case .leftMouseDown:
            if event.clickCount == 2 { onDoubleClick?(); return }        // double-click: talk to her (no hotkey needed)
            onGrab?()
            webView.evaluateJavaScript("pet.boop && pet.boop()", completionHandler: nil)
            if event.clickCount == 1 { performDrag(with: event); return }
        default: break
        }
        super.sendEvent(event)
    }

    /// Queue JS until the page has loaded, then run on main.
    func js(_ script: String) {
        DispatchQueue.main.async {
            if self.loaded { self.webView.evaluateJavaScript(script, completionHandler: nil) }
            else { self.pendingJS.append(script) }
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        // the window is the drop target; WebKit's own registrations (on the web view and its internal views) would win otherwise
        webView.stripDragRegistrations(log: "pet after load")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { webView.stripDragRegistrations() }
        loaded = true
        pendingJS.forEach { webView.evaluateJavaScript($0, completionHandler: nil) }
        pendingJS.removeAll()
        onLoaded?()
    }

    private func restorePosition() {
        if let s = UserDefaults.standard.string(forKey: "petOrigin") {
            setFrameOrigin(NSPointFromString(s))
        } else if let screen = NSScreen.main {
            let f = screen.visibleFrame
            setFrameOrigin(NSPoint(x: f.maxX - Self.size.width - 24, y: f.minY + 24))
        }
    }
}
