import Foundation
import Combine

/// Optional local NeMo runtime. Its SQLite journal owns exact tool-call recovery;
/// the native conversation keeps the stable job UUID across resumes and restarts.
@MainActor
final class NemoRunner: ObservableObject {
    static let shared = NemoRunner()
    @Published private(set) var installing = false
    @Published private(set) var notice = ""
    var isRunning: Bool { activeJob != nil }
    private var activeJob: URL?
    private var process: Process?
    static var source: URL {
        if let bundled = Bundle.main.url(forResource: "nemo", withExtension: nil) { return bundled }
        return URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("experiments/nemo")
    }
    static var python: URL {
        let installed = Store.shared.dir.appendingPathComponent("nemo-env/bin/python")
        if FileManager.default.isExecutableFile(atPath: installed.path) { return installed }
        // Only development binaries use the repository's already-installed environment.
        if Bundle.main.bundleURL.pathExtension != "app" { return source.appendingPathComponent(".venv/bin/python") }
        return installed
    }
    static var available: Bool {
        let marker = python.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("squipi-ready.json")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              let data = try? Data(contentsOf: marker),
              let result = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return result["ready"] as? Bool == true && result["schema"] as? Int == 1
    }
    static func jobURL(_ id: UUID) -> URL { Store.shared.dir.appendingPathComponent("research-jobs/" + id.uuidString) }

    func stop() {
        guard let activeJob else { return }
        try? Data().write(to: activeJob.appendingPathComponent("stop.requested"), options: .atomic)
    }

    /// No shell interpolation, inherited API keys or hosted telemetry.
    private func execute(_ arguments: [String], at directory: URL, bootstrap: Bool = false, healthCheck: Bool = false) async throws -> String {
        // Only `work` executes a model. Status, setup and metadata remain usable.
        let modelLease = !bootstrap && !healthCheck && arguments.first == "work"
            ? try InferenceCoordinator.shared.beginModel() : nil
        defer { if let modelLease { InferenceCoordinator.shared.endModel(modelLease) } }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let log = directory.appendingPathComponent("runtime.log")
        let result = directory.appendingPathComponent("runtime-result.json")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        FileManager.default.createFile(atPath: result.path, contents: nil)
        let output = try FileHandle(forWritingTo: result), errors = try FileHandle(forWritingTo: log)
        defer { try? output.close(); try? errors.close() }
        let p = Process()
        p.executableURL = bootstrap ? URL(fileURLWithPath: "/bin/bash") : Self.python
        p.arguments = bootstrap ? arguments : healthCheck ? [Self.source.appendingPathComponent("healthcheck.py").path] + arguments : ["-m", "squipi_nemo.cli"] + arguments
        p.currentDirectoryURL = directory
        p.environment = ["PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": NSHomeDirectory(),
            "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": Self.source.path + ":" + (Worker.sourceDir?.path ?? ""),
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"]
        p.standardOutput = output; p.standardError = errors
        process = p
        defer { process = nil }
        let code: Int32 = try await withCheckedThrowingContinuation { continuation in
            p.terminationHandler = { continuation.resume(returning: $0.terminationStatus) }
            do { try p.run() } catch { continuation.resume(throwing: error) }
        }
        let data = try Data(contentsOf: result)
        guard code == 0 else {
            let error = (try? String(contentsOf: log, encoding: .utf8)) ?? ""
            let detail = error.split(separator: "\n").last.map(String.init) ?? "No error detail was returned."
            throw Worker.Failure(code: "nemo_runtime", message: "NeMo \(bootstrap ? "setup" : healthCheck ? "runtime check" : "execution") failed. \(String(detail.prefix(350))) Details: \(log.path)")
        }
        return String(decoding: data, as: UTF8.self)
    }

    func install() async {
        guard !installing else { return }
        installing = true; notice = "Installing the local research runtime…"
        defer { installing = false }
        do {
            _ = try await execute([Self.source.appendingPathComponent("bootstrap.sh").path,
                Store.shared.dir.appendingPathComponent("nemo-env").path, Worker.sourceDir?.path ?? ""],
                at: Store.shared.dir.appendingPathComponent("nemo-setup"), bootstrap: true)
            notice = "NeMo is ready on this Mac."
        } catch { notice = error.localizedDescription }
    }

    func status(jobID: UUID) async throws -> String {
        let job = Self.jobURL(jobID)
        return try await execute(["status", job.path], at: job)
    }

    func run(jobID: UUID, objective: String, followup: String, inputs: [URL], templates: [URL]) async -> Agent.Reply {
        var reply = Agent.Reply(via: "NeMo · local Ollama", taskID: "nemo_" + jobID.uuidString, status: .partial)
        guard FileManager.default.isExecutableFile(atPath: Self.python.path) else { reply.error = "Set up NeMo in Me → Research runner, then continue this task."; reply.status = .failed; return reply }
        let job = Self.jobURL(jobID)
        activeJob = job
        defer { activeJob = nil }
        do {
            let marker = Self.python.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("squipi-ready.json")
            do {
                _ = try await execute([marker.path], at: Store.shared.dir.appendingPathComponent("nemo-runtime-check"), healthCheck: true)
            } catch {
                try? FileManager.default.removeItem(at: marker)
                notice = "NeMo needs repair. Use Set up NeMo to install and verify its dependencies."
                reply.error = notice + " " + error.localizedDescription
                reply.status = .failed
                return reply
            }
            let model = OllamaProvider()
            // The CLI validates loopback-only endpoints and rejects cloud model aliases too.
            let base = model.baseURL.hasSuffix("/v1") ? model.baseURL : model.baseURL + "/v1"
            let exists = FileManager.default.fileExists(atPath: job.appendingPathComponent("task.json").path)
            if !exists {
                var args = ["init", job.path, "--instruction", objective + SquipiLanguage.prompt + UserInstructions.shared.snapshot().promptSection]
                for input in inputs { args += ["--attach", input.path] }
                for template in templates { args += ["--template", template.path] }
                let lower = objective.lowercased()
                for (format, words) in [("xlsx", ["excel", "xlsx"]), ("docx", ["word", "docx"]), ("pptx", ["powerpoint", "pptx", "slides", "presentation"])] {
                    if words.contains(where: lower.contains) { args += ["--require", format] }
                }
                _ = try await execute(args, at: job.deletingLastPathComponent())
            }
            if Task.isCancelled { stop(); reply.status = .cancelled; return reply }
            var args = ["work", job.path, "--model", model.model, "--base-url", base,
                        "--steps", "12", "--seconds", "300", "--request-seconds", "120", "--cycles", "6", "--max-stalls", "2"]
            let resumeWords = ["continue", "resume", "proceed", "go ahead", "जारी रखें", "继续", "تابع", "continuer", "continuar"]
            if exists && !resumeWords.contains(followup.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()) { args += ["--message", followup + SquipiLanguage.prompt] }
            let raw = try await execute(args, at: job)
            guard let data = raw.data(using: .utf8), let status = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw Worker.Failure(code: "nemo_response", message: "NeMo returned an unreadable status. The saved task can be resumed.")
            }
            let completed = status["status"] as? String == "completed"
            reply.status = Task.isCancelled ? .cancelled : (completed ? .completed : .partial)
            let files = (status["outputs"] as? [String] ?? []).map { URL(fileURLWithPath: $0).resolvingSymlinksInPath() }.filter {
                $0.path.hasPrefix(job.resolvingSymlinksInPath().appendingPathComponent("project/artifacts").path + "/") && FileManager.default.fileExists(atPath: $0.path)
            }
            reply.text = status["answer"] as? String ?? (completed ? "The research task is complete." : "Progress is saved. " + (status["error"] as? String ?? "Say continue to resume this task."))
            reply.steps = [.init(tool: "nemo_research", args: [:], ok: completed, summary: "\((status["completed_tools"] as? [String] ?? []).count) saved tool steps · \(status["pause_reason"] as? String ?? status["status"] as? String ?? "paused")", outputs: files, elapsed: 0)]
            if !completed { reply.error = status["error"] as? String ?? "This task is paused; completed files and exact steps are saved." }
        } catch { reply.error = error.localizedDescription; reply.status = Task.isCancelled ? .cancelled : (FileManager.default.fileExists(atPath: job.appendingPathComponent("task.json").path) ? .partial : .failed) }
        return reply
    }
}
