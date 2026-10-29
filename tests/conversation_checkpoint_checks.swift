import Foundation

@main struct CheckpointChecks {
    static func main() {
        var checked = 0
        func check(_ value: Bool, _ message: String) {
            precondition(value, message); checked += 1
        }
        let history = [ResearchContinuation.HistoryEntry(role: "user", text: "Summarize the evaluation."),
                       .init(role: "squipi", text: "The transfer helped during the acute phase; some gains faded."),
                       .init(role: "user", text: "Actually, food security gains faded by Round 3.")]
        let checkpoint = ConversationCheckpoint.context(history, objective: "Review the evaluation")
        check(checkpoint.contains("food security gains faded"), "A correction survives compaction")
        check(checkpoint.contains("speaker\":\"user"), "A user correction keeps its speaker")
        check(checkpoint.contains("speaker\":\"squipi"), "An assistant interpretation is not relabelled user knowledge")
        check(checkpoint.contains("Review the evaluation"), "The original objective survives compaction")
        for text in ["is it worth it then", "why did that happen?", "what about the limitations?", "can you explain that?"] {
            check(ConversationCheckpoint.isDiscussion(text, hasCompletedWork: true), "Completed research supports follow-ups")
            check(!ConversationCheckpoint.isDiscussion(text, hasCompletedWork: false), "No invented completed-work context")
        }
        for text in ["create another report", "calculate the rate", "search current GDP", "top countries by GDP in 2026", "open Downloads"] {
            check(!ConversationCheckpoint.isDiscussion(text, hasCompletedWork: true), "Actions and unrelated topics do not become old research discussion")
        }
        // 28 Oct: a figure asked of the data after an analysis is a query of the sources, not a discussion of the answer.
        for text in ["how many adverse possession cases were successful", "what share of households were food insecure?", "which district has the most cases", "how much did spending rise by year"] {
            check(ConversationCheckpoint.isDataQuery(text), "a count, share or ranking question is a data query: \(text)")
            check(!ConversationCheckpoint.isDiscussion(text, hasCompletedWork: true), "a data query is not discussion: \(text)")
        }
        check(!ConversationCheckpoint.isDataQuery("why did that happen?"), "a why question is not a data query")
        for text in ["but you need to tell me what these vars stand for", "explain to me in detail what these variables mean, and the findings in detail",
                     "explain these findings to me by also explaining what these variables mean and what that means in simple language", "so the decline is real then?"] {
            check(ConversationCheckpoint.isDiscussion(text, hasCompletedWork: true), "talk about the findings is discussion: \(text)")
        }
        for text in ["yes do 1 and 2", "now compare treatment and control on food security", "make a chart of the trend", "produce the full report with all findings", "analyze the Cases sheet"] {
            check(!ConversationCheckpoint.isDiscussion(text, hasCompletedWork: true), "new work is not discussion: \(text)")
        }
        let long = Array(repeating: ResearchContinuation.HistoryEntry(role:"user", text:String(repeating:"old ",count:1000)),count:20)
        let compact = ConversationCheckpoint.context(long + history, objective: "Preserve corrections")
        check(compact.count < 10000 && compact.contains("food security gains faded"), "Bounded context retains the newest correction")
        let provenance = ConversationCheckpoint.context([.init(role:"squipi",text:"Saved answer",taskID:"task_recent",status:"completed")], inputs:["doc_current"], outputs:["/tmp/current.docx"])
        check(provenance.contains("task_recent") && provenance.contains("completed"), "Checkpoint preserves exact journal linkage")
        check(provenance.contains("doc_current") && provenance.contains("current.docx"), "Checkpoint preserves source and output handles")
        print("Conversation checkpoint checks passed: \(checked) assertions")
    }
}
