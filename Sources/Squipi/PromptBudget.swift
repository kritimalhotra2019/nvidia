import Foundation

/// Tokens are what a local model pays for. The harness never measured them before; now every
/// turn estimates the prompt, trims it in a fixed order until it fits, and logs the estimate
/// beside the provider's reported prompt_tokens so the calibration can be checked in the log.
enum PromptBudget {
    /// The tokenizer family the current turn is calibrated against: "local" (Qwen on Ollama/MLX) or a cloud model id.
    /// Calibration learned from one tokenizer must not steer another (28 Oct: Nemotron counts pulled the local
    /// estimate to 2.37 chars/token and the local trims overshot by 40%).
    nonisolated(unsafe) static var activeFamily = "local"
    static var calibrationKey: String { "promptCharsPerToken." + activeFamily.replacingOccurrences(of: "/", with: "_") }
    /// Chars per token for mixed English prose, JSON schemas and tool results on Qwen tokenizers.
    static var charsPerToken: Double {
        let saved = UserDefaults.standard.double(forKey: calibrationKey)
        return saved > 1.5 && saved < 8 ? saved : 3.3
    }
    static func tokens(_ text: String) -> Int { max(1, Int(Double(text.utf8.count) / charsPerToken)) }
    static func tokens(tools: [[String: Any]]) -> Int {
        guard !tools.isEmpty, let data = try? JSONSerialization.data(withJSONObject: tools) else { return 0 }
        return max(1, Int(Double(data.count) / charsPerToken))
    }
    static func tokens(messages: [ChatMessage]) -> Int {
        messages.reduce(0) { $0 + tokens($1.content) + 4 + $1.toolCalls.reduce(0) { $0 + tokens($1.arguments) + tokens($1.name) } }
    }
    /// Prefill tokens/s measured from the local provider's own timings (Ollama reports prompt_eval_duration).
    nonisolated(unsafe) static var lastPrefillRate: Double? = nil
    static func observePrefill(tokens: Int, seconds: Double) {
        guard tokens > 200, seconds > 0.2 else { return }
        let rate = Double(tokens) / seconds
        lastPrefillRate = lastPrefillRate.map { $0 * 0.5 + rate * 0.5 } ?? rate
    }
    /// Kernel memory-pressure level mirrored by MemoryPressure.refresh() ("normal", "warning", "critical").
    nonisolated(unsafe) static var memoryLevel = "normal"
    /// A slow Mac (memory pressure, shared GPU) gets smaller prompts automatically.
    static var adaptiveFactor: Double {
        var factor = 1.0
        if let rate = lastPrefillRate { factor = rate < 50 ? 0.6 : (rate < 90 ? 0.75 : 1.0) }
        if memoryLevel == "critical" { factor = min(factor, 0.6) } else if memoryLevel == "warning" { factor = min(factor, 0.75) }
        return factor
    }
    /// Round-0 budgets keep the first reply fast; in-loop budgets leave room for tool results.
    static func budget(isLocal: Bool, round: Int) -> Int {
        let key = isLocal ? (round == 0 ? "promptBudgetLocalFirst" : "promptBudgetLocal") : "promptBudgetCloud"
        let saved = UserDefaults.standard.integer(forKey: key)
        if saved >= 2_000 { return isLocal ? Int(Double(saved) * adaptiveFactor) : saved }
        // Measured 25 Oct on a 16 GB M4 under memory pressure: prefill ran at 45–55 tokens/s, so
        // every extra 1K tokens is ~20 s. Keep local turns small; the journal holds the detail.
        // Cloud: 40K keeps a document turn from piling every span into one prompt (27 Oct: 50K tokens by round 8).
        return isLocal ? Int(Double(round == 0 ? 8_000 : 12_000) * adaptiveFactor) : 40_000
    }
    struct Estimate { let system: Int; let tools: Int; let messages: Int; var total: Int { system + tools + messages } }
    static func estimate(system: String, tools: [[String: Any]], messages: [ChatMessage]) -> Estimate {
        Estimate(system: tokens(system), tools: tokens(tools: tools), messages: tokens(messages: messages))
    }
    /// Learn the chars-per-token ratio from what the provider actually counted. Bounded so one
    /// odd report cannot swing later estimates; prompt caching makes Ollama under-report, so only
    /// counts at least as large as the estimate adjust the ratio.
    static func calibrate(estimatedTokens: Int, reportedPromptTokens: Int, promptCharacters: Int) {
        guard reportedPromptTokens > 500, estimatedTokens > 0, promptCharacters > 0, reportedPromptTokens >= estimatedTokens else { return }
        let observed = Double(promptCharacters) / Double(reportedPromptTokens)
        let blended = max(2.0, min(6.0, charsPerToken * 0.8 + observed * 0.2))
        UserDefaults.standard.set(blended, forKey: calibrationKey)
    }
}
