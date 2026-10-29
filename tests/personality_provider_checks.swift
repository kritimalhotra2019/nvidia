import AppKit
import Foundation

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate { let brain = FixtureBrain() }
@MainActor final class FixtureBrain { func prepare() async {} }
enum Keychain { static func get(_ account: String) -> String? { nil }; static func set(_ value: String, account: String) {} }
enum HermesRunner {
    struct Configuration { let model = "fixture-local-model" }
    static func localConfiguration() throws -> Configuration { Configuration() }
}

@main struct PersonalityProviderChecks {
    @MainActor static func main() async throws {
        let endpoint = CommandLine.arguments[1]
        let source = try String(contentsOfFile: CommandLine.arguments[2], encoding: .utf8)
        ModelRouter.transient = true
        UserDefaults.standard.setVolatileDomain(["ollamaBaseURL":endpoint,"mlxBaseURL":endpoint+"/v1","nebiusBaseURL":endpoint+"/v1","nebiusModel":"fixture-nebius"],forName:UserDefaults.argumentDomain)
        let providers: [any ChatProvider] = [OllamaProvider(),MLXProvider(),NebiusProvider()]
        for provider in providers {
            let identity = SquipiPersonality.prompt(source,detail:.directory) + SquipiPersonality.runtime(label:provider.label,model:provider.model,isLocal:provider.isLocal)
            let answer = try await provider.complete(messages:[ChatMessage(role:"system",content:identity),ChatMessage(role:"user",content:"Who are you?")],tools:nil,key:"fixture-not-a-real-key",stream:false,temperature:0,maxTokens:64,think:false,onDelta:nil)
            precondition(answer.text.contains("Squipi"))
            print("Identity transported through \(provider.label)")
        }
    }
}
