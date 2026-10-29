import AppKit
import AVFoundation
import Speech

// Debug entry points (no UI):
//   Squipi --transcribe file.wav
//   Squipi --summarize transcript.txt
//   Squipi --tap-test 5            capture 5s of system audio, transcribe it
//   Squipi --chat "hey" ["..."]     load the brain once, print its spoken reply to each message (no memory writes)
//   Squipi --write "question"        same, but the longer written register used by the Chat tab
//   Squipi --speak "text" out.caf    render a line with Squipi's voice settings to an audio file
//   Squipi --worker                  set up the analysis worker (if needed), run its self-test, print the profile summary
//   Squipi --agent "question" [file…] start the worker, import/read the files, let the Ollama model answer with tools; prints each step
//   Squipi --profile file.csv        launch the app and profile that file right away (UI run)
/// Collects AVSpeechSynthesizer.write buffers into one audio file.
final class SpeechFileWriter: @unchecked Sendable {
    private let url: URL
    private var file: AVAudioFile?
    private var frames: Int64 = 0
    private var rate: Double = 22050
    private let q = DispatchQueue(label: "speechwriter")
    init(url: URL) { self.url = url }
    func write(_ pcm: AVAudioPCMBuffer) {
        q.sync {
            if file == nil { file = try? AVAudioFile(forWriting: url, settings: pcm.format.settings, commonFormat: pcm.format.commonFormat, interleaved: pcm.format.isInterleaved); rate = pcm.format.sampleRate }
            try? file?.write(from: pcm); frames += Int64(pcm.frameLength)
        }
    }
    private var finished = false
    /// True the first time only; the synthesizer sends more than one empty buffer at the end.
    func finish() -> Bool { q.sync { if finished { return false }; finished = true; file = nil; return true } }
    var seconds: Double { q.sync { Double(frames) / rate } }
}

final class SampleSink: @unchecked Sendable {
    private var buf: [Float] = []
    private let q = DispatchQueue(label: "sink")
    func append(_ s: [Float]) { q.sync { buf.append(contentsOf: s) } }
    func drain() -> [Float] { q.sync { buf } }
}

func runDebug(_ args: [String]) -> Bool {
    guard args.count >= 2, ["--transcribe", "--summarize", "--tap-test", "--check", "--brain", "--chat", "--write", "--speak", "--worker", "--agent"].contains(args[1]) else { return false }
    let sem = DispatchSemaphore(value: 0)
    Task {
        do {
            switch args[1] {
            case "--transcribe":
                let engine: Transcriber = AppleTranscriber()
                let t0 = Date(); try await engine.prepare(); let t1 = Date()
                let samples = try AudioFileLoader.load16kMono(path: args[2])
                let words = try await engine.transcribeTimed(samples); let t2 = Date()
                print("[\(engine.name)] \(AppleTranscriber.join(words))")
                print("words: \(words.count), first: \(words.prefix(3).map { "\($0.text)@\(String(format: "%.2f", $0.start))" })")
                print(String(format: "prepare %.2fs | transcribe %.2fs | audio %.1fs", t1.timeIntervalSince(t0), t2.timeIntervalSince(t1), Double(samples.count) / 16000))
            case "--summarize":
                let text = try String(contentsOfFile: args[2], encoding: .utf8)
                print("apple available: \(Summarizer.appleAvailable), active engine: \(String(describing: Summarizer.active))")
                let (title, body) = try await Summarizer.summarize(transcript: text)
                print("TITLE: \(title)\n\(body)")
            case "--brain":
                UserDefaults.standard.set(true, forKey: "brainOn")
                let brain = await Brain()
                await brain.prepare()
                print("status: \(await brain.status)")
                var t0 = Date()
                print("say:  \(await brain.say(args.count >= 3 ? args[2] : "What is a difference-in-differences design, in one sentence?") ?? "nil")")
                print(String(format: "  %.2fs", Date().timeIntervalSince(t0))); t0 = Date()
                print("chat: \(await brain.chat("Hey Squipi, how is your day going? Also remind me in 20 minutes to send the draft.") ?? "nil")")
                print(String(format: "  %.2fs incl. memory extraction", Date().timeIntervalSince(t0)))
                print("reminders: \(await Mind.shared.reminders.map { "\($0.text) @ \($0.due.map { "\($0)" } ?? "no time")" })")
                print("facts: \(await Mind.shared.facts)")
            case "--chat":
                UserDefaults.standard.set(true, forKey: "brainOn")
                let brain = await Brain()
                await brain.prepare()
                print("status: \(await brain.status)")
                for m in args.dropFirst(2) { print("> \(m)"); print(await brain.say(m) ?? "nil") }
            case "--write":
                UserDefaults.standard.set(true, forKey: "brainOn")
                let brain = await Brain()
                await brain.prepare()
                print("status: \(await brain.status)")
                for m in args.dropFirst(2) { print("> \(m)"); print(await brain.write(m) ?? "nil") }
            case "--worker":
                let worker = await Worker.shared
                if await !worker.isInstalled { print("installing worker env…"); await worker.install() }
                print("worker: \(await worker.status.label)")
                try await worker.ensureRunning()
                let tools = await worker.tools.map(\.name)
                print("tools: \(tools.joined(separator: ", "))")
                let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("squipi-selftest-\(UUID().uuidString.prefix(6)).csv")
                let csv = "household_id,survey_date,district,treated,monthly_income\n" + (1...200).map { i in "\(1000 + i),2023-0\(1 + i % 3)-01,\(["North", "South", "East"][i % 3]),\(i % 2),\(Double(i) * 12.5)" }.joined(separator: "\n") + "\n"
                try csv.write(to: tmp, atomically: true, encoding: .utf8)
                try await worker.allow([tmp.path])
                let imp = try await worker.call("import_dataset", ["path": tmp.path])
                let prof = try await worker.call("profile_dataset", ["dataset_id": imp["dataset_id"] as? String ?? ""]) { pct, note in print("  \(pct)% \(note)") }
                print(prof["summary_text"] as? String ?? "no summary")
                print("report: \(prof["report_path"] as? String ?? "none")")
                await worker.stop()
            case "--agent":
                guard args.count >= 3 else { print("usage: --agent \"question\" [file …]"); break }
                ModelRouter.transient = true
                let environment = ProcessInfo.processInfo.environment
                let testDirectory = environment["SQUIPI_TEST_DATA_DIR"]
                let isolatedTest = testDirectory?.hasPrefix("/") == true
                var history: [ChatMessage] = []
                var overrides = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
                if isolatedTest, let code = environment["SQUIPI_TEST_LANGUAGE"], let language = SquipiLanguage(rawValue: code) { overrides["squipiLanguage"] = language.rawValue }
                if let m = ProcessInfo.processInfo.environment["SQUIPI_OLLAMA_MODEL"] {
                    overrides["ollamaModel"] = m
                    if isolatedTest { overrides["hermesModel"] = m }
                }
                if let endpoint = environment["SQUIPI_TEST_OLLAMA_URL"] {
                    guard isolatedTest, let url = URLComponents(string: endpoint),
                          ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
                          ["localhost", "127.0.0.1", "::1", "[::1]"].contains(url.host?.lowercased() ?? ""),
                          url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
                          ["", "/", "/v1", "/v1/"].contains(url.path) else {
                        throw ProviderError.invalidResponse("Test model overrides require an isolated --agent directory and a loopback endpoint.")
                    }
                    overrides["ollamaBaseURL"] = endpoint
                }
                if let historyPath = environment["SQUIPI_AGENT_HISTORY"] {
                    guard isolatedTest, historyPath.hasPrefix("/"), let testDirectory else {
                        throw ProviderError.invalidResponse("Test history requires an isolated --agent directory.")
                    }
                    let file = URL(fileURLWithPath: historyPath).resolvingSymlinksInPath()
                    let root = URL(fileURLWithPath: testDirectory).resolvingSymlinksInPath()
                    guard file.path.hasPrefix(root.path + "/") else {
                        throw ProviderError.invalidResponse("Test history must be inside its isolated data directory.")
                    }
                    let data = try Data(contentsOf: file)
                    guard data.count <= 1_000_000,
                          let entries = try JSONSerialization.jsonObject(with: data) as? [[String: String]], entries.count <= 100,
                          entries.allSatisfy({ Set($0.keys) == ["role", "content"] && ["user", "assistant"].contains($0["role"] ?? "") }) else {
                        throw ProviderError.invalidResponse("Test history must contain at most 100 user/assistant messages with role and content fields.")
                    }
                    history = entries.map { ChatMessage(role: $0["role"]!, content: $0["content"]!) }
                }
                let testProvider = environment["SQUIPI_TEST_PROVIDER"] ?? "ollama"
                guard ["ollama", "hermes", "nebius", "mlx"].contains(testProvider), testProvider == "ollama" || isolatedTest else {
                    throw ProviderError.invalidResponse("Alternative provider tests require an isolated data directory.")
                }
                if let endpoint = environment["SQUIPI_TEST_MLX_URL"] {
                    guard isolatedTest else { throw ProviderError.badURL }
                    try MLXConfiguration.validateEndpoint(endpoint)
                    overrides["mlxBaseURL"] = endpoint
                }
                if environment["SQUIPI_TEST_DIRECTORY_GRANT"] == "1" {
                    guard isolatedTest, let testDirectory,
                          environment["SQUIPI_TEST_OLLAMA_URL"] != nil || environment["SQUIPI_TEST_MLX_URL"] != nil else {
                        throw ProviderError.invalidResponse("Directory fixtures require an isolated data directory and controlled loopback model.")
                    }
                    let testRoot = URL(fileURLWithPath: testDirectory)
                    let folder = testRoot.appendingPathComponent("granted", isDirectory: true)
                    guard LocalFilePolicy.contains(root: testRoot, candidate: folder),
                          (try? folder.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true else {
                        throw ProviderError.invalidResponse("Directory fixtures must remain inside the isolated test root.")
                    }
                    guard ["ollama", "mlx"].contains(testProvider) else { throw ProviderError.invalidResponse("Directory fixtures use controlled local model providers only.") }
                }
                if let cloudModel = environment["SQUIPI_TEST_NEBIUS_MODEL"] {
                    guard isolatedTest, testProvider == "nebius", !cloudModel.isEmpty else { throw ProviderError.invalidResponse("Cloud model overrides require an isolated Nebius evaluation.") }
                    overrides["nebiusModel"] = cloudModel
                }
                if testProvider == "mlx" { overrides["mlxThink"] = false }
                if testProvider == "nebius", environment["SQUIPI_TEST_CLOUD_CONSENT"] != "synthetic-household-benchmark" {
                    throw ProviderError.invalidResponse("The cloud benchmark requires explicit consent for sending synthetic test data.")
                }
                if isolatedTest {
                    overrides["hermesAllowNetwork"] = false
                    // Reproducible local qualification without changing the user's setting.
                    overrides["ollamaThink"] = false
                    // Legacy scripted peers encode exact request sequences; the contract call is
                    // opt-in for them (SQUIPI_TEST_CONTRACT=1) and on by default in the app.
                    overrides["contractFirst"] = environment["SQUIPI_TEST_CONTRACT"] == "1"
                    overrides["argumentRepair"] = environment["SQUIPI_TEST_ARGUMENT_REPAIR"] == "1"
                    overrides["appleIntelligenceAssist"] = environment["SQUIPI_TEST_APPLE_INTELLIGENCE"] == "1"
                }
                UserDefaults.standard.setVolatileDomain(overrides, forName: UserDefaults.argumentDomain)
                if environment["SQUIPI_TEST_DIRECTORY_GRANT"] == "1", let testDirectory {
                    try await MainActor.run { try LocalFiles.shared.useIsolatedDirectoryFixture(URL(fileURLWithPath: testDirectory)) }
                }
                await MainActor.run { ModelRouter.shared.mode = ModelRouter.Mode(rawValue: testProvider)! }
                let worker = await Worker.shared
                let historyEntries = history.map { ResearchContinuation.HistoryEntry(role: $0.role == "assistant" ? "squipi" : $0.role, text: $0.content) }
                let lightweight = args.count == 3 && ResearchContinuation.isLightweightChat(args[2], history: historyEntries)
                if !lightweight {
                    if await !worker.isInstalled { print("installing worker env…"); await worker.install() }
                    try await worker.ensureRunning()
                }
                // With SQUIPI_AGENT_ATTACH=1 the files on the command line are attachments of this message, marked
                // exactly as Chat marks them ("[Attached dataset …]") so binding treats them as fresh attachments.
                // Without it (the scripted suites) they are pre-existing project files, as before.
                let markAttachments = environment["SQUIPI_AGENT_ATTACH"] == "1"
                var attachmentLines: [String] = []
                for f in args.dropFirst(3) {
                    let url = URL(fileURLWithPath: f)
                    try await worker.allow([url.path])
                    if Analysis.isDocumentFile(url) {
                        let r = try await worker.call("read_document", ["path": url.path])
                        print("read: \(r["summary_text"] as? String ?? "")")
                        if let id = r["document_id"] as? String { await Analysis.shared.setCurrent(document: id) }
                        attachmentLines.append("Attached document \(url.lastPathComponent): \(r["summary_text"] as? String ?? "") Its id is \(r["document_id"] as? String ?? "?").")
                    } else {
                        let r = try await worker.call("import_dataset", ["path": url.path])
                        print("imported: \(r["dataset_id"] as? String ?? "?") \(r["rows"] ?? 0)×\(r["cols"] ?? 0)")
                        if let id = r["dataset_id"] as? String { await Analysis.shared.setCurrent(dataset: id) }
                        let cols = (r["columns"] as? [String] ?? []).prefix(40).joined(separator: ", ")
                        attachmentLines.append("Attached dataset \(url.lastPathComponent) as \(r["dataset_id"] as? String ?? "?"): \((r["rows"] as? Int ?? 0).formatted()) rows × \(r["cols"] as? Int ?? 0) columns. Columns: \(cols)")
                        for sib in r["sibling_datasets"] as? [[String: Any]] ?? [] {
                            attachmentLines.append("Other sheet of the same workbook, registered but not selected: '\(sib["sheet"] as? String ?? "?")' = \(sib["dataset_id"] as? String ?? "?") (\((sib["rows"] as? Int ?? 0).formatted()) rows × \(sib["cols"] as? Int ?? 0) columns)")
                        }
                    }
                }
                if !lightweight { await Analysis.shared.refreshInventory() }
                // Replay the same pending-task recovery used by Chat, so a test's
                // final clarification retains the original goal and selected input.
                var instruction = args[2] + (markAttachments ? attachmentLines.map { "\n\n[\($0)]" }.joined() : "")
                var continuingTask = false
                if !history.isEmpty {
                    let entries = history.map { ResearchContinuation.HistoryEntry(role: $0.role == "assistant" ? "squipi" : "user", text: $0.content) }
                    var continuation = ResearchContinuation.recover(from: entries)
                    let inputs = await Analysis.shared.researchInputIDs(for: args[2])
                    instruction = continuation.prepare(userText: args[2], inputIDs: inputs, history: entries).effectiveInstruction
                        + (markAttachments ? attachmentLines.map { "\n\n[\($0)]" }.joined() : "")
                    continuingTask = !(continuation.pending?.amendments.isEmpty ?? true)
                }
                let t0 = Date()
                let reply: Agent.Reply
                if let idText = environment["SQUIPI_TEST_NEMO_JOB"], isolatedTest, let jobID = UUID(uuidString: idText) {
                    reply = await NemoRunner.shared.run(jobID: jobID, objective: instruction, followup: args[2], inputs: args.dropFirst(3).map { URL(fileURLWithPath: $0) }, templates: [])
                } else if testProvider == "hermes" {
                    let configuration = try await HermesRunner.localConfiguration()
                    reply = await HermesRunner.shared.research(instruction: instruction, history: history, configuration: configuration)
                } else {
                    reply = await Agent.shared.respond(to: instruction, history: history, register: .written, preferStructuredActions: continuingTask, allowQuickConversation: args.count == 3, discussionOnly: isolatedTest && environment["SQUIPI_TEST_DISCUSSION"] == "1", userInstruction: args[2], onDelta: { t, _ in if let t { print(t, terminator: ""); fflush(stdout) } })
                }
                print("TASK_STATUS: \(reply.status?.rawValue ?? "unknown")")
                if isolatedTest, let testDirectory {
                    let evidence: [String: Any] = ["status": reply.status?.rawValue ?? "unknown", "text": reply.text,
                        "error": reply.error ?? "", "via": reply.via, "task_id": reply.taskID ?? "",
                        "seconds": Date().timeIntervalSince(t0),
                        "steps": reply.steps.map { ["tool": $0.tool, "ok": $0.ok, "outputs": $0.outputs.map(\.path)] as [String: Any] }]
                    try JSONSerialization.data(withJSONObject: evidence, options: [.prettyPrinted, .sortedKeys])
                        .write(to: URL(fileURLWithPath: testDirectory).appendingPathComponent("last-agent-result.json"), options: .atomic)
                }
                print("")
                // Structured recovery deliberately does not stream unvalidated JSON.
                // Print the accepted answer even when that path produced no deltas.
                if !reply.text.isEmpty { print("FINAL: \(reply.text)") }
                for s in reply.steps { print(String(format: "  [%@ %.1fs] %@ %@", s.ok ? "ok" : "FAILED", s.elapsed, s.tool, s.summary.prefix(200) as CVarArg)); for o in s.outputs { print("     → \(o.path)") } }
                print("via \(reply.via)" + (reply.error.map { " · error: \($0)" } ?? "") + String(format: " · %.1fs total", Date().timeIntervalSince(t0)))
                if !reply.reasoning.isEmpty { print("(reasoning: \(reply.reasoning.count) chars)") }
                await worker.stop()
            case "--speak":
                guard args.count >= 4 else { print("usage: --speak \"text\" out.caf"); break }
                let out = URL(fileURLWithPath: args[3])
                try? FileManager.default.removeItem(at: out)
                let u = AVSpeechUtterance(string: args[2])
                let name = UserDefaults.standard.string(forKey: "voiceName") ?? PetVoice.defaultVoice
                u.voice = await MainActor.run { PetVoice.voice(named: name) }
                u.rate = 0.5
                u.pitchMultiplier = 1.05
                u.volume = 0.9
                let synth = AVSpeechSynthesizer()
                let writer = SpeechFileWriter(url: out)
                await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                    synth.write(u) { buffer in
                        if let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 { writer.write(pcm) } else if writer.finish() { c.resume() }
                    }
                }
                print("wrote \(out.path) (\(String(format: "%.2f", writer.seconds))s, voice \(u.voice?.name ?? "?"))")
            case "--nebius-models":
                // The ids this account can use, from GET /models with the stored key; nothing else is sent.
                guard let key = await MainActor.run(body: { NebiusProvider.apiKey }) else { print("no Nebius key stored (Me tab)"); break }
                let models = try await NebiusProvider(kind: .nebius).listModels(key: key)
                let featured = NebiusModels.featuredAvailableModels(models)
                print("\(models.count) models; Nemotron/Kimi ids:"); featured.forEach { print("  " + $0) }
                print("configured presets: " + NebiusModels.presets.map(\.id).joined(separator: ", "))
            case "--check":
                print("accessibility trusted: \(Permissions.accessibilityTrusted)")
                print("mic: \(AVCaptureDevice.authorizationStatus(for: .audio).rawValue) (3 = authorized)")
                print("speech: \(SFSpeechRecognizer.authorizationStatus().rawValue) (3 = authorized)")
                print("fn key does something else: \(Permissions.fnKeyDoesSomethingElse)")
            case "--tap-test":
                let secs = Double(args.count >= 3 ? args[2] : "5") ?? 5
                let tap = SystemAudioTap()
                let sink = SampleSink()
                tap.onSamples = { s in sink.append(s) }
                try tap.start()
                print("tap started, capturing \(secs)s of system audio…")
                try await Task.sleep(nanoseconds: UInt64(secs * 1e9))
                tap.stop()
                let samples = sink.drain()
                var peak: Float = 0; for s in samples { peak = max(peak, abs(s)) }
                print(String(format: "captured %.1fs, peak %.3f", Double(samples.count) / 16000, peak))
                var rms: [String] = []
                for sec in stride(from: 0, to: samples.count, by: 16000) {
                    let slice = samples[sec..<min(sec + 16000, samples.count)]
                    rms.append(String(format: "%.2f", (slice.reduce(0) { $0 + $1 * $1 } / Float(slice.count)).squareRoot()))
                }
                print("rms/sec: \(rms.joined(separator: " "))")
                let dump = try AudioFileLoader.writeTempCAF(samples); print("saved \(dump.path)")
                let engine = AppleTranscriber(); try await engine.prepare()
                print("heard: \(try await engine.transcribe(samples))")
            default:
                print("unknown flag \(args[1])")
            }
        } catch { print("error: \(error)") }
        sem.signal()
    }
    while sem.wait(timeout: .now()) == .timedOut { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
    return true
}

if runDebug(CommandLine.arguments) { exit(0) }

// Launched as an app: keep a readable log next to the data (stderr is where NSLog writes). Launching from a terminal
// to capture it instead makes macOS attribute permission prompts to the terminal, so do not do that.
if isatty(2) == 0, let logDir = try? FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("Squipi") {
    try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
    let logPath = logDir.appendingPathComponent("squipi.log").path
    if let a = try? FileManager.default.attributesOfItem(atPath: logPath), (a[.size] as? Int ?? 0) > 2_000_000 { try? FileManager.default.removeItem(atPath: logPath) }
    freopen(logPath, "a", stderr)
}

if CommandLine.arguments.contains("--fresh") {      // test runs start from an empty chat
    MainActor.assumeIsolated { try? FileManager.default.removeItem(at: Store.shared.dir.appendingPathComponent("chat.json")) }
}
let app = NSApplication.shared
let delegate = MainActor.assumeIsolated { AppDelegate() }
app.delegate = delegate
app.setActivationPolicy(.regular)    // Finder/Dock launch and reopen behave like a normal Mac app.
app.run()
