import AppKit
import Foundation

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate { let brain = FixtureBrain() }
@MainActor final class FixtureBrain { func prepare() async {} }
enum Keychain {
    static func get(_ account: String) -> String? { fatalError("Health fixtures must not access credentials") }
    static func set(_ value: String, account: String) { fatalError("Health fixtures must not modify credentials") }
}
enum HermesRunner {
    struct Configuration { let model = "fixture-local-model" }
    static func localConfiguration() throws -> Configuration { Configuration() }
}

@main struct MLXHealthChecks {
    @MainActor static func main() async throws {
        let endpoint = CommandLine.arguments[1]
        let directory = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
        let savedArguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        defer { UserDefaults.standard.setVolatileDomain(savedArguments, forName: UserDefaults.argumentDomain) }
        UserDefaults.standard.setVolatileDomain(["mlxBaseURL": endpoint + "/v1"], forName: UserDefaults.argumentDomain)
        ModelRouter.transient = true // Never launch or stop a real inference process.
        var checks = 0
        func check(_ value: Bool, _ message: String) {
            checks += 1
            precondition(value, message)
        }
        let startup = "2026-10-25 18:09:05,303 - INFO - Starting httpd at 127.0.0.1 on port 8766...\n"
        let crash = "2026-10-25 18:12:55,321 - ERROR - mlx_lm.server generation thread died: [METAL] Insufficient Memory.\nRuntimeError: generation thread died\n"
        let laterStartup = "2026-10-25 18:20:00,000 - INFO - Starting httpd at 127.0.0.1 on port 8766...\n"
        let modelRead = "127.0.0.1 - - [25/Oct/2026 18:13:22] \"GET /v1/models HTTP/1.1\" 200 -\n"
        check(MLXRuntime.generationFailureMarker(in: startup + modelRead) == nil, "A healthy startup is not a crash")
        check(MLXRuntime.generationFailureMarker(in: startup + crash) != nil, "Current generation death is detected")
        check(MLXRuntime.generationFailureMarker(in: startup + crash + modelRead) != nil, "/models liveness cannot mask generation death")
        check(MLXRuntime.generationFailureMarker(in: startup + crash + laterStartup + modelRead) == nil, "A later server startup supersedes an old crash")
        check(MLXRuntime.generationFailureMarker(in: "RuntimeError: generation thread died\n") != nil, "A traceback remains detectable when the startup is outside the bounded tail")
        check(MLXRuntime.generationFailureMarker(in: "some unrelated error\n") == nil, "Unrelated errors are not generation death")
        check(MLXRuntime.generationFailureMarker(in: startup + crash)?.contains("18:12:55") == true, "The timestamped death is retained rather than overwritten by the generic traceback")

        let current = directory.appendingPathComponent("mlx-server.log")
        let previous = directory.appendingPathComponent("mlx-server.previous.log")
        try (startup + crash).write(to: previous, atomically: true, encoding: .utf8)
        try (laterStartup + modelRead).write(to: current, atomically: true, encoding: .utf8)
        let healthy = MLXRuntime(logURL: current)
        await healthy.refresh()
        check(healthy.status == "Local MLX server is ready", "Previous rotated crash logs do not mark the current server dead")

        try (startup + crash + modelRead).write(to: current, atomically: true, encoding: .utf8)
        let dead = MLXRuntime(logURL: current)
        await dead.refresh()
        check(dead.status.contains("generation stopped"), "GUI refresh notices a failure observed by a separate CLI")
        try await dead.ensureRunning() // Controlled one-token HTTP peer; no process launch.
        check(dead.status == "Local MLX server is ready", "Successful external generation can recover without terminating a process")
        await dead.refresh()
        check(dead.status == "Local MLX server is ready", "The already superseded external crash does not re-poison health")

        let secondCrash = "2026-10-25 18:25:00,000 - ERROR - mlx_lm.server generation thread died: [METAL] Insufficient Memory.\nRuntimeError: generation thread died\n"
        try (startup + crash + modelRead + secondCrash).write(to: current, atomically: true, encoding: .utf8)
        await dead.refresh()
        check(dead.status.contains("generation stopped"), "A new death is detected after an earlier successful external probe")

        try (startup + crash + laterStartup + modelRead).write(to: current, atomically: true, encoding: .utf8)
        let restarted = MLXRuntime(logURL: current)
        await restarted.refresh()
        check(restarted.status == "Local MLX server is ready", "A current log containing an old crash followed by startup remains healthy")

        let provider = MLXProvider()
        let completed = try await provider.complete(messages: [ChatMessage(role: "user", content: "COMPLETE")], tools: nil,
            key: "local", stream: true, temperature: 0, maxTokens: 8, think: false, onDelta: nil)
        check(completed.text == "ready" && completed.finishReason == "stop", "Complete SSE still succeeds")
        check(!MLXRuntime.shared.status.contains("generation stopped"), "Healthy completion does not poison shared runtime")
        do {
            _ = try await provider.complete(messages: [ChatMessage(role: "user", content: "TRUNCATED")], tools: nil,
                key: "local", stream: true, temperature: 0, maxTokens: 8, think: false, onDelta: nil)
            preconditionFailure("A truncated tool response was accepted")
        } catch ProviderError.invalidResponse {
            checks += 1
        }
        check(!MLXRuntime.shared.status.contains("generation stopped"), "An ordinary output cap is distinct from server death")
        do {
            _ = try await provider.complete(messages: [ChatMessage(role: "user", content: "EOF")], tools: nil,
                key: "local", stream: true, temperature: 0, maxTokens: 8, think: false, onDelta: nil)
            preconditionFailure("An incomplete SSE tool response was accepted")
        } catch ProviderError.runtimeFailed(let message) {
            check(message.contains("before a completion marker"), "Premature EOF remains a fail-closed runtime error")
        }
        check(MLXRuntime.shared.status.contains("generation stopped"), "Actual provider EOF marks runtime unhealthy, including transient CLI callers")

        // A fresh app retains the old crash log, but has no Process handle. A
        // closed endpoint must reach normal startup, not an external-owner error.
        try (startup + crash).write(to: current, atomically: true, encoding: .utf8)
        UserDefaults.standard.setVolatileDomain(["mlxBaseURL": CommandLine.arguments[3] + "/v1"], forName: UserDefaults.argumentDomain)
        let noServer = MLXRuntime(logURL: current, installationAvailable: { false })
        do {
            try await noServer.ensureRunning()
            preconditionFailure("An unavailable installation started a process")
        } catch ProviderError.network(let message) {
            check(message.contains("Install the isolated MLX runtime"), "Stale crash with no listener reaches the normal installation/startup branch")
        }

        UserDefaults.standard.setVolatileDomain(["mlxBaseURL": CommandLine.arguments[4] + "/v1"], forName: UserDefaults.argumentDomain)
        let externalDead = MLXRuntime(logURL: current, installationAvailable: { false })
        do {
            try await externalDead.ensureRunning()
            preconditionFailure("A failed externally owned server was replaced")
        } catch ProviderError.runtimeFailed(let message) {
            check(message.contains("externally launched MLX server"), "A responding dead external server retains ownership protection")
        }
        print("MLX health checks passed: \(checks) assertions; controlled local HTTP only, no model or GPU.")
    }
}
