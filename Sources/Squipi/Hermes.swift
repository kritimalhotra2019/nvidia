import AppKit
import Foundation

/// Nous Research's Hermes Agent as an alternative harness. Squipi's tools reach it over MCP (`squipi_worker.mcp`,
/// configured in a dedicated local profile), and each turn is one quiet `hermes chat` query restricted to that toolset.
/// Both the harness and model run on this Mac; the user's global Hermes provider is never inherited.
@MainActor
final class HermesRunner {
    static let shared = HermesRunner()
    static let binary = NSHomeDirectory() + "/.local/bin/hermes"
    static var isInstalled: Bool { FileManager.default.isExecutableFile(atPath: binary) }
    static var profileHome: URL { Store.shared.dir.appendingPathComponent("hermes-local", isDirectory: true) }
    private(set) var running: Process?
    private var activityMonitor: Task<Void, Never>?
    private var activityMonitorID: UUID?

    struct Outcome { var text: String; var error: String?; var seconds: Double }

    static func localConfiguration() throws -> HermesLocalConfiguration {
        let defaults = UserDefaults.standard
        let override = defaults.string(forKey: "hermesModel")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return try HermesLocalConfiguration(
            baseURL: defaults.string(forKey: "ollamaBaseURL").flatMap { $0.isEmpty ? nil : $0 } ?? OllamaProvider.defaultBaseURL,
            model: override.isEmpty ? (defaults.string(forKey: "ollamaModel").flatMap { $0.isEmpty ? nil : $0 } ?? OllamaProvider.defaultModel) : override)
    }

    func isRegistered() async -> Bool {
        FileManager.default.fileExists(atPath: Self.profileHome.appendingPathComponent("config.yaml").path)
    }

    /// This profile is app-owned. Personal ~/.hermes configuration, sessions and credentials stay untouched.
    private func prepareProfile(_ configuration: HermesLocalConfiguration) throws {
        guard let source = Worker.sourceDir else { throw HermesLocalConfiguration.Failure(message: "Worker source not found.") }
        guard Worker.shared.isInstalled else { throw HermesLocalConfiguration.Failure(message: "Set up the local analysis worker in Me first.") }
        let home = Self.profileHome
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let settings = configuration.profile(workerSource: source, python: Worker.shared.python,
            project: Worker.shared.projectDir, allowNetwork: UserDefaults.standard.bool(forKey: "hermesAllowNetwork"))
        // JSON is valid YAML and avoids interpreting model/path characters as YAML syntax.
        try JSONSerialization.data(withJSONObject: settings, options: [.prettyPrinted, .sortedKeys])
            .write(to: home.appendingPathComponent("config.yaml"), options: .atomic)
    }

    /// Set up Squipi's local profile and verify the model without making an inference request.
    func register() async -> String {
        do {
            let configuration = try Self.localConfiguration()
            try await validateLocalModel(configuration)
            try prepareProfile(configuration)
            return "Ready: Hermes uses \(configuration.model) on this Mac and Squipi's tools."
        } catch { return "Could not set up local Hermes: \(error.localizedDescription)" }
    }

    /// One turn. Chat reads observed tool outcomes from this task's journal after the harness exits.
    func ask(_ prompt: String, configuration: HermesLocalConfiguration, researchTaskID: String? = nil, templateDefaults: [String: String] = [:],
             onActivity: ((String) -> Void)? = nil) async -> Outcome {
        let t0 = Date()
        do {
            try await validateLocalModel(configuration)
            try Task.checkCancellation()
            try prepareProfile(configuration)
        } catch { return Outcome(text: "", error: error.localizedDescription, seconds: Date().timeIntervalSince(t0)) }
        let activityID = UUID()
        var monitor: Task<Void, Never>?
        if let taskID = researchTaskID, let onActivity {
            let auditURL = Worker.shared.projectDir.appendingPathComponent("audit.jsonl")
            var cursor = AuditActivityCursor(taskID: taskID, file: auditURL)
            monitor = Task { @MainActor in
                var readingUntil: Date?
                while !Task.isCancelled {
                    do { try await Task.sleep(nanoseconds: 750_000_000) } catch { break }
                    guard !Task.isCancelled else { break }
                    if let state = try? cursor.readNewActivity(from: auditURL) {
                        onActivity(state)
                        readingUntil = state == "reading" ? Date().addingTimeInterval(4) : nil
                    } else if let until = readingUntil, Date() >= until {
                        onActivity("analysing"); readingUntil = nil
                    }
                }
            }
            activityMonitor?.cancel()
            activityMonitor = monitor; activityMonitorID = activityID
        }
        defer {
            monitor?.cancel()
            if activityMonitorID == activityID { activityMonitor = nil; activityMonitorID = nil }
        }
        let args = [Self.binary] + configuration.invocationArguments(prompt: prompt)
        let r = await Self.run(args, configuration: configuration, timeout: HermesLocalConfiguration.processTimeoutSeconds, researchTaskID: researchTaskID, templateDefaults: templateDefaults)
        monitor?.cancel()
        if let monitor { await monitor.value }
        if !Task.isCancelled { onActivity?("analysing") }
        let secs = Date().timeIntervalSince(t0)
        Mind.shared.reload()          // reminders and facts Hermes saved through the MCP server
        Task { await Analysis.shared.refreshInventory() }
        NSLog("HERMES turn: %.0fs, status %d", secs, r.status)
        return Self.makeOutcome(r, seconds: secs)
    }

    static func makeOutcome(_ r: RunResult, seconds: Double) -> Outcome {
        let output = HermesOutput.parse(r.stdout)
        for diagnostic in output.diagnostics { NSLog("HERMES diagnostic: %@", diagnostic) }
        let text = output.reply
        let errorLine = r.stderr.split(separator: "\n").map(String.init).last { !$0.hasPrefix("session_id:") }
        let err: String? = r.status == 0 && !text.isEmpty ? nil : (errorLine ?? "Hermes exited with \(r.status) without a final answer.")
        return Outcome(text: text, error: err, seconds: seconds)
    }

    func cancel() {
        activityMonitor?.cancel(); activityMonitor = nil; activityMonitorID = nil
        running?.terminate(); running = nil
    }

    /// Observe new audit metadata only: never reload historical tool results or show a reading
    /// cue for an attachment alone. A completed read means Hermes is receiving source text;
    /// the UI shows a brief cue, then returns to its working state.
    struct AuditActivityCursor {
        let taskID: String
        private(set) var offset: UInt64
        private var pending = Data()

        init(taskID: String, file: URL) {
            self.taskID = taskID
            offset = ((try? FileManager.default.attributesOfItem(atPath: file.path)[.size]) as? NSNumber)?.uint64Value ?? 0
        }

        mutating func readNewActivity(from file: URL) throws -> String? {
            let handle = try FileHandle(forReadingFrom: file)
            defer { try? handle.close() }
            let size = try handle.seekToEnd()
            if size < offset { offset = 0; pending.removeAll(keepingCapacity: true) }
            try handle.seek(toOffset: offset)
            guard let next = try handle.read(upToCount: 65_536), !next.isEmpty else { return nil }
            offset += UInt64(next.count)
            pending.append(next)
            var activity: String?
            while let newline = pending.firstIndex(of: 10) {
                let line = Data(pending[..<newline])
                pending.removeSubrange(...newline)
                guard let event = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any],
                      event["research_task_id"] as? String == taskID,
                      let tool = event["tool"] as? String else { continue }
                if ["read_document", "search_document"].contains(tool) { activity = "reading" }
                else if !["start_research_task", "record_research_step", "finish_research_task", "read_research_task", "recall_research"].contains(tool) { activity = "analysing" }
            }
            // A malformed or unexpectedly large audit line must not grow UI memory unboundedly.
            if pending.count > 65_536 { pending.removeAll(keepingCapacity: true) }
            return activity
        }
    }

    private final class NoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(nil)
        }
    }

    private func validateLocalModel(_ configuration: HermesLocalConfiguration) async throws {
        let sessionConfig = URLSessionConfiguration.ephemeral
        sessionConfig.timeoutIntervalForRequest = 15
        sessionConfig.connectionProxyDictionary = [:]
        let session = URLSession(configuration: sessionConfig, delegate: NoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: URL(string: configuration.ollamaURL + "/api/show")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["model": configuration.model])
        let data: Data, response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch { throw HermesLocalConfiguration.Failure(message: "Cannot reach local Ollama. Start Ollama and check Me → Ollama, then try again.") }
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw HermesLocalConfiguration.Failure(message: "Local Ollama could not find \(configuration.model). Choose an installed model in Me → Ollama.")
        }
        guard let metadata = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw HermesLocalConfiguration.Failure(message: "Local Ollama returned invalid model details.")
        }
        try HermesLocalConfiguration.validateModelMetadata(metadata)
    }

    struct RunResult { var stdout: String; var stderr: String; var status: Int32 }

    private final class DataBox: @unchecked Sendable {
        private var data = Data(); private let lock = NSLock()
        func append(_ d: Data) { lock.lock(); data.append(d); lock.unlock() }
        var string: String { lock.lock(); defer { lock.unlock() }; return String(decoding: data, as: UTF8.self) }
    }

    private static func run(_ args: [String], configuration: HermesLocalConfiguration, timeout: TimeInterval, researchTaskID: String? = nil, templateDefaults: [String: String] = [:]) async -> RunResult {
        let modelLease: UUID
        do { modelLease = try InferenceCoordinator.shared.beginModel() }
        catch { return RunResult(stdout: "", stderr: error.localizedDescription, status: -1) }
        defer { InferenceCoordinator.shared.endModel(modelLease) }
        return await withCheckedContinuation { c in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: args[0])
            p.arguments = Array(args.dropFirst())
            p.currentDirectoryURL = Worker.shared.projectDir
            p.environment = configuration.environment(inheriting: ProcessInfo.processInfo.environment,
                home: Self.profileHome, researchTaskID: researchTaskID)
            // Propagate the same per-turn template defaults to the local MCP child.
            let defaultsData = (try? JSONSerialization.data(withJSONObject: templateDefaults, options: [.sortedKeys])) ?? Data("{}".utf8)
            p.environment?["SQUIPI_TEMPLATE_DEFAULTS"] = String(decoding: defaultsData, as: UTF8.self)
            let out = Pipe(), err = Pipe()
            p.standardOutput = out; p.standardError = err
            p.standardInput = FileHandle.nullDevice
            let outData = DataBox(), errData = DataBox()
            out.fileHandleForReading.readabilityHandler = { h in outData.append(h.availableData) }
            err.fileHandleForReading.readabilityHandler = { h in errData.append(h.availableData) }
            let timer = DispatchWorkItem { if p.isRunning { p.terminate() } }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: timer)
            p.terminationHandler = { proc in
                timer.cancel()
                out.fileHandleForReading.readabilityHandler = nil; err.fileHandleForReading.readabilityHandler = nil
                outData.append(out.fileHandleForReading.readDataToEndOfFile()); errData.append(err.fileHandleForReading.readDataToEndOfFile())
                c.resume(returning: RunResult(stdout: outData.string, stderr: errData.string, status: proc.terminationStatus))
            }
            do { try p.run(); Task { @MainActor in HermesRunner.shared.running = p } }
            catch {
                timer.cancel()
                out.fileHandleForReading.readabilityHandler = nil; err.fileHandleForReading.readabilityHandler = nil
                c.resume(returning: RunResult(stdout: "", stderr: error.localizedDescription, status: -1))
            }
        }
    }
}
