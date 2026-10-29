import Foundation

@main struct NebiusModelChecks {
    static func main() throws {
        var checks = 0
        func check(_ value: Bool, _ explanation: String) {
            checks += 1
            precondition(value, explanation)
        }
        let previous = ["nebiusModel": NebiusModels.lightning,
                        "nebiusBaseURL": "https://fixture.invalid/v1",
                        "modelMode": "ollama", "summaryEngine": "apple",
                        "unrelatedPreference": "preserve-me"]
        let selected = NebiusModels.selectingResearchModel(NebiusModels.kimiK3, in: previous)
        check(selected["nebiusModel"] == "moonshotai/Kimi-K3", "K3 uses the canonical hosted ID")
        check(selected["nebiusMeetingModel"] == NebiusModels.lightning, "A research model change keeps the prior NVIDIA meeting writer")
        for key in ["nebiusBaseURL", "modelMode", "summaryEngine", "unrelatedPreference"] {
            check(selected[key] == previous[key], "Preset selection must preserve \(key)")
        }
        let reopened = try JSONDecoder().decode([String: String].self, from: JSONEncoder().encode(selected))
        check(reopened == selected, "Independent research and meeting selections survive serialized persistence")
        check(NebiusModels.meetingModel(saved: reopened["nebiusMeetingModel"], researchModel: reopened["nebiusModel"]!) == NebiusModels.lightning,
              "Reopening K3 does not turn the meeting writer into K3")
        let custom = "another-provider/custom-research-v2"
        let customSelected = NebiusModels.selectingResearchModel(custom, in: reopened)
        check(customSelected["nebiusModel"] == custom, "Custom research IDs remain editable and unchanged")
        check(NebiusModels.preset(for: custom) == nil && NebiusModels.displayName(for: custom) == custom, "An unknown model must not masquerade as NVIDIA or Kimi")
        check(customSelected["nebiusMeetingModel"] == NebiusModels.lightning, "A later custom selection preserves the explicit meeting model")
        let selectedAgain = NebiusModels.selectingResearchModel(NebiusModels.nemotron, in: customSelected)
        check(selectedAgain["nebiusModel"] == NebiusModels.nemotron && selectedAgain["nebiusMeetingModel"] == NebiusModels.lightning,
              "Switching back to Nemotron keeps independently configured meetings")
        check(NebiusModels.meetingModel(saved: nil, researchModel: NebiusModels.kimiK3) == NebiusModels.nemotron,
              "A fresh K3 setup retains NVIDIA as the cloud meeting writer")
        let customMeeting = "nvidia/Nemotron-custom-deployment"
        check(NebiusModels.meetingModel(saved: customMeeting, researchModel: NebiusModels.kimiK3) == customMeeting,
              "An explicitly saved meeting deployment is not rewritten")
        let beforeDiscovery = customSelected
        let available = NebiusModels.featuredAvailableModels([NebiusModels.kimiK3, NebiusModels.nemotron, NebiusModels.kimiK3, custom, "vendor/Nemotron-spoof"])
        check(available == [NebiusModels.kimiK3, NebiusModels.nemotron].sorted(), "Featured discovery includes K3 and NVIDIA without duplicates or misleading vendors")
        check(customSelected == beforeDiscovery, "Account discovery never overwrites a custom selection")
        check(NebiusModels.meetingPresets.allSatisfy { NebiusModels.isNemotron($0.id) } && !NebiusModels.meetingPresets.contains { $0.id == NebiusModels.kimiK3 },
              "Meeting presets respect the Apple/NVIDIA restriction")
        check(NebiusModels.displayName(for: NebiusModels.kimiK3) == "Moonshot Kimi K3", "K3 is labelled with the correct developer")
        print("Nebius model selection checks passed: \(checks) assertions; pure settings, no credentials, network or user preferences.")
    }
}
