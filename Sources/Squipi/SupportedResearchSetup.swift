import Foundation

/// Select the native research harness and migrate retired experimental settings.
enum SupportedResearchSetup {
    static let localModel = "qwen3.5:9b"

    static func apply(to defaults: UserDefaults) {
        if !["nebius", "mlx"].contains(defaults.string(forKey: "modelMode") ?? "") {
            defaults.set("ollama", forKey: "modelMode")
        }
        defaults.set(localModel, forKey: "ollamaModel")
        defaults.set(false, forKey: "nemoResearchEnabled")
        defaults.set(false, forKey: "deepAgentsEnabled")
    }
}
