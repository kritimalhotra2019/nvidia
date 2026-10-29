import AppKit

/// The menu bar item next to Wi-Fi and battery: show/hide Squipi, analyse a file, open the workspace or settings, quit.
@MainActor
final class StatusBar: NSObject, NSMenuDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private weak var app: AppDelegate?
    private let menu = NSMenu()

    init(app: AppDelegate) {
        self.app = app
        super.init()
        item.button?.image = Self.octopusGlyph()
        item.button?.image?.isTemplate = true
        item.button?.toolTip = "Squipi"
        menu.delegate = self
        item.menu = menu
        installMainMenu()
    }

    /// Dock apps need ordinary app menus too; the menu-bar extra is not the main menu.
    private func installMainMenu() {
        let main = NSMenu()
        func submenu(_ title: String) -> NSMenu {
            let entry = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let child = NSMenu(title: title)
            entry.submenu = child
            main.addItem(entry)
            return child
        }
        let appMenu = submenu("Squipi")
        appMenu.addItem(make("Settings…", #selector(openSettings), key: ","))
        appMenu.addItem(.separator())
        appMenu.addItem(make("Quit Squipi", #selector(quit), key: "q"))
        let fileMenu = submenu("File")
        fileMenu.addItem(make("Attach files…", #selector(pickFile), key: "o"))
        fileMenu.addItem(make("Open workspace", #selector(openWorkspace), key: "1"))
        fileMenu.addItem(make("Open Chat", #selector(openHub), key: "2"))
        fileMenu.addItem(NSMenuItem(title: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w"))
        let editMenu = submenu("Edit")
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        NSApp.mainMenu = main
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        guard let app else { return }
        let visible = app.panel.isVisible
        menu.addItem(make(visible ? "Hide Squipi" : "Show Squipi", #selector(toggleVisible), key: "h"))
        menu.addItem(make(TalkState.shared.listening ? "Stop listening" : "Talk to Squipi", #selector(talk), key: "t"))
        if app.voice.isSpeaking || Conversation.shared.busy { menu.addItem(make("Hush (stop talking)", #selector(hush), key: ".")) }
        menu.addItem(make("Bring Squipi here", #selector(comeHere), key: "b"))
        menu.addItem(make("Attach files…", #selector(pickFile), key: "o"))
        menu.addItem(make("Open workspace", #selector(openWorkspace), key: "w"))
        menu.addItem(make("Chat, notes & settings…", #selector(openHub), key: ","))
        menu.addItem(.separator())
        let mode = NSMenuItem(title: "Mode: \(ModelRouter.shared.displayLabel)", action: nil, keyEquivalent: "")
        mode.isEnabled = false
        menu.addItem(mode)
        menu.addItem(.separator())
        let wander = make("Wanders around", #selector(toggleWander), key: "")
        wander.state = UserDefaults.standard.bool(forKey: "wander") ? .on : .off
        menu.addItem(wander)
        let voice = make("Talks back", #selector(toggleVoice), key: "")
        voice.state = UserDefaults.standard.bool(forKey: "voiceOn") ? .on : .off
        menu.addItem(voice)
        let sounds = make("Sounds", #selector(toggleSounds), key: "")
        sounds.state = UserDefaults.standard.bool(forKey: "sounds") ? .on : .off
        menu.addItem(sounds)
        let calm = make("Reduced motion", #selector(toggleReducedMotion), key: "")
        calm.state = UserDefaults.standard.bool(forKey: "reducedMotion") ? .on : .off
        menu.addItem(calm)
        menu.addItem(.separator())
        menu.addItem(make("Quit Squipi", #selector(quit), key: "q"))
    }

    private func make(_ title: String, _ sel: Selector, key: String) -> NSMenuItem {
        let m = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        m.target = self
        return m
    }

    @objc private func toggleVisible() {
        guard let app else { return }
        if app.panel.isVisible { app.panel.orderOut(nil) } else { app.panel.show() }
    }
    @objc private func pickFile() { Conversation.shared.pickFiles() }
    @objc private func talk() { app?.toggleTalk() }
    @objc private func hush() { Conversation.shared.cancel(); app?.hush() }
    @objc private func comeHere() { app?.comeHere() }
    @objc private func openWorkspace() { app?.workspace.present() }
    @objc private func openHub() { app?.hub.show(); app?.hub.selectTab(0) }
    @objc private func openSettings() { app?.hub.show(); app?.hub.selectTab(5) }
    @objc private func toggleWander() {
        UserDefaults.standard.set(!UserDefaults.standard.bool(forKey: "wander"), forKey: "wander"); app?.applyWanderPref()
    }
    @objc private func toggleVoice() { UserDefaults.standard.set(!UserDefaults.standard.bool(forKey: "voiceOn"), forKey: "voiceOn") }
    @objc private func toggleSounds() {
        UserDefaults.standard.set(!UserDefaults.standard.bool(forKey: "sounds"), forKey: "sounds"); app?.applySoundsPref()
    }
    @objc private func toggleReducedMotion() {
        UserDefaults.standard.set(!UserDefaults.standard.bool(forKey: "reducedMotion"), forKey: "reducedMotion"); app?.applyLookPref()
    }
    @objc private func quit() { NSApp.terminate(nil) }

    /// A tiny octopus: round head with two ear-fins and a scalloped skirt, drawn as a template image.
    static func octopusGlyph() -> NSImage {
        let size = NSSize(width: 20, height: 18)
        let img = NSImage(size: size, flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(ovalIn: NSRect(x: 4, y: 6, width: 12, height: 11)).fill()          // head
            NSBezierPath(ovalIn: NSRect(x: 1, y: 11, width: 5, height: 4)).fill()           // fins
            NSBezierPath(ovalIn: NSRect(x: 14, y: 11, width: 5, height: 4)).fill()
            let skirt = NSBezierPath()                                                     // scalloped skirt
            skirt.move(to: NSPoint(x: 3, y: 7))
            skirt.line(to: NSPoint(x: 17, y: 7))
            skirt.line(to: NSPoint(x: 18, y: 3))
            for i in stride(from: 4, through: 0, by: -1) {
                let x = 3 + CGFloat(i) * 3.5
                skirt.appendArc(withCenter: NSPoint(x: x, y: 3), radius: 1.6, startAngle: 0, endAngle: 180, clockwise: true)
            }
            skirt.close()
            skirt.fill()
            NSGraphicsContext.current?.compositingOperation = .destinationOut
            NSBezierPath(ovalIn: NSRect(x: 6.5, y: 10, width: 2.6, height: 2.6)).fill()     // eyes
            NSBezierPath(ovalIn: NSRect(x: 10.9, y: 10, width: 2.6, height: 2.6)).fill()
            return true
        }
        img.isTemplate = true
        return img
    }
}
