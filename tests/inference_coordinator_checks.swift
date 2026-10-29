import Foundation

private func require(_ value: @autoclosure () -> Bool, _ message: String) {
    precondition(value(), message)
}

private func expectBusy(_ operation: () throws -> UUID) {
    do { _ = try operation(); preconditionFailure("Expected an exclusive-model refusal") }
    catch is InferenceCoordinator.Busy { }
    catch { preconditionFailure("Unexpected gate error: \(error)") }
}

private final class RaceResult: @unchecked Sendable {
    private let lock = NSLock()
    private var modes = [String]()
    func add(_ mode: String) { lock.lock(); defer { lock.unlock() }; modes.append(mode) }
    func snapshot() -> [String] { lock.lock(); defer { lock.unlock() }; return modes }
}

@main struct InferenceCoordinatorChecks {
    static func main() async throws {
        let gate = InferenceCoordinator()
        let first = try gate.beginModel()
        let second = try gate.beginModel()
        expectBusy { try gate.beginForecast() }
        gate.endModel(first)
        expectBusy { try gate.beginForecast() }
        gate.endModel(UUID())
        expectBusy { try gate.beginForecast() }
        gate.endModel(second)
        let forecast = try gate.beginForecast()
        require(gate.forecasting, "Forecast lease must be visible")
        expectBusy { try gate.beginModel() }
        expectBusy { try gate.beginForecast() }
        gate.endForecast(UUID())
        require(gate.forecasting, "A foreign token must not release a forecast")
        gate.endForecast(forecast)
        require(!gate.forecasting, "The owner must release its forecast")
        let replacement = try gate.beginForecast()
        gate.endForecast(forecast)
        require(gate.forecasting, "A stale token must not release the next forecast")
        gate.endForecast(replacement)

        // Both contenders attempt admission before either releases its token.
        // Across each race exactly one mode may own the budget.
        for _ in 0..<250 {
            let attempted = DispatchGroup()
            let finished = DispatchGroup()
            let release = DispatchSemaphore(value: 0)
            let result = RaceResult()
            for mode in ["model", "forecast"] {
                attempted.enter(); finished.enter()
                DispatchQueue.global().async {
                    defer { finished.leave() }
                    let token = mode == "model" ? try? gate.beginModel() : try? gate.beginForecast()
                    if token != nil { result.add(mode) }
                    attempted.leave()
                    _ = release.wait(timeout: .now() + 5)
                    if let token {
                        if mode == "model" { gate.endModel(token) } else { gate.endForecast(token) }
                    }
                }
            }
            require(attempted.wait(timeout: .now() + 5) == .success, "Race participants did not start")
            require(result.snapshot().count == 1, "Ordinary and forecast ownership overlapped")
            release.signal(); release.signal()
            require(finished.wait(timeout: .now() + 5) == .success, "Race participants did not finish")
        }

        // A cancelled operation must release its token through the caller's defer.
        let ready = DispatchSemaphore(value: 0)
        let cancelled = Task.detached {
            let lease = try gate.beginForecast()
            defer { gate.endForecast(lease) }
            ready.signal()
            try await Task.sleep(nanoseconds: 10_000_000_000)
        }
        require(ready.wait(timeout: .now() + 5) == .success, "Cancellation fixture did not start")
        cancelled.cancel()
        do { try await cancelled.value; preconditionFailure("Cancelled task unexpectedly completed") }
        catch is CancellationError { }
        let afterCancellation = try gate.beginModel()
        gate.endModel(afterCancellation)
        print("Inference coordinator checks passed: ownership, stale tokens, 250 admission races, cancellation release. No model calls.")
    }
}
