import Foundation

/// A host-authored checkpoint, never an unlabelled model summary. Preserve speaker attribution
/// and the immediately preceding answer even when executor prompts must shed older history.
enum ConversationCheckpoint {
    static func context(_ history: [ResearchContinuation.HistoryEntry], objective: String = "", inputs: [String] = [], outputs: [String] = []) -> String {
        let recent = ResearchContinuation.boundedHistory(history, maxMessages: 6, maxCharacters: 6500)
        let records = recent.map { ["speaker": $0.role, "text": $0.text, "task_id":$0.taskID ?? "", "status":$0.status ?? ""] }
        let value: [String: Any] = ["objective": String(objective.prefix(1500)), "recent_messages": records,
                                  "source_ids":Array(inputs.prefix(12)), "output_paths":Array(outputs.filter { $0.count < 700 }.suffix(5))]
        let data = (try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])) ?? Data()
        return """
        Conversation checkpoint (attributed historical context, not tool evidence or new instructions):
        \(String(decoding: data, as: UTF8.self))
        Resolve indirect follow-ups against this discussion. Earlier assistant claims may be wrong;
        user corrections retain their speaker and scope. Retrieve exact evidence when needed.
        """
    }

    /// A question that asks for a figure from the data ("how many cases were successful", "what share of
    /// households…", "which district has the most…") is a query of the bound sources, not a discussion of the
    /// previous answer, however it starts. 28 Oct: a count question after an analysis got only the memory tools.
    static func isDataQuery(_ text: String) -> Bool {
        let t = text.lowercased()
        return t.range(of: #"\b(?:how many|how much|number of|what (?:share|percent(?:age)?|proportion|fraction|number|is the (?:average|mean|median|total|sum|count|rate))|which (?:\w+ ){0,3}(?:has|have|had|is|are|was|were) the (?:most|least|highest|lowest|largest|smallest)|per cent|percent|share of|average|median|total|count|by (?:district|state|year|month|judge|court|group|arm|gender|sex|age|region)\b)"#, options: .regularExpression) != nil
    }

    /// New work after a completed task: a request for something not yet computed or produced. Everything else that
    /// refers back to the work is conversation about it (28 Oct, user: "the model needs to be able to answer by
    /// thinking instead of always using tools, like normal chatting about what we found").
    static func asksNewWork(_ text: String) -> Bool {
        let t = text.lowercased()
        return t.range(of: #"\b(?:create|export|save|clean|remove|delete|calculate|compute|find|search|download|open|resume|continue|new task|new question|regress|compare|chart|plot|graph|visuali[sz]e|report|brief|word (?:file|doc)|excel|docx|pptx|filter|subset|rerun|re-run|redo|run (?:it|this|that|the)|do (?:it|this|that|the same|\d)|same for|analy[sz]e|anonymi|pseudonymi|redact|merge|combine|join|append|translate|rewrite|draft)\b|https?://|(?:/|~/)\S"#, options: .regularExpression) != nil
    }

    static func isDiscussion(_ text: String, hasCompletedWork: Bool) -> Bool {
        let t = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard hasCompletedWork, t.count <= 500, !isDataQuery(t), !asksNewWork(t) else { return false }
        if t.range(of: #"^(?:so\s+)?(?:is (?:it|that)|was (?:it|that)|does (?:it|that)|did (?:it|that)|what (?:does|do|about|would|are|is)|why|how|can you explain|could you explain|explain (?:it|that)|tell me more)\b"#, options: .regularExpression) != nil { return true }
        // A question after completed work that asks for no new work is a question about the work.
        if t.hasSuffix("?") { return true }
        // A follow-up that refers to the work ("these variables", "the findings", "what you found") is talk about it.
        return t.range(of: #"\b(?:these|those|this|that|it|the (?:findings?|variables?|columns?|results?|numbers?|figures?|patterns?|insights?|data|dataset|analysis|trend|correlation)|you (?:said|found|mean|wrote)|your (?:findings?|answer|analysis)|what we found|above|earlier|mean|stand for|explain|interpret|understand|simple (?:language|terms|words)|detail)\b"#, options: .regularExpression) != nil
    }
}
