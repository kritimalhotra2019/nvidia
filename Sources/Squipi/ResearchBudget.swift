import Foundation

/// Research has no automatic wall-clock cutoff. Only a deadline requested by the
/// user reserves synthesis time and limits the current turn.
struct ResearchBudget {
    let seconds: TimeInterval?
    init(instruction: String) {
        // Require a deadline instruction at a clause boundary, not a duration
        // mentioned in source descriptions (for example, travel in 10 minutes).
        let pattern = #"(?i)(?:^|[.!?;\n])\s*(?:(?:please|actually|can you|could you)\s*[, :]?\s*)?(?:(?:finish|complete|stop|work|run|read|summarize|summarise|analyse|analyze|respond|answer)(?:\s+(?:this|it|the (?:task|analysis|report|work|summary|run)))?\s+)?(?:within|in|under|at most|no more than)\s+(\d{1,3})\s*(?:minutes?|mins?)\b"#
        let regex = try? NSRegularExpression(pattern: pattern)
        let match = regex?.matches(in: instruction, range: NSRange(instruction.startIndex..., in: instruction)).last
        let minutes = match.flatMap { Range($0.range(at: 1), in: instruction) }.flatMap { Int(instruction[$0]) }
        seconds = minutes.flatMap { $0 > 0 ? TimeInterval($0 * 60) : nil }
    }
    var synthesisReserve: TimeInterval { seconds.map { min(180, max(20, $0 * 0.2)) } ?? 0 }
    func deadline(startingAt start: Date = Date()) -> Date? { seconds.map { start.addingTimeInterval($0) } }
    static func hasExpired(_ deadline: Date?, now: Date = Date()) -> Bool { deadline.map { now >= $0 } ?? false }
    var notice: String {
        guard let seconds else { return "Research progress is saved for continuation." }
        return "This run reached the \(Int(seconds / 60))-minute deadline in your request. Its source cursors and research steps are saved for continuation."
    }
}

private struct BudgetReply: @unchecked Sendable { let reply: ModelReply }

@MainActor enum ModelBudget {
    static func call(until deadline: Date?, operation: @escaping @MainActor () async throws -> ModelReply) async throws -> ModelReply {
        try Task.checkCancellation()
        guard let deadline else {
            let reply = try await operation()
            try Task.checkCancellation()
            return reply
        }
        let remaining = deadline.timeIntervalSinceNow
        guard remaining > 0 else { throw Worker.Failure(code: "reading_budget", message: "The requested deadline for this research phase was reached; use saved notes and disclose incomplete coverage.") }
        return try await withThrowingTaskGroup(of: BudgetReply.self) { group in
            group.addTask { BudgetReply(reply: try await operation()) }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
                throw Worker.Failure(code: "reading_budget", message: "The requested deadline for this research phase was reached. Earlier completed steps and notes remain saved.")
            }
            defer { group.cancelAll() }
            return try await group.next()!.reply
        }
    }
}
