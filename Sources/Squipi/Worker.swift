import Foundation
import Darwin

/// A tiny thread-safe string holder for text that arrives on background handlers.
final class LineBox: @unchecked Sendable {
    private var value = ""
    private let lock = NSLock()
    func set(_ v: String) { lock.lock(); value = v; lock.unlock() }
    func get() -> String { lock.lock(); defer { lock.unlock() }; return value }
}

/// Squipi's analysis worker: a Python child process (worker/ in the app bundle) that speaks JSON lines
/// on stdin/stdout. Private IPC, no sockets. One job at a time, with progress and cancellation.
/// The worker only reads files we explicitly `allow`, and only writes inside its project directory.
@MainActor
final class Worker: ObservableObject {
    static let shared = Worker()

    enum Status: Equatable {
        case notInstalled, installing(String), starting, ready, stopped, failed(String)
        var label: String {
            switch self {
            case .notInstalled: return "not set up"
            case .installing(let s): return "setting up… \(s)"
            case .starting: return "starting"
            case .ready: return "ready"
            case .stopped: return "stopped"
            case .failed(let e): return "failed: \(e)"
            }
        }
    }
    struct ToolSpec: Identifiable {
        let name: String, description: String, params: [String: Any], needsModel: Bool, tested: Bool
        var id: String { name }
    }
    struct Failure: Error, LocalizedError {
        let code: String, message: String
        var errorDescription: String? { message }
        var isCancelled: Bool { code == "cancelled" }
    }

    @Published private(set) var status: Status = .notInstalled
    @Published private(set) var tools: [ToolSpec] = []
    @Published private(set) var info: [String: Any] = [:]
    @Published private(set) var researchSetup = ""

    /// ~/Library/Application Support/Squipi/worker-env — the pinned Python environment (dependencies only).
    let envDir: URL = Store.shared.dir.appendingPathComponent("worker-env", isDirectory: true)
    /// ~/Library/Application Support/Squipi/projects/default — where datasets are registered and artifacts written.
    let projectDir: URL = Store.shared.dir.appendingPathComponent("projects/default", isDirectory: true)
    /// The bundled worker source; in development it falls back to the repository's worker/ folder.
    static var sourceDir: URL? {
        if let u = Bundle.main.url(forResource: "worker", withExtension: nil), FileManager.default.fileExists(atPath: u.appendingPathComponent("squipi_worker").path) { return u }
        let dev = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("worker")
        return FileManager.default.fileExists(atPath: dev.appendingPathComponent("squipi_worker").path) ? dev : nil
    }
    var python: URL {
        let enhanced = Store.shared.dir.appendingPathComponent("research-env/bin/python")
        if FileManager.default.fileExists(atPath: Store.shared.dir.appendingPathComponent("research-ready.json").path),
           FileManager.default.isExecutableFile(atPath: enhanced.path) { return enhanced }
        if ProcessInfo.processInfo.environment["SQUIPI_TEST_DATA_DIR"] != nil,
           let testPython = ProcessInfo.processInfo.environment["SQUIPI_TEST_RESEARCH_PYTHON"] {
            return URL(fileURLWithPath: testPython)
        }
        return envDir.appendingPathComponent("bin/python")
    }
    var isInstalled: Bool { FileManager.default.isExecutableFile(atPath: python.path) }

    func installResearch() async {
        guard !Conversation.shared.busy && !Analysis.shared.busy else { researchSetup = "Pause current research before setting up libraries."; return }
        guard let source = Self.sourceDir,
              let uv = Bundle.main.url(forResource: "uv", withExtension: nil, subdirectory: "bin") else {
            researchSetup = "The bundled research installer is unavailable."; return
        }
        if researchSetup.hasPrefix("Installing") { return }
        researchSetup = "Installing research packages and local OCR models…"
        process?.terminate()
        let setup = Process()
        setup.executableURL = URL(fileURLWithPath: "/bin/bash")
        setup.arguments = [source.appendingPathComponent("bootstrap-research.sh").path, Store.shared.dir.path, uv.path]
        setup.environment = ["PATH": "/usr/bin:/bin", "HOME": NSHomeDirectory(), "LANG": "en_US.UTF-8"]
        let output = Pipe(); setup.standardOutput = output; setup.standardError = output
        let last = LineBox()
        output.fileHandleForReading.readabilityHandler = { handle in
            let text = String(decoding: handle.availableData, as: UTF8.self)
            if let line = text.split(separator: "\n").last { last.set(String(line.suffix(400))) }
        }
        let code: Int32 = await withCheckedContinuation { continuation in
            setup.terminationHandler = { continuation.resume(returning: $0.terminationStatus) }
            do { try setup.run() } catch { last.set(error.localizedDescription); continuation.resume(returning: -1) }
        }
        output.fileHandleForReading.readabilityHandler = nil
        researchSetup = code == 0 ? "Research pack installed. The base runtime remains available for fallback." : "Research setup failed: " + last.get()
        if code == 0 { try? await ensureRunning() }
    }

    private var process: Process?
    private var stdinHandle: FileHandle?
    private var buffer = Data()
    private var nextID = 0
    private var activeObjective: String?
    private struct Pending { let continuation: CheckedContinuation<[String: Any], Error>; let progress: ((Int, String) -> Void)? }
    private var pending: [String: Pending] = [:]

    private init() {
        status = isInstalled ? .stopped : .notInstalled
    }

    // MARK: install

    /// Runs worker/bootstrap.sh into envDir. Needs network the first time (pip/uv downloads pinned wheels).
    func install() async {
        guard let src = Self.sourceDir else { status = .failed("worker source missing from the app bundle"); return }
        if case .installing = status { return }
        status = .installing("preparing")
        let script = src.appendingPathComponent("bootstrap.sh")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [script.path, envDir.path]
        p.currentDirectoryURL = src
        var env = ProcessInfo.processInfo.environment
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        p.environment = env
        let out = Pipe(), err = Pipe()
        p.standardOutput = out; p.standardError = err
        let lastLine = LineBox()
        err.fileHandleForReading.readabilityHandler = { h in
            let s = String(decoding: h.availableData, as: UTF8.self)
            if let l = s.split(separator: "\n").last.map(String.init), !l.isEmpty {
                lastLine.set(l)
                Task { @MainActor in self.status = .installing(String(l.prefix(60))) }
            }
        }
        var launchError: String?
        let code: Int32 = await withCheckedContinuation { (c: CheckedContinuation<Int32, Never>) in
            p.terminationHandler = { proc in c.resume(returning: proc.terminationStatus) }
            do { try p.run() } catch { launchError = error.localizedDescription; c.resume(returning: -1) }
        }
        err.fileHandleForReading.readabilityHandler = nil
        if let launchError { status = .failed("could not run bootstrap: \(launchError)"); return }
        let stdout = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        if code == 0, stdout.contains("READY ") {
            status = .stopped
            NSLog("WORKER installed at %@", envDir.path)
        } else {
            let last = lastLine.get()
            let detail = last.isEmpty ? "bootstrap exited with \(code)" : last
            status = .failed(detail)
            NSLog("WORKER install failed: %@\n%@", detail, stdout)
        }
    }

    // MARK: lifecycle

    /// Starts the process if needed and says hello. Installs first when the environment is missing.
    func ensureRunning() async throws {
        if status == .ready, process?.isRunning == true { return }
        if !isInstalled { await install() }
        guard isInstalled else { throw Failure(code: "not_installed", message: "the analysis worker is not set up: \(status.label)") }
        try start()
        let hello = try await request("hello", params: [:])
        info = hello
        tools = (hello["tools"] as? [[String: Any]] ?? []).map {
            ToolSpec(name: $0["name"] as? String ?? "?", description: $0["description"] as? String ?? "",
                     params: $0["params"] as? [String: Any] ?? [:], needsModel: $0["needs_model"] as? Bool ?? false, tested: $0["tested"] as? Bool ?? false)
        }
        status = .ready
    }

    private func start() throws {
        if let p = process, p.isRunning { return }
        guard let src = Self.sourceDir else { throw Failure(code: "missing", message: "worker source missing") }
        try FileManager.default.createDirectory(at: projectDir, withIntermediateDirectories: true)
        status = .starting
        let p = Process()
        p.executableURL = python
        p.arguments = ["-m", "squipi_worker", "--project", projectDir.path]
        p.currentDirectoryURL = projectDir
        var env: [String: String] = ["PATH": "/usr/bin:/bin", "HOME": NSHomeDirectory(), "LANG": "en_US.UTF-8"]
        env["PYTHONPATH"] = src.path
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["HF_HUB_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["DO_NOT_TRACK"] = "1"
        env["HF_HOME"] = Store.shared.dir.appendingPathComponent("research-model-cache").path
        env["SQUIPI_RSCRIPT"] = Store.shared.dir.appendingPathComponent("research-r/bin/Rscript").path
        env["SQUIPI_KUMO_CONFIG"] = KumoRuntime.config.path
        env["SQUIPI_KUMO_PREDICT_CONFIG"] = KumoRuntime.predictionConfig.path
        env["SQUIPI_DOCUMENT_MODELS"] = Store.shared.dir.appendingPathComponent("research-document-models").path
        // Meaning-based recall and passage search use nomic-embed-text through the local Ollama daemon
        // (loopback only; the worker refuses any other host). Absent model or daemon → keyword mode.
        env["SQUIPI_OLLAMA_URL"] = OllamaProvider().baseURL
        if let embedModel = UserDefaults.standard.string(forKey: "embeddingModel"), !embedModel.isEmpty { env["SQUIPI_EMBED_MODEL"] = embedModel }
        if ProcessInfo.processInfo.environment["SQUIPI_TEST_DATA_DIR"] != nil {
            // Isolated test runs talk to a scripted model peer; an embedding probe would land on that peer as an
            // unexpected request. Keyword mode is the default there; live checks opt in with SQUIPI_EMBEDDINGS=on.
            env["SQUIPI_EMBEDDINGS"] = ProcessInfo.processInfo.environment["SQUIPI_EMBEDDINGS"] ?? "off"
        }
        if ProcessInfo.processInfo.environment["SQUIPI_TEST_DATA_DIR"] != nil {
            for key in ["SQUIPI_RSCRIPT", "SQUIPI_DOCUMENT_MODELS"] {
                if let value = ProcessInfo.processInfo.environment[key] { env[key] = value }
            }
        }
        let plottingCache = projectDir.appendingPathComponent(".matplotlib-cache", isDirectory: true)
        try FileManager.default.createDirectory(at: plottingCache, withIntermediateDirectories: true)
        env["MPLCONFIGDIR"] = plottingCache.path
        p.environment = env
        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        p.standardInput = inPipe; p.standardOutput = outPipe; p.standardError = errPipe
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            Task { @MainActor in self?.consume(data) }
        }
        errPipe.fileHandleForReading.readabilityHandler = { h in
            let s = String(decoding: h.availableData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if !s.isEmpty { NSLog("WORKER stderr: %@", String(s.prefix(2000))) }
        }
        p.terminationHandler = { [weak self] proc in
            Task { @MainActor in
                guard let self else { return }
                self.status = proc.terminationStatus == 0 ? .stopped : .failed("worker exited (\(proc.terminationStatus))")
                for (_, pend) in self.pending { pend.continuation.resume(throwing: Failure(code: "exited", message: "the worker stopped unexpectedly")) }
                self.pending.removeAll()
                self.process = nil; self.stdinHandle = nil
            }
        }
        try p.run()
        process = p
        stdinHandle = inPipe.fileHandleForWriting
    }

    func stop() {
        guard let p = process, p.isRunning else { return }
        try? send(["id": "bye", "method": "shutdown"])
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in if let p = self?.process, p.isRunning { p.terminate() } }
    }

    // MARK: protocol

    private func send(_ obj: [String: Any]) throws {
        guard let h = stdinHandle else { throw Failure(code: "not_running", message: "worker is not running") }
        var data = try JSONSerialization.data(withJSONObject: obj)
        data.append(0x0A)
        try h.write(contentsOf: data)
    }

    private func consume(_ data: Data) {
        guard !data.isEmpty else { return }
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            guard !line.isEmpty, let obj = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] else { continue }
            let id = (obj["id"] as? String) ?? (obj["id"] as? Int).map(String.init) ?? ""
            if let prog = obj["progress"] as? [String: Any] {
                pending[id]?.progress?(prog["pct"] as? Int ?? 0, prog["note"] as? String ?? "")
                continue
            }
            guard let pend = pending.removeValue(forKey: id) else { continue }
            if obj["ok"] as? Bool == true {
                pend.continuation.resume(returning: obj["result"] as? [String: Any] ?? [:])
            } else {
                let e = obj["error"] as? [String: Any] ?? [:]
                pend.continuation.resume(throwing: Failure(code: e["code"] as? String ?? "failed", message: e["message"] as? String ?? "unknown worker error"))
            }
        }
    }

    private func request(_ method: String, params: [String: Any], progress: ((Int, String) -> Void)? = nil, timeout: TimeInterval = 600, networkAuthorized: Bool = false, exclusiveModel: String? = nil) async throws -> [String: Any] {
        nextID += 1
        let id = "r\(nextID)"
        return try await withTaskCancellationHandler(operation: {
          try Task.checkCancellation()
          return try await withCheckedThrowingContinuation { c in
            pending[id] = Pending(continuation: c, progress: progress)
            var message: [String: Any] = ["id": id, "method": method, "params": params, "network_authorized": networkAuthorized]
            if let exclusiveModel { message["exclusive_model"] = exclusiveModel }
            do { try send(message) }
            catch { pending.removeValue(forKey: id); c.resume(throwing: error); return }
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1e9))
                guard let self, let pend = self.pending.removeValue(forKey: id) else { return }
                try? self.send(["id": "cancel-\(id)", "method": "cancel", "params": ["target": id]])
                pend.continuation.resume(throwing: Failure(code: "timeout", message: "\(method) took longer than \(Int(timeout)) s"))
            }
          }
        }, onCancel: { [weak self] in
            // Cancel only this request. Keep awaiting the worker's acknowledgement
            // so a specialist's model lease cannot be released while it is running.
            Task { @MainActor [weak self] in
                guard let self, self.pending[id] != nil else { return }
                try? self.send(["id": "cancel-\(id)", "method": "cancel", "params": ["target": id]])
            }
        })
    }

    // MARK: public API

    /// Lets the worker read these files (dropped or picked by the user). Nothing else on the Mac is readable.
    func allow(_ paths: [String]) async throws {
        try await ensureRunning()
        _ = try await request("allow", params: ["paths": paths])
    }

    /// Runs one tool. `progress` is called on the main actor with (percent, note). Throws `Failure` on error.
    func call(_ tool: String, _ args: [String: Any], networkAuthorized: Bool = false, executionScope: String? = nil, progress: ((Int, String) -> Void)? = nil) async throws -> [String: Any] {
        try await ensureRunning()
        var params: [String: Any] = ["tool": tool, "args": args]
        if networkAuthorized {
            var credentials: [String: Any] = [:]
            if tool == "search_papers", let key = await Task.detached(operation: { Keychain.get("openalex") }).value { credentials["openalex"] = key }
            if ["brightdata_fetch", "brightdata_search"].contains(tool) {
                credentials["brightdata_enabled"] = UserDefaults.standard.bool(forKey: "brightdataEnabled")
                if credentials["brightdata_enabled"] as? Bool == true {
                    credentials["brightdata"] = await Task.detached(operation: { Keychain.get("brightdata") ?? "" }).value
                    credentials["brightdata_web_zone"] = UserDefaults.standard.string(forKey: "brightdataWebZone") ?? ""
                    credentials["brightdata_serp_zone"] = UserDefaults.standard.string(forKey: "brightdataSerpZone") ?? ""
                    credentials["brightdata_limit"] = UserDefaults.standard.object(forKey: "brightdataRequestLimit") as? Int ?? 8
                }
            }
            params["service_credentials"] = credentials
        }
        try Task.checkCancellation()
        if let executionScope = executionScope ?? activeObjective { params["execution_scope"] = executionScope }
        if ["forecast_series", "predict_outcome"].contains(tool) {
            let linked = !(args["linked_tables"] as? [[String: Any]] ?? []).isEmpty
            let requestedEngine = args["engine"] as? String ?? "auto"
            let relational = requestedEngine == "relational" || (requestedEngine == "auto" && linked)
            let specialist = tool == "forecast_series" ? KumoRuntime.displayName : relational ? KumoRuntime.relationalName : KumoRuntime.tabularName
            let lease = try await KumoRuntime.shared.acquire(specialist: specialist)
            let forecastWorker = process
            var safeToRelease = true
            defer { if safeToRelease { KumoRuntime.shared.release(lease) } }
            do {
                return try await request("call", params: params, progress: progress, timeout: 960, networkAuthorized: false, exclusiveModel: "kumo")
            } catch {
                // The child has a parent-death watchdog. Wait for its parent and
                // watchdog before returning the GPU/model budget on transport failure.
                if let failure = error as? Failure, ["timeout", "exited", "not_running"].contains(failure.code) {
                    // An unstructured task keeps cleanup alive even if the user's
                    // research task was cancelled. Never release while inference lives.
                    safeToRelease = await Task.detached {
                        if forecastWorker?.isRunning == true { forecastWorker?.terminate() }
                        for _ in 0..<50 where forecastWorker?.isRunning == true {
                            try? await Task.sleep(nanoseconds: 100_000_000)
                        }
                        if let owned = forecastWorker, owned.isRunning { _ = Darwin.kill(owned.processIdentifier, SIGKILL) }
                        for _ in 0..<20 where forecastWorker?.isRunning == true {
                            try? await Task.sleep(nanoseconds: 100_000_000)
                        }
                        try? await Task.sleep(nanoseconds: 1_000_000_000)
                        return forecastWorker?.isRunning != true
                    }.value
                    if !safeToRelease { KumoRuntime.shared.cleanupBlocked() }
                }
                throw error
            }
        }
        return try await request("call", params: params, progress: progress, networkAuthorized: networkAuthorized)
    }

    func cancelAll() {
        for id in pending.keys { try? send(["id": "cancel-\(id)", "method": "cancel", "params": ["target": id]]) }
    }

    func learning(_ params: [String: Any]) async throws -> [String: Any] {
        let modelLease = (params["action"] as? String == "context") ? try InferenceCoordinator.shared.beginModel() : nil
        defer { if let modelLease { InferenceCoordinator.shared.endModel(modelLease) } }
        try await ensureRunning()
        return try await request("learning", params: params, timeout: 20)
    }

    func openObjective(id: String, instruction: String, inputs: [String], userInstruction: String) async throws -> [String: Any] {
        try await ensureRunning()
        let result = try await request("objective", params: ["action": "open", "objective_id": id, "instruction": instruction, "inputs": inputs, "user_instruction": userInstruction])
        activeObjective = id
        return result
    }

    func reviewObjective(_ id: String) async throws -> [String: Any] {
        try await request("objective", params: ["action": "review", "objective_id": id])
    }

    func objectiveState(_ id: String, state: String) async throws {
        _ = try await request("objective", params: ["action": "state", "objective_id": id, "state": state])
        if activeObjective == id && state != "running" { activeObjective = nil }
    }

    /// Verify input/code/output identities without rerunning an operation on a miss.
    func cachedResult(_ tool: String, arguments: [String: Any], scope: String) async throws -> [String: Any]? {
        try await ensureRunning()
        let result = try await request("cached_result", params: ["tool": tool, "args": arguments, "execution_scope": scope])
        return result["cached_result"] as? [String: Any]
    }

    func datasets() async throws -> [[String: Any]] {
        try await inventory().datasets
    }

    /// Drop a dataset or document from the project registry (the file itself is untouched).
    /// Clear every registered dataset and document, saved outputs and caches (dry run first for the confirmation).
    func clearProject(dryRun: Bool, journal: Bool = false) async throws -> [String: Any] {
        try await ensureRunning()
        return try await request("clear_project", params: ["dry_run": dryRun, "artifacts": true, "caches": true, "journal": journal])
    }
    func forget(_ id: String) async throws -> Bool {
        try await ensureRunning()
        return try await request("forget", params: ["id": id])["forgotten"] as? Bool ?? false
    }

    /// Everything registered in the project: datasets (including cleaned copies) and documents.
    func inventory() async throws -> (datasets: [[String: Any]], documents: [[String: Any]]) {
        try await ensureRunning()
        let r = try await request("datasets", params: [:])
        return (r["datasets"] as? [[String: Any]] ?? [], r["documents"] as? [[String: Any]] ?? [])
    }

    /// Template-library management is host IPC, never a model-callable import/remove tool.
    /// Only a native file-picker selection may supply an import path.
    func templates(action: String, path: String? = nil, id: String? = nil, name: String? = nil, makeDefault: Bool? = nil) async throws -> [String: Any] {
        if action == "import", let path { try await allow([path]) }
        else { try await ensureRunning() }
        var params: [String: Any] = ["action": action]
        if let path { params["path"] = path }
        if let id { params["id"] = id }
        if let name { params["name"] = name }
        if let makeDefault { params["make_default"] = makeDefault }
        return try await request("templates", params: params)
    }
}
