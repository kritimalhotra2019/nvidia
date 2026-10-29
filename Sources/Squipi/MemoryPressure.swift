import Foundation
import Combine

/// Memory is the real speed limit for a 9B model on a 16 GB Mac. On 25–26 Oct 2026 the same prompt
/// prefilled at 1,200–2,300 tokens/s with memory free and at 45–65 tokens/s under pressure, so a
/// 10K-token round took 3 minutes instead of 10 seconds. The harness now watches two signals, the
/// kernel's memory-pressure notifications and the measured prefill rate, shrinks prompts and spans
/// when they are bad, releases the embedding model when a task ends under pressure, and tells the
/// user in plain words what would help.
@MainActor
final class MemoryPressure: ObservableObject {
    static let shared = MemoryPressure()

    enum Level: String { case normal, warning, critical }

    @Published private(set) var level: Level = .normal
    @Published private(set) var availableBytes: UInt64 = 0
    @Published private(set) var prefillRate: Double? = nil

    private var source: DispatchSourceMemoryPressure?
    private var timer: Timer?

    private init() {}

    func start() {
        guard source == nil else { return }
        let s = DispatchSource.makeMemoryPressureSource(eventMask: [.normal, .warning, .critical], queue: .main)
        s.setEventHandler { [weak self] in
            guard let self else { return }
            let event = s.data
            if event.contains(.critical) { self.level = .critical }
            else if event.contains(.warning) { self.level = .warning }
            else { self.level = .normal }
            self.refresh()
            NSLog("MEMORY pressure event: %@, %.1f GB available", self.level.rawValue, Double(self.availableBytes) / 1e9)
        }
        s.resume()
        source = s
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in Task { @MainActor in self?.refresh() } }
    }

    /// Free + inactive + purgeable + speculative pages: what the kernel could hand to a model without swapping.
    static func measureAvailableBytes() -> UInt64 {
        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &stats) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &count) }
        }
        guard result == KERN_SUCCESS else { return 0 }
        let pages = UInt64(stats.free_count) + UInt64(stats.inactive_count) + UInt64(stats.purgeable_count) + UInt64(stats.speculative_count)
        return pages * UInt64(vm_kernel_page_size)
    }

    func refresh() {
        availableBytes = Self.measureAvailableBytes()
        prefillRate = PromptBudget.lastPrefillRate
        // The kernel only posts transitions; a stale "warning" after memory came back would keep
        // prompts small for no reason, so the level also follows the measured headroom.
        if level != .normal, availableBytes > 4_000_000_000 { level = .normal }
        if level == .normal, availableBytes > 0, availableBytes < 1_500_000_000 { level = .warning }
        PromptBudget.memoryLevel = level.rawValue
    }

    var availableGB: Double { Double(availableBytes) / 1e9 }

    /// True when prompts and spans should shrink and idle models should be released.
    var constrained: Bool {
        if level != .normal { return true }
        if availableBytes > 0, availableBytes < 2_000_000_000 { return true }
        if let rate = prefillRate ?? PromptBudget.lastPrefillRate, rate < 90 { return true }
        return false
    }

    /// One line for the Work tab and the reply footer; empty when there is nothing to say.
    var summary: String {
        guard constrained else { return "" }
        var parts: [String] = []
        parts.append(level == .critical ? "memory critical" : "memory tight")
        if availableBytes > 0 { parts.append(String(format: "%.1f GB available", availableGB)) }
        if let rate = prefillRate ?? PromptBudget.lastPrefillRate { parts.append(String(format: "prefill %.0f tok/s", rate)) }
        return parts.joined(separator: ", ")
    }

    /// Plain advice shown in the Work tab while constrained: it names what is actually using the memory
    /// instead of guessing at browsers, and does not blame other apps when Squipi's own model is the user.
    var advice: String {
        guard constrained else { return "" }
        let rate = prefillRate ?? PromptBudget.lastPrefillRate
        let slow = rate.map { $0 < 90 } ?? false
        var text = "Squipi is running with little free memory, so the local model reads prompts slowly"
        if slow, let rate { text += String(format: " (%.0f tokens/s; over 1,000 when memory is free)", rate) }
        text += ". Prompts and reading spans are being kept small automatically."
        let users = Self.largestOtherProcesses()
        let model = users.first { $0.name.contains("llama-server") || $0.name.contains("ollama") || $0.name.contains("mlx") }
        let others = users.filter { !($0.name.contains("llama-server") || $0.name.contains("ollama") || $0.name.contains("mlx")) && $0.gigabytes >= 0.5 }
        if let model { text += String(format: " The local model itself holds %.1f GB.", model.gigabytes) }
        if !others.isEmpty {
            text += " Other large processes: " + others.prefix(3).map { String(format: "%@ %.1f GB", $0.name, $0.gigabytes) }.joined(separator: ", ") + ". Closing them would make answers faster."
        } else if model != nil {
            text += " No other large app is open; the limit is the Mac's memory while the model is loaded. Waiting for the current task to finish frees the most."
        }
        return text
    }

    struct ProcessUse { let name: String; let gigabytes: Double }

    /// The other local brain still holding memory while this one works: Ollama and MLX never share a 16 GB Mac well.
    /// Returns a one-line note for the Work tab, or nil.
    nonisolated static func competingBrain(activeMode: String) -> String? {
        let users = largestOtherProcesses()
        if activeMode == "ollama", let mlx = users.first(where: { $0.name == "MLX server" }), mlx.gigabytes >= 1 {
            return String(format: "The MLX server is holding %.1f GB while Ollama works; stop it (Me → MLX) so Ollama has the memory to itself.", mlx.gigabytes)
        }
        if activeMode == "mlx", let ol = users.first(where: { $0.name == "Ollama llama-server" }), ol.gigabytes >= 1 {
            return String(format: "Ollama is still holding %.1f GB while MLX works; its models are being released.", ol.gigabytes)
        }
        return nil
    }

    /// Resident memory of the largest processes other than Squipi (via /bin/ps; local only).
    nonisolated static func largestOtherProcesses() -> [ProcessUse] {
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/ps"); p.arguments = ["-axo", "rss=,comm="]
        let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return [] }
        p.waitUntilExit()
        let text = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        var byName: [String: Double] = [:]
        for line in text.split(separator: "\n") {
            let parts = line.trimmingCharacters(in: .whitespaces).split(separator: " ", maxSplits: 1)
            guard parts.count == 2, let kb = Double(parts[0]) else { continue }
            let path = String(parts[1])
            if path.contains("Squipi.app") { continue }
            let name = path.contains("Ollama.app") ? "Ollama llama-server" : (path.contains("mlx") ? "MLX server" : (path.split(separator: "/").first { $0.hasSuffix(".app") }.map { String($0.dropLast(4)) } ?? String(path.split(separator: "/").last ?? "")))
            byName[name, default: 0] += kb / 1_048_576
        }
        return byName.map { ProcessUse(name: $0.key, gigabytes: $0.value) }.sorted { $0.gigabytes > $1.gigabytes }
    }

    /// Release what is not needed between tasks: the embedding model stays loaded only when memory allows.
    func releaseIdleModels() async {
        guard constrained else { return }
        await OllamaProvider().unload(model: OllamaProvider.embeddingModel)
    }
}
