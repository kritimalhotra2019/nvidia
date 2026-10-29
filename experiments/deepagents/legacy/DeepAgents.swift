import Foundation
import SwiftUI

/// Deep Agents owns planning/context. Its external tools only propose actions;
/// Agent.swift remains the sole executor, journal and approval boundary.
final class DeepAgentsProvider: ActionChatProvider, @unchecked Sendable {
    let base: any ChatProvider
    private let objective: String
    private let mode: ModelRouter.Mode
    init(base: any ChatProvider, mode: ModelRouter.Mode, objective: String) { self.base = base; self.mode = mode; self.objective = objective }
    var model: String { base.model }
    var label: String { "Deep Agents · " + base.label }
    var isLocal: Bool { true }
    func listModels(key: String) async throws -> [String] { try await base.listModels(key:key) }
    func complete(messages: [ChatMessage], tools: [[String: Any]]?, key: String, stream: Bool, temperature: Double, maxTokens: Int, think: Bool, onDelta: ((String?, String?) -> Void)?) async throws -> ModelReply {
        guard let tools, !tools.isEmpty else { return try await base.complete(messages:messages,tools:nil,key:key,stream:stream,temperature:temperature,maxTokens:maxTokens,think:think,onDelta:onDelta) }
        return try await decide(messages:messages,tools:tools,think:think,allowFinal:true)
    }
    func completeAction(messages: [ChatMessage], tools: [[String: Any]], key: String, allowFinalAnswer: Bool) async throws -> ModelReply {
        try await decide(messages:messages,tools:tools,think:false,allowFinal:allowFinalAnswer)
    }
    private func decide(messages: [ChatMessage], tools: [[String: Any]], think: Bool, allowFinal: Bool) async throws -> ModelReply {
        if mode == .mlx && !ModelRouter.transient { try await MLXRuntime.shared.ensureRunning() }
        let endpoint = mode == .mlx ? NebiusProvider(kind:.mlx).baseURL : OllamaProvider().baseURL
        let request: [String: Any] = ["provider":mode.rawValue,"model":model,"endpoint":endpoint,"objective_id":objective,"messages":messages.map(\.json),"tools":tools,"think":think,"allow_final":allowFinal]
        let result: [String: Any]
        do { result = try await DeepAgentsRuntime.shared.decide(request) }
        catch {
            let message=error.localizedDescription.lowercased()
            // Keep the existing strict action recovery for local models whose
            // native tool parser fails. The failed proposal executed nothing;
            // the recovered action still passes through the same host gates.
            if !Task.isCancelled, ["syntax", "parse tool", "invalid tool", "incomplete"].contains(where:message.contains),
               let fallback=base as? any ActionChatProvider {
                NSLog("DEEP_AGENTS using one validated native action recovery after a parser failure")
                return try await fallback.completeAction(messages:messages,tools:tools,key:"local",allowFinalAnswer:allowFinal)
            }
            throw error
        }
        let allowed = Set(tools.compactMap { ($0["function"] as? [String: Any])?["name"] as? String })
        let calls = try (result["tool_calls"] as? [[String: Any]] ?? []).map { call -> ToolCallRequest in
            guard let name = call["name"] as? String, allowed.contains(name) else { throw ProviderError.invalidResponse("Deep Agents proposed an unavailable host tool") }
            return ToolCallRequest(id:"deep_"+UUID().uuidString,name:name,arguments:try OllamaProvider.encodedToolArguments(call["arguments"]))
        }
        return ModelReply(text:result["text"] as? String ?? "",reasoning:"",toolCalls:calls,usage:[:],finishReason:result["finish_reason"] as? String)
    }
}

@MainActor final class DeepAgentsRuntime: ObservableObject {
    static let shared = DeepAgentsRuntime()
    @Published private(set) var installing = false
    @Published var notice = ""
    static var source: URL {
        Bundle.main.url(forResource:"deepagents",withExtension:nil) ?? URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("experiments/deepagents")
    }
    static var python: URL {
        if ModelRouter.transient, let p = ProcessInfo.processInfo.environment["SQUIPI_TEST_DEEPAGENTS_PYTHON"] { return URL(fileURLWithPath:p) }
        return Store.shared.dir.appendingPathComponent("deepagents-env/bin/python")
    }
    static var available: Bool { FileManager.default.isExecutableFile(atPath:python.path) && FileManager.default.fileExists(atPath:python.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("squipi-ready.json").path) }
    static var enabled: Bool {
        let wanted = ModelRouter.transient ? ProcessInfo.processInfo.environment["SQUIPI_TEST_DEEPAGENTS"] == "1" : UserDefaults.standard.object(forKey:"deepAgentsEnabled") as? Bool ?? true
        return wanted && available
    }
    private func execute(_ p: Process) async throws {
        let watchdog=Task { try await Task.sleep(nanoseconds:600_000_000_000); if p.isRunning { p.terminate() } }
        defer { watchdog.cancel() }
        try await withTaskCancellationHandler(operation: {
            let code: Int32 = try await withCheckedThrowingContinuation { c in
                p.terminationHandler = { c.resume(returning:$0.terminationStatus) }
                do { try Task.checkCancellation();try p.run();if Task.isCancelled { p.terminate() } } catch { c.resume(throwing:error) }
            }
            try Task.checkCancellation()
            guard code == 0 else { throw ProviderError.runtimeFailed("The local Deep Agents runner stopped. Completed native tool results remain saved; resume the task or switch to the standard runner.") }
        }, onCancel: { if p.isRunning { p.terminate() } })
    }
    func decide(_ request: [String: Any]) async throws -> [String: Any] {
        guard Self.available else { throw ProviderError.runtimeFailed("Set up Deep Agents in Me → Research runner first.") }
        let root = Store.shared.dir.appendingPathComponent("projects/default/deepagents")
        let temp = root.appendingPathComponent("requests/"+UUID().uuidString)
        try FileManager.default.createDirectory(at:temp,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        defer { try? FileManager.default.removeItem(at:temp) }
        let input=temp.appendingPathComponent("input.json"), output=temp.appendingPathComponent("output.json"), log=temp.appendingPathComponent("runtime.log")
        try JSONSerialization.data(withJSONObject:request).write(to:input,options:.atomic)
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:input.path)
        FileManager.default.createFile(atPath:log.path,contents:nil)
        let handle=try FileHandle(forWritingTo:log); defer { try? handle.close() }
        let p=Process(); p.executableURL=Self.python
        p.arguments=["-m","squipi_deepagents.bridge",input.path,output.path,root.path]
        p.environment=["PATH":"/usr/bin:/bin","HOME":NSHomeDirectory(),"PYTHONPATH":Self.source.path+":"+(Worker.sourceDir?.path ?? ""),"PYTHONDONTWRITEBYTECODE":"1","LANGSMITH_TRACING":"false","LANGCHAIN_TRACING_V2":"false","DO_NOT_TRACK":"1"]
        p.standardOutput=handle;p.standardError=handle
        try await execute(p)
        guard let data=try? Data(contentsOf:output), let result=try JSONSerialization.jsonObject(with:data) as? [String: Any] else { throw ProviderError.invalidResponse("Deep Agents returned no readable decision") }
        if let error=result["error"] as? String {
            if request["provider"] as? String == "mlx", error.lowercased().contains("generation thread died") { MLXRuntime.shared.markGenerationFailed() }
            throw ProviderError.runtimeFailed("Deep Agents: "+error+". Completed tool results remain saved.")
        }
        return result
    }
    func install() async {
        guard !installing else { return };installing=true;notice="Installing Deep Agents locally…";defer { installing=false }
        do {
            let uv=Bundle.main.url(forResource:"uv",withExtension:nil,subdirectory:"bin") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/uv")
            let p=Process();p.executableURL=URL(fileURLWithPath:"/bin/bash");p.arguments=[Self.source.appendingPathComponent("bootstrap.sh").path,Store.shared.dir.appendingPathComponent("deepagents-env").path,uv.path]
            p.environment=["PATH":"/usr/bin:/bin","HOME":NSHomeDirectory(),"PYTHONDONTWRITEBYTECODE":"1"]
            let log=Store.shared.dir.appendingPathComponent("deepagents-setup.log");FileManager.default.createFile(atPath:log.path,contents:nil)
            let h=try FileHandle(forWritingTo:log);defer { try? h.close() };p.standardOutput=h;p.standardError=h
            try await execute(p);notice="Deep Agents is ready for Ollama and MLX."
        } catch { notice=error.localizedDescription }
    }
}

struct DeepAgentsSettingsView: View {
    @ObservedObject private var runtime=DeepAgentsRuntime.shared
    @AppStorage("deepAgentsEnabled") private var enabled=true
    var body: some View {
        VStack(alignment:.leading,spacing:10) {
            SectionTitle(text:"Research runner")
            Toggle("Use Deep Agents for local research",isOn:$enabled).toggleStyle(.switch).controlSize(.small)
            Text("Works with both Ollama and MLX. Planning and context stay local; Squipi validates tools and asks for Mac actions. Switching this off uses the standard runner. Everyday chat keeps its fast path.").font(.caption).foregroundStyle(Color.petMuted)
            Text(DeepAgentsRuntime.available ? "Local runtime installed" : "Set up the local runtime to enable this runner").font(.caption)
            if !DeepAgentsRuntime.available { Button(runtime.installing ? "Setting up…" : "Set up Deep Agents") { Task { await runtime.install() } }.disabled(runtime.installing).buttonStyle(Pill(filled:false)) }
            if !runtime.notice.isEmpty { Text(runtime.notice).font(.caption) }
        }.card()
    }
}
