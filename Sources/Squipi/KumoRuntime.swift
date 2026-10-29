import AppKit
import Foundation
import SwiftUI

@MainActor
final class KumoRuntime: ObservableObject {
    static let shared = KumoRuntime()
    static let displayName = "NVIDIA Kumo for Forecast"
    static let relationalName = "NVIDIA Kumo Relational"
    static let tabularName = "NVIDIA Kumo Tabular Small"
    @Published private(set) var status = "Optional local forecasting model. Check setup before use."
    @Published private(set) var settingUp = false
    @Published private(set) var forecasting = false
    @Published private(set) var predictionStatus = "Check the shared local Relational and Tabular setup before use."
    private var setupProcess: Process?
    static var root: URL { Store.shared.dir.appendingPathComponent("kumo", isDirectory: true) }
    static var config: URL { root.appendingPathComponent("config.json") }
    static var predictionConfig: URL { root.appendingPathComponent("prediction-config.json") }

    func check() async {
        do {
            let result = try await Worker.shared.call("forecast_capabilities", [:])
            status = result["summary_text"] as? String ?? String(describing: result)
        } catch { status = error.localizedDescription }
    }

    func checkPredictions() async {
        do {
            let result = try await Worker.shared.call("prediction_capabilities", [:])
            predictionStatus = result["summary_text"] as? String ?? String(describing: result)
        } catch { predictionStatus = error.localizedDescription }
    }

    func install() async {
        guard !settingUp, !forecasting, !Conversation.shared.busy, !Analysis.shared.busy else {
            status = "Finish the current task before setting up Kumo."; return
        }
        guard let source = Worker.sourceDir,
              let uv = Bundle.main.url(forResource: "uv", withExtension: nil, subdirectory: "bin") else {
            status = "The bundled Kumo installer is unavailable."; return
        }
        let script = source.appendingPathComponent("bootstrap-kumo.sh")
        guard FileManager.default.fileExists(atPath: script.path) else { status = "The Kumo installer is missing from this build."; return }
        settingUp = true; defer { settingUp = false; setupProcess = nil }
        status = "Setting up Kumo in its own environment; downloading the required weights…"
        let process = Process(); process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [script.path, Store.shared.dir.path, uv.path]
        process.environment = ["PATH": "/usr/bin:/bin", "HOME": NSHomeDirectory(), "LANG": "en_US.UTF-8"]
        let pipe = Pipe(); process.standardOutput = pipe; process.standardError = pipe
        let last = LineBox()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let value = String(decoding: handle.availableData, as: UTF8.self)
            if let line = value.split(separator: "\n").last, !line.isEmpty {
                let note = String(line.suffix(300)); last.set(note)
                Task { @MainActor in self?.status = note }
            }
        }
        setupProcess = process
        let code: Int32 = await withCheckedContinuation { continuation in
            process.terminationHandler = { continuation.resume(returning: $0.terminationStatus) }
            do { try process.run() } catch { last.set(error.localizedDescription); continuation.resume(returning: -1) }
        }
        pipe.fileHandleForReading.readabilityHandler = nil
        if code == 0 { await check() } else { status = "Kumo setup did not complete: " + last.get() }
    }

    func cancelSetup() { setupProcess?.terminate(); status = "Stopping Kumo setup; existing research files are unchanged." }

    func acquire(specialist: String = "NVIDIA Kumo for Forecast") async throws -> UUID {
        guard !settingUp else { throw InferenceCoordinator.Busy(message: "Kumo setup is still running.") }
        let token = try InferenceCoordinator.shared.beginForecast()
        do {
            ModelRouter.shared.activeSpecialist = specialist
            forecasting = true; status = "Releasing local language models before Kumo…"
            (NSApp?.delegate as? AppDelegate)?.brain.unload()
            try await MLXRuntime.shared.stopForForecast()
            try await OllamaProvider().unloadForForecast()
            try Task.checkCancellation()
            status = "\(specialist) is working. Other model calls are paused."
            return token
        } catch {
            forecasting = false; InferenceCoordinator.shared.endForecast(token)
            ModelRouter.shared.activeSpecialist = nil
            status = error.localizedDescription; throw error
        }
    }

    func release(_ token: UUID) {
        forecasting = false; InferenceCoordinator.shared.endForecast(token)
        ModelRouter.shared.activeSpecialist = nil
        status = "Kumo finished. Your chosen research brain can resume when needed."
    }

    func cleanupBlocked() {
        status = "Kumo's worker could not be stopped. Other model calls remain blocked; quit and reopen Squipi before continuing."
    }
}

struct KumoSettingsView: View {
    @ObservedObject private var runtime = KumoRuntime.shared
    @State private var specialist = "forecast"
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            SectionTitle(text: "NVIDIA Kumo")
            Picker("Explore a specialist", selection: $specialist) {
                Text("Forecast").tag("forecast")
                Text("Relational").tag("relational")
                Text("Tabular Small").tag("tabular")
            }.pickerStyle(.segmented)
            Text(specialist == "forecast" ? KumoRuntime.displayName : specialist == "relational" ? KumoRuntime.relationalName : KumoRuntime.tabularName)
                .fontWeight(.semibold)
            if specialist == "forecast" {
                Text("Predict future values of a regular time series with at least 512 observations. Saves a forecast table and chart. Experimental: compare against simple baselines before relying on the forecast.")
            } else if specialist == "relational" {
                Text("Predict a target using linked tables—for example, learner outcomes using learner records and related course information. Uses the selected known outcomes as context; no separate training job.")
                Text("This first integration accepts cross-sectional snapshots. Prepare time-specific inputs explicitly before predicting future outcomes; automatic historical backtesting is not included.").foregroundStyle(Color.petMuted)
            } else {
                Text("Predict a target column from one table—for example, an outcome for rows whose target is missing. Uses known examples with fixed model settings, without a separate training or tuning job.")
            }
            Text("Open a model card in Brain to select data and run Kumo directly. You can also ask your research brain to use a specialist in conversation. All three run locally, one at a time. Squipi releases Ollama and app-managed MLX models first; external MLX servers must be stopped separately.")
                .foregroundStyle(Color.petMuted)
            if specialist == "forecast" { HStack {
                Button("Check Kumo setup") { Task { await runtime.check() } }.buttonStyle(Pill(filled: false)).disabled(runtime.settingUp || runtime.forecasting)
                Button("Set up NVIDIA Kumo…") { Task { await runtime.install() } }.buttonStyle(Pill(filled: false)).disabled(runtime.settingUp || runtime.forecasting)
                if runtime.settingUp { Button("Stop setup") { runtime.cancelSetup() }.buttonStyle(Pill(filled: false)) }
            }
                Text(runtime.status).textSelection(.enabled)
                Text("Setup downloads a separate Python runtime, libraries and approximately 2.8 GB of model files. Git and Apple Command Line Tools are required. A fresh-Mac setup has not yet been verified.").foregroundStyle(Color.petMuted)
            } else {
                Button("Check shared prediction setup") { Task { await runtime.checkPredictions() } }.buttonStyle(Pill(filled: false)).disabled(runtime.forecasting)
                Text(runtime.predictionStatus).textSelection(.enabled)
                Text("Relational and Tabular Small share one structured-data-models environment. No model is downloaded when a research task calls them.").foregroundStyle(Color.petMuted)
            }
            Text("Predictions are not causal impact estimates. Model scores need task-specific validation and are not guaranteed calibrated probabilities.")
                .foregroundStyle(Color.petMuted)
        }.font(.system(size: 11.5, design: .rounded)).card()
    }
}
