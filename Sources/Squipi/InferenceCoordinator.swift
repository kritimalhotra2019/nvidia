import Foundation

/// Process-wide ownership: the forecast subprocess has the model budget to itself.
/// Tokens are released synchronously, including on cancellation and thrown errors.
final class InferenceCoordinator: @unchecked Sendable {
    static let shared = InferenceCoordinator()
    private let lock = NSLock()
    private var requests = Set<UUID>()
    private var exclusive: UUID?

    struct Busy: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    var forecasting: Bool {
        lock.lock(); defer { lock.unlock() }
        return exclusive != nil
    }

    func beginModel() throws -> UUID {
        lock.lock(); defer { lock.unlock() }
        guard exclusive == nil else {
            throw Busy(message: "A Kumo specialist is working. Other model requests are unavailable until it finishes; retry this request afterward.")
        }
        let token = UUID(); requests.insert(token); return token
    }

    func endModel(_ token: UUID) {
        lock.lock(); defer { lock.unlock() }; requests.remove(token)
    }

    func beginForecast() throws -> UUID {
        lock.lock(); defer { lock.unlock() }
        guard exclusive == nil, requests.isEmpty else {
            throw Busy(message: "Another model request is still running. Let it finish before starting Kumo; no forecast was started.")
        }
        let token = UUID(); exclusive = token; return token
    }

    func endForecast(_ token: UUID) {
        lock.lock(); defer { lock.unlock() }
        if exclusive == token { exclusive = nil }
    }
}
