import AppKit
import Foundation

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    let brain = FixtureBrain()
}
@MainActor final class FixtureBrain { func prepare() async {} }
enum Keychain {
    static func get(_ account: String) -> String? { nil }
    static func set(_ value: String, account: String) {}
}
enum HermesRunner {
    struct Configuration { let model = "fixture-local-model" }
    static func localConfiguration() throws -> Configuration { Configuration() }
}

@main struct OllamaActionChecks {
    @MainActor static func main() throws {
        var checks = 0
        let dead = ProviderError.serverFailure(code: 404, message: #"{"error":"generation thread died"}"#, mlx: true)
        precondition(dead.localizedDescription.contains("generation worker stopped"))
        precondition(!dead.localizedDescription.contains("could not find"))
        if case .http(404, _) = ProviderError.serverFailure(code: 404, message: "unknown model", mlx: true) {} else { fatalError("Real missing model error changed") }
        if case .http(404, _) = ProviderError.serverFailure(code: 404, message: "generation thread died", mlx: false) {} else { fatalError("Non-MLX error misclassified") }

        func check(_ condition: Bool, _ message: String) {
            checks += 1
            if !condition { fatalError(message) }
        }
        func reply(_ text: String, finish: String? = "stop") -> ModelReply {
            ModelReply(text: text, reasoning: "", toolCalls: [], usage: [:], finishReason: finish)
        }
        func reject(_ text: String, finish: String? = "stop") {
            do {
                _ = try OllamaProvider.actionReply(reply(text, finish: finish), allowedTools: ["clean_dataset", "profile_dataset"])
                fatalError("Accepted malformed or unoffered action")
            } catch { checks += 1 }
        }
        do {
            _ = try OllamaProvider.actionReply(reply(" \n"), allowedTools: ["profile_dataset"])
            fatalError("Empty action response became executable")
        } catch ProviderError.emptyReply { checks += 1 }
        let action = try OllamaProvider.actionReply(reply(#"{"tool":"clean_dataset","arguments":{"dataset_id":"fixture","pii":"keep","drop_columns":["name","phone","email"],"steps":[]}}"#), allowedTools: ["clean_dataset"])
        check(action.text.isEmpty && action.toolCalls.count == 1 && action.finishReason == "tool_calls", "A valid action becomes one structured call, not a claim of execution")
        let call = action.toolCalls[0]
        let arguments = try JSONSerialization.jsonObject(with: Data(call.arguments.utf8)) as! [String: Any]
        check(call.name == "clean_dataset" && arguments["pii"] as? String == "keep", "User-selected PII policy survives unchanged")
        check(arguments["drop_columns"] as? [String] == ["name", "phone", "email"] && (arguments["steps"] as? [Any])?.isEmpty == true, "No cleaning action or additional column removal is invented")
        let blocked = try OllamaProvider.actionReply(reply(#"{"blocked":"The requested file has not been attached."}"#), allowedTools: ["profile_dataset"])
        check(blocked.toolCalls.isEmpty && blocked.finishReason == "blocked" && blocked.text.contains("not been attached"), "A blocked response remains explicit and cannot masquerade as completed work")
        let answered = try OllamaProvider.actionReply(reply(#"{"answer":"The Word report is saved at the observed output path."}"#), allowedTools: ["profile_dataset"])
        check(answered.toolCalls.isEmpty && answered.finishReason == "stop" && answered.text.hasPrefix("The Word report"), "A final answer remains available after the host verifies the requested work")
        for malformed in ["not JSON", "[]", #"{"tool":"delete_everything","arguments":{}}"#,
            #"{"tool":"profile_dataset","arguments":"{}"}"#, #"{"tool":"profile_dataset","arguments":null}"#,
            #"{"tool":"profile_dataset","arguments":[]}"#, #"{"tool":"profile_dataset"}"#,
            #"{"tool":"profile_dataset","arguments":{},"blocked":"maybe"}"#, #"{"blocked":" "}"#,
            #"{"blocked":"missing","arguments":{}}"#, #"{"tool":"profile_dataset","arguments":{},"executed":true}"#,
            #"{"answer":" "}"#, #"{"answer":"done","tool":"profile_dataset","arguments":{}}"#] { reject(malformed) }
        for finish: String? in [nil, "length", "error", "tool_calls"] { reject(#"{"tool":"profile_dataset","arguments":{}}"#, finish: finish) }
        let encodedObject = try OllamaProvider.encodedToolArguments(["dataset_id": "fixture", "columns": ["income", "gender"]])
        let encodedString = try OllamaProvider.encodedToolArguments(#"{"dataset_id":"fixture","columns":["income","gender"]}"#)
        check(encodedObject == encodedString, "Native object arguments and a serialized object preserve the same values")
        for invalid: Any? in [nil, NSNull(), "not JSON", "[]", "null", "42", 42, ["income", "gender"]] {
            do { _ = try OllamaProvider.encodedToolArguments(invalid); fatalError("Malformed arguments became an empty object") }
            catch { checks += 1 }
        }
        let required = OllamaProvider.actionSchema(allowedTools: ["finalize_template_presentation"], allowFinalAnswer: false)
        let requiredVariants = required["oneOf"] as! [[String: Any]]
        check(requiredVariants.count == 2 && !String(describing: requiredVariants).contains("answer"), "Missing outputs remove the answer branch rather than resampling the same prose")
        do {
            _ = try OllamaProvider.actionReply(reply(#"{"answer":"Edit slide 3 and finalize"}"#), allowedTools: ["finalize_template_presentation"], allowFinalAnswer: false)
            fatalError("A premature final answer was accepted")
        } catch { checks += 1 }
        let schema = OllamaProvider.actionSchema(allowedTools: ["profile_dataset", "clean_dataset"])
        let variants = schema["oneOf"] as! [[String: Any]]
        let properties = variants[0]["properties"] as! [String: [String: Any]]
        check(properties["tool"]?["enum"] as? [String] == ["profile_dataset", "clean_dataset"], "The schema constrains selection to offered tool names")
        check(properties["arguments"]?["type"] as? String == "object", "Action arguments are always an object")
        print("PASS: \(checks) Ollama action parsing checks; no model or network calls")
    }
}

@MainActor final class MLXRuntime { static let shared = MLXRuntime(); func stop() {} }
