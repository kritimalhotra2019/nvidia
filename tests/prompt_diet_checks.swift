import Foundation

@main struct PromptDietChecks {
    static func main() {
        // Ranking: specific routed tools first, then generic in priority order, then always-on, bounded.
        let names = ["profile_dataset", "summarize_research_data", "difference_in_differences", "recall_research", "read_research_task", "estimate_treatment_effect", "find_insights", "attrition_check", "write_research_brief", "render_report", "export_document", "list_templates", "fit_fixed_effects_model", "combine_datasets"]
        let routed: Set<String> = ["profile_dataset", "summarize_research_data", "difference_in_differences", "estimate_treatment_effect", "find_insights", "attrition_check", "write_research_brief", "render_report", "export_document", "list_templates", "fit_fixed_effects_model", "combine_datasets"]
        let ranked = PromptDiet.rankedNames(names, routed: routed, cap: 10)
        precondition(ranked.count == 10, "cap holds: \(ranked)")
        precondition(ranked.prefix(5).allSatisfy { !PromptDiet.generic.contains($0) }, "specific tools first: \(ranked)")
        precondition(ranked[0] == "difference_in_differences" && ranked[1] == "estimate_treatment_effect", "routing order kept: \(ranked)")
        precondition(ranked.contains("profile_dataset") && ranked.contains("find_insights"), "entry-point generic tools survive: \(ranked)")
        precondition(!ranked.contains("list_templates"), "low-priority generic dropped at the cap: \(ranked)")
        precondition(PromptDiet.rankedNames(["a", "b"], routed: [], cap: 10) == ["a", "b"], "unrouted names keep their order")
        precondition(PromptDiet.rankedNames([], routed: [], cap: 10).isEmpty)
        precondition(PromptDiet.rankedNames(["recall_research", "read_research_task", "x"], routed: ["x"], cap: 2) == ["x", "recall_research"], "always-on after specific")

        // Families: paragraphs follow the offer; nil offer keeps everything.
        precondition(PromptDiet.families(offered: nil) == Set(PromptDiet.Family.allCases))
        precondition(PromptDiet.families(offered: ["profile_dataset", "find_insights"]).isEmpty, "a plain analysis turn carries no artifact, memory, document or brief paragraph")
        precondition(PromptDiet.families(offered: ["read_document"]) == [.documents])
        precondition(PromptDiet.families(offered: ["report_dataset"]) == [.artifacts, .brief])
        precondition(PromptDiet.families(offered: ["set_reminder", "set_research_requirements"]) == [.memory, .requirements])

        // Work identity: bounded, carries the voice section, never the directory.
        let profile = "# Squipi\n\n## Identity and voice\nYou are Squipi, a female flapjack-octopus research companion. " + String(repeating: "Warm, curious, sharp. ", count: 80) + "\n\n## Working style\nLots of style notes.\n\n## Tool and skill directory\n- tool_one\n- tool_two\n"
        let identity = PromptDiet.workIdentity(profile, fallback: "fallback text")
        precondition(identity.contains("flapjack-octopus"), "voice kept")
        precondition(!identity.contains("tool_one") && !identity.contains("style notes"), "directory and style dropped")
        precondition(identity.count < PromptDiet.identityCap + 400, "bounded: \(identity.count)")
        precondition(PromptDiet.workIdentity("   ", fallback: "fallback text").contains("fallback text"), "empty profile uses the fallback")
        precondition(PromptDiet.workIdentity("no headings here at all", fallback: "f").contains("no headings here"), "a profile without headings is used whole")
        precondition(SquipiPersonality.prompt(profile, detail: .work).hasPrefix("SQUIPI IDENTITY"), "the work detail routes to the diet")
        precondition(SquipiPersonality.prompt(profile, detail: .core).contains("style notes"), "the core detail keeps the full profile")
        print("prompt diet checks: ok")
    }
}
