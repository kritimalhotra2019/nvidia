import Foundation

@MainActor final class Store {
    static let shared = Store()
    let dir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("squipi-hermes-fixture")
}
@MainActor final class Worker {
    static let shared = Worker()
    static let sourceDir: URL? = URL(fileURLWithPath: "/fixture/worker")
    let python = URL(fileURLWithPath: "/fixture/python")
    let projectDir = URL(fileURLWithPath: "/fixture/project")
    var isInstalled: Bool { true }
}
enum OllamaProvider {
    static let defaultBaseURL = "http://localhost:11434"
    static let defaultModel = "qwen3.5:9b"
}
@MainActor final class Mind { static let shared = Mind(); func reload() {} }
@MainActor final class Analysis { static let shared = Analysis(); func refreshInventory() async {} }

@main struct HermesLocalChecks {
    @MainActor static func main() throws {
        var checks = 0
        func check(_ condition: Bool, _ message: String) {
            checks += 1
            if !condition { fatalError(message) }
        }
        let local = try HermesLocalConfiguration(baseURL: "http://localhost:11434/", model: "qwen3.5:9b")
        let auditDir = FileManager.default.temporaryDirectory.appendingPathComponent("squipi-activity-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: auditDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: auditDir) }
        let audit = auditDir.appendingPathComponent("audit.jsonl")
        try Data("{\"tool\":\"read_document\",\"research_task_id\":\"current\"}\n".utf8).write(to: audit)
        var activity = HermesRunner.AuditActivityCursor(taskID: "current", file: audit)
        check(try activity.readNewActivity(from: audit) == nil, "Historical reads must not animate a new run")
        func appendAudit(_ text: String) throws {
            let file = try FileHandle(forWritingTo: audit); defer { try? file.close() }
            try file.seekToEnd(); try file.write(contentsOf: Data(text.utf8))
        }
        try appendAudit("{\"tool\":\"read_document\",\"research_task_id\":\"another\"}\n")
        check(try activity.readNewActivity(from: audit) == nil, "Another task's reads must not change this pet's activity")
        try appendAudit("{\"tool\":\"read_document\",\"research_task_id\":\"current\"")
        check(try activity.readNewActivity(from: audit) == nil, "Partial audit writes must wait for a complete record")
        try appendAudit("}\n")
        check(try activity.readNewActivity(from: audit) == "reading", "A new observed source read should show reading")
        check(try activity.readNewActivity(from: audit) == nil, "An audit record must only be consumed once")
        try appendAudit("{\"tool\":\"search_document\",\"research_task_id\":\"current\"}\n")
        check(try activity.readNewActivity(from: audit) == "reading", "Searching document text is a reading activity")
        try appendAudit("{\"tool\":\"aggregate\",\"research_task_id\":\"current\"}\n")
        check(try activity.readNewActivity(from: audit) == "analysing", "A later calculation should return the pet to working")
        let beforeBoundedRead = activity.offset
        try appendAudit(String(repeating: "x", count: 140_000))
        _ = try activity.readNewActivity(from: audit)
        check(activity.offset - beforeBoundedRead <= 65_536, "Each activity poll must read a bounded increment")
        let notice = HermesOutput.scannerNotice
        let clean = HermesOutput.parse("  \(notice)\nN rows = 3.\n")
        check(clean.reply == "N rows = 3." && clean.diagnostics.count == 1, "Only the known leading notice moves to diagnostics")
        check(HermesOutput.parse("An answer\n\(notice)").reply == "An answer\n\(notice)", "The same text inside an answer must remain")
        check(HermesOutput.parse("Warning: something else\n3 rows").reply == "Warning: something else\n3 rows", "Unknown stdout warnings must not be silently discarded")
        check(HermesOutput.parse("\(notice) changed\n3 rows").diagnostics.isEmpty, "Partial banner matches must remain visible")
        let bannerOnly = HermesRunner.makeOutcome(.init(stdout: "  \(notice)\n", stderr: "session_id: fixture\n", status: 0), seconds: 1)
        check(bannerOnly.text.isEmpty && bannerOnly.error != nil, "A startup banner alone is never a successful answer")
        let answered = HermesRunner.makeOutcome(.init(stdout: "  \(notice)\n3 rows\n", stderr: "session_id: fixture\n", status: 0), seconds: 1)
        check(answered.text == "3 rows" && answered.error == nil, "An actual answer survives cleanup and session metadata")
        check(local.baseURL == "http://localhost:11434/v1", "Ollama must use its compatibility endpoint")
        check(local.ollamaURL == "http://localhost:11434", "Metadata must use native Ollama API")
        let prompt = "Count rows only; $(never execute) `literal`"
        let invocation = local.invocationArguments(prompt: prompt)
        check(invocation == ["chat", "-Q", "-q", HermesLocalConfiguration.documentReadingGuidance + "\n\n" + prompt,
            "--max-turns", String(HermesLocalConfiguration.maxTurns), "--run-budget", String(HermesLocalConfiguration.runBudgetSeconds),
            "--reasoning", "none", "--provider", "custom", "-m", "qwen3.5:9b", "-t", "squipi", "--no-restore-cwd"],
              "Use the quiet CLI path and document traversal protocol, preserving the user prompt as one argument")
        let shortInvocation = local.invocationArguments(prompt: prompt, maxTurns: 3, runBudgetSeconds: 180)
        check(shortInvocation[5] == "3" && shortInvocation[7] == "180", "Short test runs can override both execution ceilings")
        check(HermesLocalConfiguration.processTimeoutSeconds > TimeInterval(HermesLocalConfiguration.runBudgetSeconds),
              "The process timeout must leave room for Hermes to report the budget boundary")
        for value in ["http://127.0.0.1:11434/v1/", "http://[::1]:11434", "https://localhost:11434/v1"] {
            _ = try HermesLocalConfiguration(baseURL: value, model: "qwen3.5:9b"); checks += 1
        }
        for value in ["https://api.tokenfactory.nebius.com/v1", "https://ollama.com", "http://localhost.example/v1",
                      "http://127.0.0.2/v1", "http://localhost:11434/v1?route=cloud", "http://user@localhost:11434",
                      "file:///tmp/model", "http://localhost:11434/proxy/cloud"] {
            do { _ = try HermesLocalConfiguration(baseURL: value, model: "qwen3.5:9b"); fatalError("Accepted non-local route") }
            catch { checks += 1 }
        }
        for model in ["qwen3.5:cloud", "qwen3.5:397b-cloud", "moa:remote", "https://some.model", ""] {
            do { _ = try HermesLocalConfiguration(baseURL: local.baseURL, model: model); fatalError("Accepted remote model") }
            catch { checks += 1 }
        }
        try HermesLocalConfiguration.validateModelMetadata(["capabilities": ["completion", "tools"],
            "model_info": ["general.architecture": "qwen35", "qwen35.context_length": 262144]]); checks += 1
        for metadata: [String: Any] in [["remote_host": "https://ollama.com"], ["remote_model": "cloud-model"], ["capabilities": ["completion"]],
            ["model_info": ["general.architecture": "small", "small.context_length": 32768]]] {
            do { try HermesLocalConfiguration.validateModelMetadata(metadata); fatalError("Accepted remote alias or incapable model") }
            catch { checks += 1 }
        }
        let project = URL(fileURLWithPath: "/fixture/a project")
        let profile = local.profile(workerSource: URL(fileURLWithPath: "/fixture/worker"), python: URL(fileURLWithPath: "/fixture/python"), project: project, allowNetwork: false)
        let serialized = try JSONSerialization.data(withJSONObject: profile)
        let saved = try JSONSerialization.jsonObject(with: serialized) as! [String: Any]
        let model = saved["model"] as! [String: Any]
        check(model["provider"] as? String == "custom" && model["base_url"] as? String == local.baseURL, "Model route must be explicitly local")
        check(model["context_length"] as? Int == 65536 && model["ollama_num_ctx"] as? Int == 65536,
              "Hermes minimum must be met by the actual Ollama context allocation, not only metadata")
        check((saved["fallback_providers"] as? [Any])?.isEmpty == true && saved["fallback_model"] is NSNull, "No cloud fallback may be inherited")
        check(((saved["tools"] as? [String: Any])?["tool_search"] as? [String: String])?["enabled"] == "off", "Local models receive direct Squipi tool schemas")
        check((saved["agent"] as? [String: Any])?["max_turns"] as? Int == HermesLocalConfiguration.maxTurns,
              "CLI and persisted profile must agree on the research iteration budget")
        let compression = saved["compression"] as! [String: Bool]
        check(compression["enabled"] == true && compression["abort_on_summary_failure"] == true,
              "Long research can compact locally, but failed compression cannot silently discard source evidence")
        for route in (saved["auxiliary"] as! [String: [String: Any]]).values {
            check(route["base_url"] as? String == local.baseURL && route["model"] as? String == local.model, "Auxiliary inference must also remain local")
        }
        let server = (saved["mcp_servers"] as! [String: [String: Any]])["squipi"]!
        check((server["env"] as? [String: String])?["PYTHONDONTWRITEBYTECODE"] == "1", "MCP imports must not modify the signed bundle")
        let args = server["args"] as! [String]
        check(server["lazy"] as? Bool == false, "The app must discover current worker schemas rather than reuse a stale cached document API")
        check(server["supports_parallel_tool_calls"] as? Bool == false, "MCP paging stays sequential within the bounded per-turn result budget")
        check(!args.contains("--allow-network") && args.contains(project.path), "Network tools default off and project paths stay intact")
        check((server["env"] as! [String: String])["SQUIPI_RESEARCH_TASK_ID"] == "${SQUIPI_RESEARCH_TASK_ID}", "Journal task identity must reach the worker")
        let web = local.profile(workerSource: URL(fileURLWithPath: "/fixture/worker"), python: URL(fileURLWithPath: "/fixture/python"), project: project, allowNetwork: true)
        let webServer = (web["mcp_servers"] as! [String: [String: Any]])["squipi"]!
        check((webServer["args"] as! [String]).contains("--allow-network"), "Explicit literature permission must reach the worker")
        let environment = local.environment(inheriting: ["HOME": "/fixture/home", "NEBIUS_API_KEY": "secret-fixture", "OPENAI_API_KEY": "secret-fixture", "HERMES_HOME": "/remote/profile", "HERMES_PROFILE": "cloud", "HTTPS_PROXY": "https://proxy.invalid", "HERMES_INFERENCE_PROVIDER": "nebius", "SQUIPI_RESEARCH_TASK_ID": "stale"], home: project, researchTaskID: "current")
        check(environment["NEBIUS_API_KEY"] == nil && environment["OPENAI_API_KEY"] == "ollama", "Cloud credentials must not reach Hermes")
        check(environment["HERMES_HOME"] == project.path && environment["HERMES_PROFILE"] == nil, "Personal profile must not be inherited")
        check(environment["HTTPS_PROXY"] == nil && environment["OPENAI_BASE_URL"] == local.baseURL, "Proxy and inherited endpoint must not override local route")
        check(environment["HERMES_INFERENCE_PROVIDER"] == "custom" && environment["HERMES_INFERENCE_MODEL"] == local.model, "Inference environment must agree with profile")
        check(environment["SQUIPI_RESEARCH_TASK_ID"] == "current", "Current task must replace stale journal ID")
        check(environment["PYTHONDONTWRITEBYTECODE"] == "1", "Hermes must not write caches into bundled Python source")
        check(local.environment(inheriting: environment, home: project, researchTaskID: nil)["SQUIPI_RESEARCH_TASK_ID"] == nil, "No task must not inherit a stale journal ID")
        if CommandLine.arguments.count > 1 {
            try serialized.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
            let args = local.invocationArguments(prompt: "__SQUIPI_SMOKE_PROMPT__", maxTurns: 3, runBudgetSeconds: 180)
            try JSONSerialization.data(withJSONObject: args).write(to: URL(fileURLWithPath: CommandLine.arguments[1] + ".arguments.json"))
        }
        print("PASS: \(checks) local Hermes routing checks; no inference or external network calls")
    }
}
