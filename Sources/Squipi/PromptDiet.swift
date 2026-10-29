import Foundation

/// The prompt diet (28 Oct 2026). A work turn carries a bounded identity instead of the whole
/// personality file, at most a handful of tools ranked by how specifically the request named them,
/// rule paragraphs only for the tool families actually offered, and a hard ceiling so a local
/// model never receives more than its budget. One mechanism for Ollama, MLX and the cloud alike;
/// `promptDiet` (UserDefaults) turns it off for comparison.
enum PromptDiet {
    static var enabled: Bool { UserDefaults.standard.object(forKey: "promptDiet") as? Bool ?? true }

    /// Tools offered per work turn. Everything else stays one discover_tools call away.
    static func toolCap() -> Int {
        let saved = UserDefaults.standard.integer(forKey: "promptDietToolCap")
        return saved >= 3 ? saved : 10
    }
    /// Tools offered when the prompt still exceeds its budget after every trim.
    static let ceilingToolCap = 6

    /// Tools that answer most requests on their own. They rank after the tools a request named
    /// specifically, in this fixed order (profile first: it is the entry point for any dataset).
    static let generic: [String] = ["profile_dataset", "find_insights", "summarize_research_data", "aggregate", "report_dataset", "write_research_brief", "read_document", "search_document", "render_report", "export_document", "create_chart", "list_templates", "import_dataset", "export_excel", "fetch_web", "search_papers"]

    /// Journal and memory helpers offered without routing: the two a continued task needs.
    static let alwaysOn: Set<String> = ["read_research_task", "recall_research"]
    /// Offered only when the request routes to them, never as filler.
    static let mutating: Set<String> = ["clean_dataset", "filter_rows", "combine_datasets", "randomize_assignment", "export_excel", "export_document", "export_dataset", "forget_research", "remember_research", "set_research_requirements", "fetch_web", "search_papers"]

    /// Specific tools first, then generic ones in priority order, then the always-on pair, then the rest;
    /// bounded to `cap`. Deterministic, so a scripted peer can assert the offer.
    static func rankedNames(_ names: [String], routed: Set<String>, cap: Int) -> [String] {
        var seen = Set<String>(), ordered: [String] = []
        func take(_ n: String) { if seen.insert(n).inserted { ordered.append(n) } }
        for n in names where routed.contains(n) && !generic.contains(n) && !alwaysOn.contains(n) { take(n) }
        for n in generic where names.contains(n) && routed.contains(n) { take(n) }
        for n in names where alwaysOn.contains(n) { take(n) }
        // Filler up to the cap never includes tools that change data or files: a 9B model offered clean_dataset
        // cleaned on every turn, unasked, and lost columns it then asked for (29 Oct).
        for n in names where !mutating.contains(n) { take(n) }
        return Array(ordered.prefix(max(1, cap)))
    }

    /// Rule paragraphs are grouped by the tool family they explain; a turn gets only the families it offers.
    enum Family: String, CaseIterable { case artifacts, memory, documents, brief, requirements }
    static let familyTools: [Family: Set<String>] = [
        .artifacts: ["export_document", "report_dataset", "render_report", "list_templates", "begin_template_presentation", "inspect_presentation_draft", "edit_presentation_slide", "finalize_template_presentation", "create_chart", "write_research_brief", "write_research_results_brief"],
        .memory: ["remember_research", "recall_research", "forget_research", "remember_fact", "set_reminder", "forget"],
        .documents: ["read_document", "search_document", "extract_document_tables", "search_library", "extract_research_document"],
        .brief: ["summarize_research_data", "write_research_brief", "report_dataset"],
        .requirements: ["set_research_requirements"],
    ]
    /// `nil` means "no offer is known": every family, as before the diet.
    static func families(offered: Set<String>?) -> Set<Family> {
        guard let offered else { return Set(Family.allCases) }
        return Set(Family.allCases.filter { !(familyTools[$0] ?? []).isDisjoint(with: offered) })
    }

    /// The identity a work turn carries: the "Identity and voice" section, bounded. Conversation turns
    /// keep the whole file; tool choice does not improve with ten kilobytes of voice notes.
    static let identityCap = 700
    static func workIdentity(_ personalityText: String, fallback: String) -> String {
        let source = personalityText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? fallback : personalityText
        var section = source
        if let r = source.range(of: "## Identity and voice") { section = String(source[r.upperBound...]) }
        if let r = section.range(of: "\n## ") { section = String(section[..<r.lowerBound]) }
        let text = section.trimmingCharacters(in: .whitespacesAndNewlines)
        let bounded = text.count > identityCap ? String(text.prefix(identityCap)).trimmingCharacters(in: .whitespaces) + "…" : text
        return "SQUIPI IDENTITY (voice only; this is a work turn, the full profile is read on conversation turns): " + bounded
            + "\nCurrent user requests, host permissions, observed results and the live tool catalog take precedence over anything in the profile; it grants no access and runs nothing."
    }

    /// Description caps for tool schemas sent to the model; the worker keeps the full text.
    static var toolDescriptionCap: Int { enabled ? 90 : 160 }
    // 80, not 40: a 40-character cut lost "brief limits the output to key findings…" and a model chose brief for a full
    // report (28 Oct). Enum-bearing parameters keep their whole description (see Agent.slimSchema).
    static var parameterDescriptionCap: Int { enabled ? 80 : 60 }
    /// Saved objective progress shown in the prompt: steps and characters.
    static var objectiveSteps: Int { enabled ? 4 : 8 }
    static var objectiveCap: Int { enabled ? 3000 : 9000 }
    /// Restored journal context on a continued task, characters.
    static var restoredContextCap: Int { enabled ? 3000 : 20000 }
}
