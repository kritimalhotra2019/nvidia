import Foundation

/// Display and selection rules only. Choosing a preset does not change the endpoint, API key,
/// active brain or an existing custom model until the user explicitly selects it.
enum NebiusModels {
    struct Preset: Identifiable, Equatable {
        let id: String
        let label: String
    }

    static let nemotron = "nvidia/Nemotron-3.5-Super-VL"
    static let lightning = "nvidia/Nemotron-3_5-Lightning"
    static let ultra = "nvidia/Nemotron-3-Ultra-550b-a55b"      // served by the account on 28 Oct 2026 (GET /models)
    static let kimiK3 = "moonshotai/Kimi-K3"
    static let presets: [Preset] = [
        Preset(id: nemotron, label: "NVIDIA Nemotron 3.5 Super VL"),
        Preset(id: lightning, label: "NVIDIA Nemotron 3.5 Lightning"),
        Preset(id: ultra, label: "NVIDIA Nemotron 3 Ultra (550B)"),
        Preset(id: kimiK3, label: "Moonshot Kimi K3")
    ]
    static var meetingPresets: [Preset] { presets.filter { isNemotron($0.id) } }

    static func preset(for model: String) -> Preset? {
        presets.first { $0.id == model.trimmingCharacters(in: .whitespacesAndNewlines) }
    }
    static func displayName(for model: String) -> String {
        preset(for: model)?.label ?? model.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    static func isNemotron(_ model: String) -> Bool {
        let value = model.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return value.hasPrefix("nvidia/") && value.contains("nemotron")
    }
    static func isKimiK3(_ model: String) -> Bool {
        model.trimmingCharacters(in: .whitespacesAndNewlines).caseInsensitiveCompare(kimiK3) == .orderedSame
    }

    /// An unset meeting preference keeps the former Nemotron selection when possible.
    /// Kimi is a research brain; meeting writing remains Apple Intelligence or NVIDIA Nemotron.
    static func meetingModel(saved: String?, researchModel: String) -> String {
        if let saved, !saved.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return saved }
        return isNemotron(researchModel) ? researchModel : nemotron
    }

    /// Explicit research-model changes preserve the previous meeting writer instead of silently
    /// switching it alongside the chat brain. Other settings and custom IDs survive unchanged.
    static func selectingResearchModel(_ model: String, in settings: [String: String]) -> [String: String] {
        var updated = settings
        if settings["nebiusMeetingModel"]?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty != false {
            updated["nebiusMeetingModel"] = meetingModel(saved: nil, researchModel: settings["nebiusModel"] ?? nemotron)
        }
        updated["nebiusModel"] = model
        return updated
    }

    /// Account discovery is advisory. An unknown, manually entered ID is never rewritten.
    static func featuredAvailableModels(_ available: [String]) -> [String] {
        Array(Set(available.filter { isNemotron($0) || isKimiK3($0) })).sorted()
    }
}
