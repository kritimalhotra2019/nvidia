import AppKit
import WebKit
import SwiftUI

@MainActor
private final class BreakPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { close() }
}

@MainActor
final class BreakGame: NSObject, NSWindowDelegate, WKScriptMessageHandler {
    static let shared = BreakGame()
    private var window: NSWindow?
    func show() {
        if let window { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        guard let source = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "web") else { return }
        guard let screen = NSScreen.main else { return }
        let frame = screen.frame
        let size = frame.size
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "gameControl")
        let web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: config)
        web.setValue(false, forKey: "drawsBackground")
        web.underPageBackgroundColor = .clear
        var parts = URLComponents(url: source, resolvingAgainstBaseURL: false)!
        let defaults = UserDefaults.standard
        parts.queryItems = ["w":String(Int(size.width)), "h":String(Int(size.height)), "game":"catch", "theme":SquipiColour.current.rawValue,
            "outfit": defaults.string(forKey: "squipiOutfit") ?? "classic", "hat":defaults.string(forKey: "squipiHat") ?? "none",
            "look":defaults.string(forKey: "skirtLook") ?? "stars", "flower":defaults.bool(forKey: "finFlower") ? "1":"0",
            "reduced":defaults.bool(forKey: "reducedMotion") ? "1":"", "glasses":(defaults.object(forKey: "squipiGlasses") as? Bool ?? true) ? "1":"0"].filter { !$0.value.isEmpty }.map { URLQueryItem(name: $0.key, value: $0.value) }
        web.loadFileURL(parts.url!, allowingReadAccessTo: source.deletingLastPathComponent())
        let panel = BreakPanel(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
        panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = false; panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.title = "Play with Squipi"; panel.contentView = web; panel.isReleasedWhenClosed = false; panel.delegate = self
        panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); window = panel
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "gameControl", message.body as? String == "close" { window?.close() }
    }
    func windowWillClose(_ notification: Notification) {
        (window?.contentView as? WKWebView)?.configuration.userContentController.removeScriptMessageHandler(forName: "gameControl")
        window?.contentView = nil; window = nil
    }
}

struct PlayWithSquipiView: View {
    @AppStorage("squipiColour") private var colour = "blue"
    @State private var minutes = 20
    @State private var notice = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Schedule a wellbeing break")
            Text("Play with Squipi across your desktop for three minutes. Drag to throw; click or press Space to catch.").font(.caption).foregroundStyle(Color.petMuted)
            Button("Play with Squipi") { BreakGame.shared.show() }.buttonStyle(Pill())
            HStack {
                Stepper("Break in \(minutes) minutes", value: $minutes, in: 1...120)
                Button("Schedule") {
                    let saved = Mind.shared.addReminder("Wellbeing break — play with Squipi", due: Date().addingTimeInterval(Double(minutes)*60), breakGame: true)
                    notice = saved ? "Game break saved. Keep Squipi running so it can remind you." : (Mind.shared.persistenceError ?? "Could not save reminder.")
                }.buttonStyle(Pill(filled: false))
            }
            if !notice.isEmpty { Text(notice).font(.caption).foregroundStyle(Color.petMuted) }
        }.card()
    }
}
