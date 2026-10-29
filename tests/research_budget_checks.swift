import Foundation
struct ModelReply { var text: String }
enum Worker { struct Failure: Error { let code: String; let message: String } }
@main struct BudgetChecks {
    @MainActor static func main() async throws {
        precondition(ResearchBudget(instruction:"Summarize within 10mins").seconds == 600)
        precondition(ResearchBudget(instruction:"Read within 10 minutes. Actually, within 2 minutes.").seconds == 120)
        precondition(ResearchBudget(instruction:"Please finish the report within 90 minutes.").seconds == 5400)
        precondition(ResearchBudget(instruction:"Analyse households reaching school in 10 minutes.").seconds == nil)
        precondition(ResearchBudget(instruction:"Do not stop after 30 minutes.").seconds == nil)
        precondition(ResearchBudget(instruction:"The previous request was 'finish within 5 minutes'. Continue without a deadline.").seconds == nil)
        let unlimited = ResearchBudget(instruction:"Analyse this dataset")
        precondition(unlimited.seconds == nil)
        let start = Date(timeIntervalSince1970: 0)
        precondition(unlimited.deadline(startingAt: start) == nil)
        precondition(!ResearchBudget.hasExpired(unlimited.deadline(startingAt: start), now: start.addingTimeInterval(86_400)))
        precondition(unlimited.synthesisReserve == 0)
        precondition(ResearchBudget(instruction:"within 1 min").synthesisReserve == 20)
        let requested = ResearchBudget(instruction:"within 100 minutes")
        precondition(requested.seconds == 6000)
        precondition(!ResearchBudget.hasExpired(requested.deadline(startingAt: start), now: start.addingTimeInterval(1801)))
        precondition(ResearchBudget.hasExpired(requested.deadline(startingAt: start), now: start.addingTimeInterval(6000)))
        precondition(ResearchBudget(instruction:"within 0 minutes").seconds == nil)
        let unrestricted = try await ModelBudget.call(until: nil) { ModelReply(text:"no automatic deadline") }
        precondition(unrestricted.text == "no automatic deadline")
        let first = try await ModelBudget.call(until: Date().addingTimeInterval(1)) { ModelReply(text:"done") }
        precondition(first.text == "done")
        let started=Date()
        do {
            _ = try await ModelBudget.call(until: Date().addingTimeInterval(0.05)) {
                try await Task.sleep(nanoseconds: 2_000_000_000)
                return ModelReply(text:"too late")
            }
            preconditionFailure("Deadline did not cancel the slow generation")
        } catch {
            precondition(Date().timeIntervalSince(started) < 0.8)
            precondition((error as? Worker.Failure)?.code == "reading_budget")
        }
        var expiredOperationRan = false
        do {
            _ = try await ModelBudget.call(until: Date().addingTimeInterval(-1)) {
                expiredOperationRan = true
                return ModelReply(text:"must not run")
            }
            preconditionFailure("Expired explicit deadline started an operation")
        } catch { precondition((error as? Worker.Failure)?.code == "reading_budget") }
        precondition(!expiredOperationRan)

        var operationStarted = false
        var operationCancelled = false
        let running = Task { @MainActor in
            try await ModelBudget.call(until: nil) {
                operationStarted = true
                do { try await Task.sleep(nanoseconds: 5_000_000_000) }
                catch { operationCancelled = true; throw error }
                return ModelReply(text:"must not finish")
            }
        }
        try await Task.sleep(nanoseconds: 50_000_000)
        precondition(operationStarted)
        running.cancel()
        do { _ = try await running.value; preconditionFailure("Stop did not cancel unrestricted work") }
        catch is CancellationError { }
        precondition(operationCancelled)

        var cancelledOperationRan = false
        let cancelled = Task { @MainActor in
            try await ModelBudget.call(until: nil) {
                cancelledOperationRan = true
                return ModelReply(text:"must not start")
            }
        }
        cancelled.cancel()
        do { _ = try await cancelled.value; preconditionFailure("Pre-cancelled work started") }
        catch is CancellationError { }
        precondition(!cancelledOperationRan)
        print("Research deadline checks passed: no automatic cutoff, requested deadlines, expiry and Stop cancellation")
    }
}
