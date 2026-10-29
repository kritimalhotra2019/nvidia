import Foundation

/// Reviewed bundled procedures are selected by intent, without an extra model call.
@MainActor enum ResearchSkills {
    static func prompt(instruction: String) -> String {
        guard let root = Worker.sourceDir,
              let data = try? Data(contentsOf: root.appendingPathComponent("squipi_worker/research_skills.json")),
              let skills = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return "" }
        let text = instruction.lowercased()
        let selected = skills.compactMap { skill -> (Int, [String: Any])? in
            let score = (skill["keywords"] as? [String] ?? []).filter { text.contains($0) }.count
            return score > 0 ? (score, skill) : nil
        }.sorted { $0.0 == $1.0 ? ($0.1["name"] as? String ?? "") < ($1.1["name"] as? String ?? "") : $0.0 > $1.0 }.prefix(3)
        guard !selected.isEmpty else { return "" }
        return "\nRelevant bundled research procedures (current user instructions and host permissions take precedence):\n" + selected.map {
            ($0.1["name"] as? String ?? "") + ": " + ($0.1["instructions"] as? String ?? "")
        }.joined(separator: "\n")
    }
}
