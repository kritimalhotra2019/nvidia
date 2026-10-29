import Foundation

@main struct Checks {
    static func main() {
        let name = "squipi-supported-setup-test-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        for mode in ["local", "hermes", "ollama", "unknown", "nebius", "mlx"] {
            defaults.set(mode, forKey: "modelMode")
            defaults.set("nemotron-3-nano:4b", forKey: "ollamaModel")
            defaults.set(true, forKey: "nemoResearchEnabled")
            defaults.set(true, forKey: "deepAgentsEnabled")
            defaults.set("lime", forKey: "squipiTheme")
            defaults.set("keep-cloud-model", forKey: "nebiusModel")
            SupportedResearchSetup.apply(to: defaults)
            precondition(defaults.string(forKey: "modelMode") == (["nebius", "mlx"].contains(mode) ? mode : "ollama"))
            precondition(defaults.string(forKey: "ollamaModel") == "qwen3.5:9b")
            precondition(!defaults.bool(forKey: "nemoResearchEnabled"))
            precondition(!defaults.bool(forKey: "deepAgentsEnabled"))
            precondition(defaults.string(forKey: "squipiTheme") == "lime")
            precondition(defaults.string(forKey: "nebiusModel") == "keep-cloud-model")
        }
        print("36 supported-setup migration checks passed")
    }
}
