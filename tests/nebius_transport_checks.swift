import AppKit
import Foundation

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate { let brain = FixtureBrain() }
@MainActor final class FixtureBrain { func prepare() async {} }
@MainActor final class MLXRuntime { static let shared = MLXRuntime(); func stop() {} }
enum Keychain {
    static func get(_ account: String) -> String? { fatalError("Transport fixtures must not access credentials") }
    static func set(_ value: String, account: String) { fatalError("Transport fixtures must not modify credentials") }
}
enum HermesRunner {
    struct Configuration { let model = "fixture-local-model" }
    static func localConfiguration() throws -> Configuration { Configuration() }
}

@main struct NebiusTransportChecks {
    @MainActor static func main() async throws {
        let endpoint = CommandLine.arguments[1]
        let savedArguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        defer { UserDefaults.standard.setVolatileDomain(savedArguments, forName: UserDefaults.argumentDomain) }
        UserDefaults.standard.setVolatileDomain([
            "nebiusBaseURL": endpoint + "/v1", "nebiusModel": NebiusModels.kimiK3,
            "ollamaBaseURL": endpoint, "ollamaModel": "qwen3.5:9b", "mlxBaseURL": endpoint + "/v1"
        ], forName: UserDefaults.argumentDomain)
        let key = "fixture-not-a-real-key"
        var checks = 0
        func check(_ value: Bool, _ message: String) {
            checks += 1
            precondition(value, message)
        }
        let tools: [[String: Any]] = [["type": "function", "function": [
            "name": "fixture_rate", "description": "Calculate a synthetic rate",
            "parameters": ["type": "object", "properties": ["numerator": ["type": "integer"], "denominator": ["type": "integer"]], "required": ["numerator", "denominator"]]
        ]]]
        let kimi = NebiusProvider()
        check(kimi.model == NebiusModels.kimiK3, "Research provider uses selected K3")
        var messages = [ChatMessage(role: "user", content: "K3_TOOL_START")]
        var reasoningDeltas = ""
        let first = try await kimi.complete(messages: messages, tools: tools, key: key, stream: true,
            temperature: 0.64, maxTokens: 777, think: false, onDelta: { _, reasoning in reasoningDeltas += reasoning ?? "" })
        check(first.reasoning == "First reasoning: use the exact denominator.", "Streaming reasoning chunks are retained")
        check(first.reasoning == reasoningDeltas, "Streaming reasoning callbacks match the stored response")
        check(first.toolCalls.count == 1 && first.toolCalls[0].id == "call_fixture_rate", "Streaming tool identity is retained")
        let call = first.toolCalls[0]
        check(call.name == "fixture_rate" && call.arguments == #"{"numerator":2,"denominator":4}"#, "Tool arguments survive fragmented SSE")
        check(first.finishReason == "tool_calls", "A tool request stays a tool request")
        messages.append(ChatMessage(role: "assistant", content: first.text, toolCalls: first.toolCalls, reasoningContent: first.reasoning))
        messages.append(ChatMessage(role: "tool", content: #"{"rate_percent":50}"#, toolCallID: call.id, toolName: call.name))
        let second = try await kimi.complete(messages: messages, tools: tools, key: key, stream: false,
            temperature: 0.64, maxTokens: 777, think: true, onDelta: nil)
        check(second.text == "The observed rate is 50%." && second.finishReason == "stop", "Nonstreaming final answer is parsed")
        check(second.reasoning == "Second reasoning: 2 of 4 is 50%.", "Nonstreaming reasoning is retained")
        messages.append(ChatMessage(role: "assistant", content: second.text, reasoningContent: second.reasoning))
        messages.append(ChatMessage(role: "user", content: "Confirm the denominator."))
        let third = try await kimi.complete(messages: messages, tools: nil, key: key, stream: true,
            temperature: 0.64, maxTokens: 777, think: false, onDelta: nil)
        check(third.text == "The denominator is 4." && third.reasoning == "Third reasoning.", "A later streamed turn preserves and consumes prior reasoning")
        let history = [ChatMessage(role: "system", content: "Synthetic history replay."),
                       ChatMessage(role: "user", content: "Previous question."),
                       ChatMessage(role: "assistant", content: "Saved visible answer without stored reasoning."),
                       ChatMessage(role: "user", content: "HISTORY_REPLAY")]
        let historical = try await kimi.complete(messages: history, tools: nil, key: key, stream: false,
            temperature: 0.64, maxTokens: 777, think: false, onDelta: nil)
        check(historical.text == "Historical context received.", "Saved visible answers can be replayed as attributed history")
        for failure in ["reasoning-only", "length", "max_tokens", "content_filter", "refusal", "missing finish", "unexpected tool call"] {
            do {
                _ = try await kimi.testConnection(key: key)
                preconditionFailure("Connection test accepted \(failure) as success")
            } catch ProviderError.emptyCompletion {
                checks += 1
            }
        }
        let connected = try await kimi.testConnection(key: key)
        check(connected.hasPrefix("Connected.") && connected.contains(NebiusModels.kimiK3) && connected.contains("ready"),
              "Only a completed visible handshake reports connection success")

        let nemotron = NebiusProvider(modelOverride: NebiusModels.nemotron)
        check(nemotron.model == NebiusModels.nemotron && UserDefaults.standard.string(forKey: "nebiusModel") == NebiusModels.kimiK3,
              "Explicit meeting/provider override never changes the saved research selection")
        let n = try await nemotron.complete(messages: [ChatMessage(role: "user", content: "NEMOTRON_UNCHANGED")], tools: tools,
            key: key, stream: false, temperature: 0.27, maxTokens: 333, think: false, onDelta: nil)
        check(n.text == "Nemotron unchanged.", "Regular NVIDIA transport still completes")

        let localMessage = ChatMessage(role: "assistant", content: "Prior local answer.", reasoningContent: "CLOUD_REASONING_MUST_NOT_LEAK")
        check(localMessage.ollamaJSON["reasoning_content"] == nil, "Ollama serialization does not inherit cloud reasoning fields")
        let o = try await OllamaProvider().complete(messages: [localMessage, ChatMessage(role: "user", content: "OLLAMA_UNCHANGED")], tools: tools,
            key: key, stream: false, temperature: 0.27, maxTokens: 333, think: false, onDelta: nil)
        check(o.text == "Ollama unchanged.", "Native Ollama transport still completes")
        let m = try await NebiusProvider(kind: .mlx).complete(messages: [ChatMessage(role: "user", content: "MLX_UNCHANGED")], tools: nil,
            key: key, stream: false, temperature: 0.27, maxTokens: 333, think: false, onDelta: nil)
        check(m.text == "MLX unchanged.", "MLX transport keeps its own model and settings")
        print("Nebius transport checks passed: \(checks) Swift assertions; fifteen controlled completions and eight catalog reads; no real models or credentials.")
    }
}
