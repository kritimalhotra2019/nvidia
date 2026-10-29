import AppKit
import WebKit
import SwiftUI

/// Shared drop logic: which dragged files Squipi can take, with a log line for the ones she cannot.
enum FileDrop {
    static func files(in info: NSDraggingInfo, site: String) -> [URL] {
        let urls = info.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        let ok = urls.filter { PetWebView.dataExtensions.contains($0.pathExtension.lowercased()) }
        if ok.isEmpty {
            let types = (info.draggingPasteboard.types ?? []).prefix(4).map { $0.rawValue }.joined(separator: ",")
            let names = urls.map { $0.lastPathComponent }.joined(separator: ", ")
            NSLog("DROP %@: nothing usable (types %@, files %@)", site, types, names)
        }
        return ok
    }
}

/// A transparent view on top of a web view that takes file drags (WebKit's internal views otherwise swallow them).
/// It is only hit-testable while a file drag is in flight, so clicks still reach the web view.
final class DropOverlay: NSView {
    var onDrop: (([URL]) -> Void)?
    var onHover: ((Bool) -> Void)?
    let site: String
    init(site: String) { self.site = site; super.init(frame: .zero); registerForDraggedTypes([.fileURL]); autoresizingMask = [.width, .height] }
    required init?(coder: NSCoder) { nil }
    override func hitTest(_ point: NSPoint) -> NSView? {
        let dragging = NSEvent.pressedMouseButtons != 0 && (NSPasteboard(name: .drag).types ?? []).contains(.fileURL)
        return dragging ? super.hitTest(point) : nil
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        let ok = !FileDrop.files(in: sender, site: site).isEmpty
        NSLog("DROP %@ overlay entered: %@", site, ok ? "accept" : "refuse")
        onHover?(ok)
        return ok ? .copy : []
    }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { FileDrop.files(in: sender, site: site).isEmpty ? [] : .copy }
    override func draggingExited(_ sender: NSDraggingInfo?) { onHover?(false) }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { !FileDrop.files(in: sender, site: site).isEmpty }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = FileDrop.files(in: sender, site: site)
        onHover?(false)
        guard !urls.isEmpty else { return false }
        NSLog("DROP %@ overlay: %@", site, urls.map { $0.lastPathComponent }.joined(separator: ", "))
        onDrop?(urls)
        return true
    }
}

/// The workspace window itself is the drop target (WebKit's internal views swallow events that reach the web view).
final class WorkspacePanel: NSPanel, NSDraggingDestination {
    var onDrop: (([URL]) -> Void)?
    func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        let ok = !FileDrop.files(in: sender, site: "workspace").isEmpty
        NSLog("DROP workspace entered: %@", ok ? "accept" : "refuse")
        return ok ? .copy : []
    }
    func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { FileDrop.files(in: sender, site: "workspace").isEmpty ? [] : .copy }
    func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { !FileDrop.files(in: sender, site: "workspace").isEmpty }
    func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = FileDrop.files(in: sender, site: "workspace")
        guard !urls.isEmpty else { return false }
        NSLog("DROP workspace: %@", urls.map { $0.lastPathComponent }.joined(separator: ", "))
        onDrop?(urls)
        return true
    }
}

/// A web view that never claims file drags, so they fall through to the overlay / window.
final class WorkspaceWebView: WKWebView {
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { NSLog("DROP workspace web view asked (declining)"); return [] }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { [] }
}

extension NSView {
    /// WebKit registers drag types on the WKWebView *and* on internal subviews it creates after loading; a registered
    /// view under the pointer is asked first and the window never is. Strip them all so the window handles file drops.
    func stripDragRegistrations(log: String? = nil) {
        unregisterDraggedTypes()
        var names: [String] = []
        func walk(_ v: NSView, depth: Int) {
            for sub in v.subviews {
                if !sub.registeredDraggedTypes.isEmpty { names.append("\(type(of: sub))[\(sub.registeredDraggedTypes.count)]") }
                sub.unregisterDraggedTypes()
                if depth < 6 { walk(sub, depth: depth + 1) }
            }
        }
        walk(self, depth: 0)
        if let log, !names.isEmpty { NSLog("DRAG %@: stripped registrations from %@", log, names.joined(separator: ", ")) }
    }
}

/// Files stay pending in the shared composer until the user sends an instruction.
private final class WorkspaceDropState: ObservableObject {
    @Published var hovering = false
}

private struct WorkspaceResearchHome: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @ObservedObject var dropState: WorkspaceDropState

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .center, spacing: 14) {
                Image(systemName: dropState.hovering ? "tray.and.arrow.down.fill" : "doc.badge.plus")
                    .font(.system(size: 27, weight: .medium)).foregroundStyle(Color.petAccent)
                    .frame(width: 52, height: 52)
                    .background(RoundedRectangle(cornerRadius: 14).fill(Color.petAccentSoft.opacity(0.55)))
                VStack(alignment: .leading, spacing: 5) {
                    Text(dropState.hovering ? "Drop to attach" : "Your research starts with an instruction")
                        .font(.system(size: 17, weight: .bold, design: .rounded)).foregroundStyle(Color.petInk)
                    Text("Drop files anywhere here, or choose Attach files. Then tell Squipi what to do.")
                        .font(.system(size: 12, design: .rounded)).foregroundStyle(Color.petMuted)
                    Text("Nothing runs until you send an instruction.")
                        .font(.system(size: 11, weight: .semibold, design: .rounded)).foregroundStyle(Color.petAccent)
                }
                Spacer(minLength: 8)
                Button("Attach files") { Conversation.shared.pickFiles(showChat: false) }
                    .buttonStyle(Pill(filled: true))
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(dropState.hovering ? Color.petAccentSoft.opacity(0.4) : Color.clear)
            Divider().overlay(Color.petAccentSoft)
            ChatView(inWorkspace: true)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.petBG)
        .tint(Color.petAccent)
        .preferredColorScheme(.light)
        .overlay {
            if dropState.hovering {
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.petAccent, style: StrokeStyle(lineWidth: 3, dash: [8, 5]))
                    .padding(5).allowsHitTesting(false)
            }
        }
    }
}

/// A shared research composer with a separate, explicit result viewer.
/// Opening the workspace or dropping a file always returns to the composer; only Send starts work.
@MainActor
final class WorkspaceController: NSObject, WKNavigationDelegate {
    private let window: WorkspacePanel
    private let webView: WorkspaceWebView
    private let researchView: NSHostingView<WorkspaceResearchHome>
    private let resultView = NSView()
    private let dropState: WorkspaceDropState
    private let navigation = NSSegmentedControl(labels: ["Research", "Result"], trackingMode: .selectOne, target: nil, action: nil)
    private let titleLabel = NSTextField(labelWithString: "")
    private let subtitleLabel = NSTextField(labelWithString: "")
    private let openButton = NSButton(title: "Open in Browser", target: nil, action: nil)
    private let revealButton = NSButton(title: "Reveal in Finder", target: nil, action: nil)
    private(set) var currentFile: URL?

    override init() {
        window = WorkspacePanel(contentRect: NSRect(x: 0, y: 0, width: 920, height: 780),
                                styleMask: [.titled, .closable, .resizable, .miniaturizable, .fullSizeContentView],
                                backing: .buffered, defer: false)
        let cfg = WKWebViewConfiguration()
        cfg.defaultWebpagePreferences.allowsContentJavaScript = false
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")
        webView = WorkspaceWebView(frame: .zero, configuration: cfg)
        let state = WorkspaceDropState()
        dropState = state
        researchView = NSHostingView(rootView: WorkspaceResearchHome(dropState: state))
        super.init()
        webView.stripDragRegistrations(log: "workspace")
        window.registerForDraggedTypes([.fileURL])
        Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.webView.stripDragRegistrations(log: "workspace (periodic)") }
        }
        window.onDrop = { [weak self] urls in self?.stage(files: urls) }
        window.title = "Squipi research workspace"
        window.appearance = NSAppearance(named: .aqua)
        window.backgroundColor = NSColor(Color.petBG)
        NotificationCenter.default.addObserver(forName: .squipiAppearanceChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.window.backgroundColor = NSColor(Color.petBG)
                self.navigation.selectedSegmentBezelColor = NSColor(Color.petAccent)
                self.openButton.contentTintColor = NSColor(Color.petAccent)
                self.revealButton.contentTintColor = NSColor(Color.petAccent)
            }
        }
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.isReleasedWhenClosed = false
        window.hidesOnDeactivate = false
        window.level = .normal
        window.collectionBehavior = [.moveToActiveSpace, .fullScreenAuxiliary]
        window.minSize = NSSize(width: 620, height: 540)
        window.setFrameAutosaveName("squipi.research-workspace")
        window.isMovableByWindowBackground = false
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        let heading = NSTextField(labelWithString: "Squipi · Research workspace")
        heading.font = .systemFont(ofSize: 13, weight: .semibold)
        heading.textColor = NSColor(Color.petInk)
        heading.setContentHuggingPriority(.defaultLow, for: .horizontal)
        navigation.target = self
        navigation.action = #selector(changePane)
        navigation.selectedSegment = 0
        navigation.setEnabled(false, forSegment: 1)
        navigation.setAccessibilityLabel("Workspace view")
        navigation.selectedSegmentBezelColor = NSColor(Color.petAccent)
        let toolbar = NSStackView(views: [heading, NSView(), navigation])
        toolbar.orientation = .horizontal
        toolbar.spacing = 10
        toolbar.edgeInsets = NSEdgeInsets(top: 10, left: 78, bottom: 10, right: 16)

        titleLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        titleLabel.textColor = NSColor(Color.petInk)
        titleLabel.lineBreakMode = .byTruncatingMiddle
        subtitleLabel.font = .systemFont(ofSize: 11)
        subtitleLabel.textColor = NSColor(Color.petMuted)
        subtitleLabel.lineBreakMode = .byTruncatingMiddle
        let titles = NSStackView(views: [titleLabel, subtitleLabel])
        titles.orientation = .vertical
        titles.alignment = .leading
        titles.spacing = 2
        titles.setContentHuggingPriority(.defaultLow, for: .horizontal)
        for button in [openButton, revealButton] {
            button.bezelStyle = .rounded
            button.contentTintColor = NSColor(Color.petAccent)
            button.controlSize = .small
            button.target = self
        }
        openButton.action = #selector(openInBrowser)
        revealButton.action = #selector(revealInFinder)
        let resultBar = NSStackView(views: [titles, NSView(), openButton, revealButton])
        resultBar.orientation = .horizontal
        resultBar.spacing = 10
        resultBar.edgeInsets = NSEdgeInsets(top: 10, left: 20, bottom: 10, right: 16)
        resultBar.translatesAutoresizingMaskIntoConstraints = false
        webView.translatesAutoresizingMaskIntoConstraints = false
        resultView.addSubview(resultBar)
        resultView.addSubview(webView)
        NSLayoutConstraint.activate([
            resultBar.leadingAnchor.constraint(equalTo: resultView.leadingAnchor),
            resultBar.trailingAnchor.constraint(equalTo: resultView.trailingAnchor),
            resultBar.topAnchor.constraint(equalTo: resultView.topAnchor),
            resultBar.heightAnchor.constraint(equalToConstant: 58),
            webView.leadingAnchor.constraint(equalTo: resultView.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: resultView.trailingAnchor),
            webView.topAnchor.constraint(equalTo: resultBar.bottomAnchor),
            webView.bottomAnchor.constraint(equalTo: resultView.bottomAnchor),
        ])

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 920, height: 780))
        let body = NSView()
        for view in [toolbar, body] {
            view.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(view)
        }
        for view in [researchView as NSView, resultView] {
            view.translatesAutoresizingMaskIntoConstraints = false
            body.addSubview(view)
            NSLayoutConstraint.activate([
                view.leadingAnchor.constraint(equalTo: body.leadingAnchor), view.trailingAnchor.constraint(equalTo: body.trailingAnchor),
                view.topAnchor.constraint(equalTo: body.topAnchor), view.bottomAnchor.constraint(equalTo: body.bottomAnchor),
            ])
        }
        let overlay = DropOverlay(site: "workspace")
        overlay.translatesAutoresizingMaskIntoConstraints = false
        overlay.onDrop = { [weak self] urls in self?.stage(files: urls) }
        overlay.onHover = { [weak self] hovering in self?.dropState.hovering = hovering }
        container.addSubview(overlay, positioned: .above, relativeTo: body)
        window.contentView = container
        NSLayoutConstraint.activate([
            toolbar.leadingAnchor.constraint(equalTo: container.leadingAnchor), toolbar.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            toolbar.topAnchor.constraint(equalTo: container.topAnchor), toolbar.heightAnchor.constraint(equalToConstant: 48),
            body.leadingAnchor.constraint(equalTo: container.leadingAnchor), body.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            body.topAnchor.constraint(equalTo: toolbar.bottomAnchor), body.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            overlay.leadingAnchor.constraint(equalTo: container.leadingAnchor), overlay.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            overlay.topAnchor.constraint(equalTo: container.topAnchor), overlay.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        selectResearch()
    }

    var isVisible: Bool { window.isVisible }

    private func stage(files: [URL]) {
        Conversation.shared.attach(files, showChat: false)
        dropState.hovering = false
        selectResearch()
        bringToFront()
    }

    private func selectResearch() {
        navigation.selectedSegment = 0
        researchView.isHidden = false
        resultView.isHidden = true
    }

    private func selectResult() {
        guard currentFile != nil else { selectResearch(); return }
        navigation.selectedSegment = 1
        researchView.isHidden = true
        resultView.isHidden = false
    }

    @objc private func changePane() {
        if navigation.selectedSegment == 1 { selectResult() } else { selectResearch() }
    }

    /// Shows an explicitly opened local HTML file (or SVG) from the worker's project directory.
    func show(file: URL, title: String, subtitle: String) {
        currentFile = file
        titleLabel.stringValue = title
        subtitleLabel.stringValue = subtitle
        navigation.setEnabled(true, forSegment: 1)
        webView.loadFileURL(file, allowingReadAccessTo: Worker.shared.projectDir)
        selectResult()
        bringToFront()
    }

    func present() {
        selectResearch()
        bringToFront()
    }

    private func bringToFront() {
        let screen = NSScreen.main ?? NSScreen.screens.first
        if !window.isVisible, let screen {
            let f = screen.visibleFrame
            let w = min(920, f.width - 60), h = min(780, f.height - 60)
            if window.frameAutosaveName.isEmpty || !window.setFrameUsingName("squipi.research-workspace") {
                window.setFrame(NSRect(x: f.minX + 30, y: f.maxY - h - 30, width: w, height: h), display: true)
            }
        }
        // A frame saved on another display can be entirely off-screen.
        if let screen, !NSScreen.screens.contains(where: { $0.visibleFrame.intersects(window.frame.insetBy(dx: 40, dy: 40)) }) {
            let f = screen.visibleFrame
            let w = min(920, f.width - 60), h = min(780, f.height - 60)
            window.setFrame(NSRect(x: f.minX + 30, y: f.maxY - h - 30, width: w, height: h), display: true)
        }
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
        NSLog("WORKSPACE present %@ visible=%d pane=%@", NSStringFromRect(window.frame), window.isVisible ? 1 : 0,
              researchView.isHidden ? "result" : "research")
    }

    func hide() { window.orderOut(nil) }

    func snapshot(_ done: @escaping (NSImage?) -> Void) {
        if researchView.isHidden {
            webView.takeSnapshot(with: WKSnapshotConfiguration()) { img, _ in done(img) }
            return
        }
        guard let view = window.contentView, let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { done(nil); return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        let image = NSImage(size: view.bounds.size)
        image.addRepresentation(bitmap)
        done(image)
    }

    @objc private func openInBrowser() {
        guard let f = currentFile else { return }
        NSWorkspace.shared.open(f)
    }

    @objc private func revealInFinder() {
        guard let f = currentFile else { return }
        NSWorkspace.shared.activateFileViewerSelecting([f])
    }

    // Links inside a report open in the same view; external links open in the browser.
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        webView.stripDragRegistrations(log: "workspace after load")
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { webView.stripDragRegistrations() }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.isFileURL || url.absoluteString == "about:blank" { decisionHandler(.allow); return }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }
}
