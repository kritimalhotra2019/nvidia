import AppKit
import AVFoundation
import WebKit

/// Whether Squipi is listening right now (for the mic button and the menu bar).
@MainActor
final class TalkState: ObservableObject {
    static let shared = TalkState()
    @Published var listening = false
    @Published var speaking = false
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let panel = PetPanel()
    let recorder = AudioRecorder()
    let hotkey = HotkeyMonitor()
    let paster = Paster()
    lazy var meeting = MeetingRecorder(mic: recorder)
    lazy var hub = HubController(app: self)
    lazy var wander = Wander(panel: panel)
    lazy var workspace = WorkspaceController()
    let voice = PetVoice()
    let brain = Brain()
    lazy var statusBar = StatusBar(app: self)
    let analysis = Analysis.shared
    private var talking = false
    private(set) var engine: Transcriber = AppleTranscriber()
    private var levelTimer: Timer?
    private var listening = false
    private var engineLoading = false
    private var pendingSpeechLanguage = false
    private var hasWoken = false
    private var demoTimer: Timer?
    // talk mode started by double-click / mic button: stops itself after a pause in speech
    private var talkAuto = false
    private var talkStart = Date()
    private var lastLoud = Date()
    private var heardSpeech = false
    private var noiseFloor: Float = 0.02
    private var readyForOpenFiles = false
    private var pendingOpenFiles: [URL] = []

    func applicationDidFinishLaunching(_ note: Notification) {
        MemoryPressure.shared.start()
        do { try UserInstructions.shared.installDefaultPersonalityIfMissing() }
        catch { NSLog("PERSONALITY could not create default file: %@", error.localizedDescription) }
        if !CommandLine.arguments.contains("--mode") {
            SupportedResearchSetup.apply(to: .standard)
        }
        UserDefaults.standard.set(SquipiLanguage.current.rawValue, forKey: "squipiLanguage")
        panel.show()
        _ = statusBar
        analysis.app = self
        panel.onRightClick = { [weak self] in self?.panel.show(); self?.hub.toggle() }
        panel.onDoubleClick = { [weak self] in self?.toggleTalk() }
        panel.onLoaded = { [weak self] in
            self?.panel.js("pet.setState('loading')")
            self?.applySoundsPref()
            self?.applyLookPref()
            self?.panel.webView.evaluateJavaScript("pet.name || 'squipi'") { v, _ in if let n = v as? String { self?.brain.petName = n } }
        }
        panel.onDrop = { urls in Conversation.shared.attach(urls) }
        panel.onDragHover = { [weak self] over in
            guard let self, !self.analysis.busy else { return }
            if over { self.panel.js("pet.setProps({glasses:true})") } else { self.applyLookPref() }
        }
        Task { await brain.prepare() }
        // she should know the project's datasets and documents from the start, not only after the next drop
        if Worker.shared.isInstalled { Task { await analysis.refreshInventory() } }
        if ModelRouter.shared.mode == .mlx, MLXConfiguration.installed { Task { try? await MLXRuntime.shared.ensureRunning() } }
        Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                do {
                    let due = try Mind.shared.claimDue()
                    guard !due.isEmpty else { return }
                    self.panel.js("pet.setState('success')")
                    let text = due.map(\.text).joined(separator: ". Next reminder: ")
                    self.voice.say("You asked me to remind you: \(text).", force: true)
                    Conversation.shared.messages.append(Conversation.Message(role: "system", text: "Reminders due:\n" + due.map { "• " + $0.text }.joined(separator: "\n"), via: "Reminders"))
                } catch { NSLog("REMINDERS could not persist due batch: %@", error.localizedDescription) }
            }
        }
        UserDefaults.standard.register(defaults: ["sounds": true, "wander": false, "voiceOn": true, "voiceReadBack": false, "voiceName": PetVoice.defaultVoice,
                                                  "brainOn": true, "chattiness": "some", "brainModel": Brain.defaultTier,
                                                  "skirtLook": "stars", "finFlower": false, "reducedMotion": false,
                                                  "nebiusBaseURL": NebiusProvider.defaultBaseURL, "nebiusModel": NebiusProvider.defaultModel])
        Permissions.requestMicrophone()
        Permissions.promptAccessibilityIfNeeded()
        hotkey.onPress = { [weak self] in self?.startListening() }
        hotkey.onRelease = { [weak self] in self?.stopListening() }
        hotkey.onTalkPress = { [weak self] in self?.startTalk() }
        hotkey.onTalkRelease = { [weak self] in self?.stopTalk() }
        hotkey.start()
        CursorTracker.shared.onMove = { [weak self] nx, ny in self?.panel.js("pet.lookAt(\(nx),\(ny))") }
        CursorTracker.shared.start(panel: panel)
        meeting.onStateChange = { [weak self] s in
            self?.panel.js("pet.setState('\(s)')")
            if s == "noting" { self?.voice.notesStarted() }
            if s == "done", let n = Store.shared.notes.first { DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self?.voice.notesReady(title: n.title) } }
        }
        meeting.engineProvider = { [weak self] in self?.engine ?? AppleTranscriber() }
        meeting.dictationActive = { [weak self] in self?.listening ?? false }
        selectEngine(UserDefaults.standard.string(forKey: "engine") ?? "apple")
        wander.enabled = UserDefaults.standard.bool(forKey: "wander")
        wander.isBusy = { [weak self] in
            guard let self else { return true }
            return self.listening || self.meeting.isRecording || self.meeting.isProcessing || self.hub.isVisible || self.engineLoading || self.analysis.busy
        }
        wander.onVelocity = { [weak self] x, y in self?.panel.js("pet.setVelocity && pet.setVelocity(\(x),\(y))") }
        panel.onGrab = { [weak self] in
            guard let self else { return }
            self.wander.pause(seconds: 30)
            if self.voice.isSpeaking { self.voice.stop(); self.panel.js("pet.setTalking && pet.setTalking(false)"); return }   // a click hushes her
            self.voice.grabbed()
        }
        voice.onTalking = { [weak self] on in self?.panel.js("pet.setTalking && pet.setTalking(\(on))"); TalkState.shared.speaking = on }
        if CommandLine.arguments.contains("--demo") { runDemo() }
        if CommandLine.arguments.contains("--hub") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.hub.show() } }
        if CommandLine.arguments.contains("--workspace") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { self.workspace.present() } }
        if let i = CommandLine.arguments.firstIndex(of: "--mode"), i + 1 < CommandLine.arguments.count, let m = ModelRouter.Mode(rawValue: CommandLine.arguments[i + 1]) {
            ModelRouter.transient = true          // test runs: pick the brain from the command line without saving it
            ModelRouter.shared.mode = m
        }
        if let i = CommandLine.arguments.firstIndex(of: "--ask"), i + 1 < CommandLine.arguments.count {
            // end-to-end UI test: type this into the chat, wait for the answer, then screenshot the Chat tab
            let text = CommandLine.arguments[i + 1]
            let dirArg = CommandLine.arguments.firstIndex(of: "--shots").flatMap { $0 + 1 < CommandLine.arguments.count ? CommandLine.arguments[$0 + 1] : nil }
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) {
                self.hub.show(); self.hub.selectTab(0)
                Conversation.shared.send(text)
                var ticks = 0
                Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { t in
                    MainActor.assumeIsolated {
                        ticks += 1
                        if !Conversation.shared.busy || ticks > 300 {
                            t.invalidate()
                            let last = String(Conversation.shared.messages.last?.text.prefix(200) ?? "no reply")
                            NSLog("ASK finished: %@", last)
                            if let d = dirArg { DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.takeShots(into: URL(fileURLWithPath: d), chatOnly: true) } }
                        }
                    }
                }
            }
        }
        if let i = CommandLine.arguments.firstIndex(of: "--shots"), i + 1 < CommandLine.arguments.count, !CommandLine.arguments.contains("--ask") {
            // self-screenshots for testing without Screen Recording permission: pet, workspace, every hub tab
            let dir = URL(fileURLWithPath: CommandLine.arguments[i + 1])
            DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.takeShots(into: dir) }
        }
        if let i = CommandLine.arguments.firstIndex(of: "--profile"), i + 1 < CommandLine.arguments.count {
            let url = URL(fileURLWithPath: CommandLine.arguments[i + 1])
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.analysis.createOverview(files: [url]) }
        }
        readyForOpenFiles = true
        if !pendingOpenFiles.isEmpty {
            let files = pendingOpenFiles
            pendingOpenFiles.removeAll()
            attachOpenedFiles(files)
        } else if CommandLine.arguments.count == 1 {
            hub.show()
            hub.selectTab(0)
        }
    }

    /// Opening from Finder or the Dock brings the chat back even while the desktop pet is visible.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        panel.show()
        hub.show()
        hub.selectTab(0)
        return false
    }

    /// Finder "Open With › Squipi", a Dock drop, or `open -a Squipi file` attaches files for an instruction.
    func application(_ application: NSApplication, open urls: [URL]) {
        NSLog("OPEN %@", urls.map { $0.lastPathComponent }.joined(separator: ", "))
        // Launch Services can deliver the first file before applicationDidFinishLaunching.
        guard readyForOpenFiles else { pendingOpenFiles.append(contentsOf: urls); return }
        attachOpenedFiles(urls)
    }

    private func attachOpenedFiles(_ urls: [URL]) {
        let ok = urls.filter { $0.isFileURL && PetWebView.dataExtensions.contains($0.pathExtension.lowercased()) }
        guard !ok.isEmpty else { voice.say("I can read CSV, Excel, Parquet, PDF, Word, PowerPoint and Markdown files.", force: true); return }
        Conversation.shared.attach(ok)
    }

    func applicationWillTerminate(_ notification: Notification) {
        MLXRuntime.shared.stop()
        HermesRunner.shared.cancel()
        Worker.shared.stop()
    }

    func applyBrainPref() { Task { await brain.prepare() } }

    /// Write PNGs of the pet, the workspace and each hub tab into `dir` (debug `--shots`).
    func takeShots(into dir: URL, chatOnly: Bool = false) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        func save(_ img: NSImage?, _ name: String) {
            guard let img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) else { NSLog("SHOT %@: nothing to save", name); return }
            try? png.write(to: dir.appendingPathComponent(name + ".png"))
            NSLog("SHOT %@ saved (%dx%d)", name, Int(img.size.width), Int(img.size.height))
        }
        func viewImage(_ v: NSView) -> NSImage? {
            guard let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return nil }
            v.cacheDisplay(in: v.bounds, to: rep)
            let img = NSImage(size: v.bounds.size); img.addRepresentation(rep); return img
        }
        if chatOnly {
            hub.show(); hub.selectTab(0)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { if let v = self.hub.contentView { save(viewImage(v), "hub-chat-after") }; NSLog("SHOT done") }
            return
        }
        let cfg = WKSnapshotConfiguration()
        panel.webView.takeSnapshot(with: cfg) { img, _ in save(img, "pet") }
        workspace.present()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            self.workspace.snapshot { img in save(img, "workspace") }
            self.hub.show()
            let tabs = ["chat", "work", "notes", "words", "mind", "me"]
            for (i, name) in tabs.enumerated() {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5 + Double(i) * 1.2) {
                    self.hub.selectTab(i)
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) {
                        if let v = self.hub.contentView { save(viewImage(v), "hub-\(name)") }
                    }
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5 + Double(tabs.count) * 1.2 + 1.5) { NSLog("SHOT done"); self.hub.selectTab(0) }
        }
    }

    /// Stop talking right now (menu bar, Chat Stop, a click on her).
    func hush() {
        voice.stop()
        panel.js("pet.setTalking && pet.setTalking(false)")
    }

    /// If she wandered off or a display changed, put her next to the pointer on the current screen and in front.
    func comeHere() {
        wander.pause(seconds: 60)
        let m = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { $0.frame.contains(m) } ?? NSScreen.main
        var origin = NSPoint(x: m.x + 24, y: m.y - PetPanel.size.height / 2)
        if let f = screen?.visibleFrame {
            origin.x = max(f.minX, min(origin.x, f.maxX - PetPanel.size.width))
            origin.y = max(f.minY, min(origin.y, f.maxY - PetPanel.size.height))
        }
        panel.setFrameOrigin(origin)
        panel.show()
    }

    func applyWanderPref() { wander.enabled = UserDefaults.standard.bool(forKey: "wander") }

    func applySoundsPref() {
        panel.js("pet.setSounds(\(UserDefaults.standard.bool(forKey: "sounds")))")
    }

    func applyLookPref() {
        let look = UserDefaults.standard.string(forKey: "skirtLook") ?? "stars"
        let flower = UserDefaults.standard.bool(forKey: "finFlower")
        let choices: [String: Any] = ["theme": SquipiColour.current.rawValue,
            "outfit": UserDefaults.standard.string(forKey: "squipiOutfit") ?? "classic",
            "accessory": UserDefaults.standard.string(forKey: "squipiAccessory") ?? "none",
            "hat": UserDefaults.standard.string(forKey: "squipiHat") ?? "none",
            "glasses": UserDefaults.standard.object(forKey: "squipiGlasses") as? Bool ?? true]
        if let data = try? JSONSerialization.data(withJSONObject: choices), let json = String(data: data, encoding: .utf8) { panel.js("pet.setWardrobe(\(json))") }
        panel.js("pet.setLook({skirt:'\(look)', flower:\(flower)})")
        panel.js("pet.setReducedMotion(\(UserDefaults.standard.bool(forKey: "reducedMotion")))")
    }

    func applyLanguagePref() {
        voice.stop()
        if listening || talking || meeting.isRecording { pendingSpeechLanguage = true; return }
        pendingSpeechLanguage = false
        selectEngine("apple")
    }

    // MARK: engine
    /// Apple's on-device SpeechAnalyzer is the only speech engine; the id is kept for settings compatibility.
    func selectEngine(_ id: String) {
        UserDefaults.standard.set("apple", forKey: "engine")
        engine = AppleTranscriber()
        Task { await warmEngine() }
    }
    @MainActor private func warmEngine() async {
        panel.js("pet.setState('loading')")
        engineLoading = true
        defer { engineLoading = false }
        do {
            try await engine.prepare()
            panel.js("pet.setState('idle')")
            if !hasWoken { hasWoken = true; DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in self?.voice.woke() } }
        } catch {
            NSLog("engine prepare failed: \(error)")
            panel.js("pet.setState('error')")
        }
    }

    // MARK: push to talk → dictation into the frontmost app (inherited from Ribbit; hold fn)
    func startListening() {
        guard !listening, !meeting.isRecording else { return }
        if pendingSpeechLanguage { applyLanguagePref() }
        listening = true
        voice.stop()
        do { try recorder.start() } catch {
            NSLog("mic start failed: \(error)")
            listening = false
            panel.js("pet.setState('error')")
            return
        }
        panel.js("pet.setState('listening')")
        levelTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.panel.js("pet.setLevel(\(self.recorder.level))")
            }
        }
    }

    func stopListening() {
        guard listening else { return }
        listening = false
        levelTimer?.invalidate(); levelTimer = nil
        let samples = recorder.stop()
        guard samples.count > 16000 / 3 else {      // shorter than ~0.3s: treat as an accidental tap
            panel.js("pet.setState('idle')")
            return
        }
        panel.js("pet.setState('thinking')")
        let engine = self.engine
        engine.vocabulary = Store.shared.vocabulary.words
        Task {
            do {
                let raw = try await engine.transcribe(samples)
                await MainActor.run {
                    let text = Store.shared.applyReplacements(raw)
                    if text.isEmpty { panel.js("pet.setState('idle')"); voice.heardNothing(); return }
                    panel.js("pet.setState('success')")
                    paster.paste(text + " ")
                    Store.shared.recordDictation(text)
                    let voice = self.voice
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { voice.dictated(text) }
                }
            } catch {
                NSLog("transcribe failed: \(error)")
                await MainActor.run { panel.js("pet.setState('error')") }
            }
        }
    }

    // MARK: talk to Squipi (double-click her, the mic button, the menu bar, or hold right Option)
    /// Double-click / mic button: start listening and stop after a pause in speech (or when pressed again).
    func toggleTalk() {
        if talking { stopTalk(); return }
        talkAuto = true
        startTalk()
    }

    func startTalk() {
        guard !listening, !talking, !meeting.isRecording else { return }
        if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
            Permissions.requestMicrophone()
            voice.say("I need the microphone first. System Settings, Privacy, Microphone, then Squipi.", force: true)
            hub.show()
            return
        }
        if pendingSpeechLanguage { applyLanguagePref() }
        talking = true
        voice.stop()
        do { try recorder.start() } catch { talking = false; talkAuto = false; panel.js("pet.setState('error')"); return }
        talkStart = Date(); lastLoud = Date(); heardSpeech = false
        TalkState.shared.listening = true
        panel.js("pet.setState('listening')")
        panel.js("pet.setCaption('\(talkAuto ? "listening… double-click or the mic button when you are done" : "listening…")')")
        noiseFloor = 0.02
        levelTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                let lv = self.recorder.level
                self.panel.js("pet.setLevel(\(lv))")
                let now = Date()
                // adaptive: the quietest level seen is the room; speech is clearly above it
                if lv < self.noiseFloor { self.noiseFloor = max(0.005, lv) } else { self.noiseFloor += (lv - self.noiseFloor) * 0.002 }
                let threshold = max(0.05, self.noiseFloor * 3)
                if lv > threshold { self.heardSpeech = true; self.lastLoud = now }
                guard self.talkAuto else { return }
                if self.heardSpeech, now.timeIntervalSince(self.lastLoud) > (UserDefaults.standard.bool(forKey: "relaxedSpeechPauses") ? 2.8 : 1.2) { self.stopTalk() }          // a real pause: done
                else if !self.heardSpeech, now.timeIntervalSince(self.talkStart) > 20 { self.stopTalk() }   // nothing came
                else if now.timeIntervalSince(self.talkStart) > 180 { self.stopTalk() }                     // hard cap: 3 minutes
            }
        }
    }

    func stopTalk() {
        guard talking else { return }
        talking = false
        talkAuto = false
        TalkState.shared.listening = false
        levelTimer?.invalidate(); levelTimer = nil
        panel.js("pet.setCaption('')")
        let samples = recorder.stop()
        guard samples.count > 16000 / 3 else { panel.js("pet.setState('idle')"); return }
        if !heardSpeech { NSLog("TALK heard no speech above the noise floor (%.3f)", noiseFloor); panel.js("pet.setState('idle')"); voice.heardNothing(); return }
        NSLog("TALK %.1fs of audio, noise floor %.3f", Double(samples.count) / 16000, noiseFloor)
        panel.js("pet.setState('thinking')")
        panel.js("pet.setCaption('hearing…')")
        let engine = self.engine
        Task {
            do {
                let said = try await engine.transcribe(samples)
                await MainActor.run {
                    panel.js("pet.setCaption('')")
                    if said.isEmpty { panel.js("pet.setState('idle')"); voice.heardNothing(); return }
                    NSLog("HEARD %@", said)
                    Conversation.shared.send(said, spoken: true)      // the reply is spoken and also appears in the Chat tab
                }
            } catch {
                NSLog("transcribe failed: \(error)")
                await MainActor.run { panel.js("pet.setCaption('')"); panel.js("pet.setState('error')"); Conversation.shared.messages.append(.init(role: "system", text: error.localizedDescription, via: "Apple speech")) }
            }
        }
    }

    /// Honest status when the local model is not available. Never a canned "answer".
    func brainUnavailableLine() -> String {
        let s = brain.status
        if s.hasPrefix("downloading") { return "My brain is still downloading, \(s.dropFirst(12)). Ask me again in a bit." }
        if brain.enabled && (s.hasPrefix("checking") || s.hasPrefix("loading")) { return "Give me a moment, my brain is still waking up." }
        if s.hasPrefix("failed") { return "My local model failed to load. The Me tab shows the error." }
        return "I heard you, but my local model is off. Turn it on in the Me tab, or set up Nebius."
    }

    // MARK: demo (cycles states so the pet can be screenshotted)
    private func runDemo() {
        let states = ["idle", "listening", "thinking", "analysing", "approving", "success", "recording", "error", "sleeping"]
        var i = 0
        var t: Double = 0
        demoTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            t += 1.0 / 30.0
            if t >= 4 { t = 0; i = (i + 1) % states.count; self.panel.js("pet.setState('\(states[i])')") }
            if states[i] == "listening" { self.panel.js("pet.setLevel(\(abs(sin(t * 6)) * 0.8))") }
        }
    }
}
