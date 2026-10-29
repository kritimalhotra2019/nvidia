import Combine
import Foundation

/// The Hub recreates its tab views. Keep unsaved text in memory until Save or explicit Reload,
/// without writing private draft content to disk or adding it to model requests.
@MainActor
final class UserInstructionEditor: ObservableObject {
    static let shared = UserInstructionEditor(instructions: .shared)
    private let instructions: UserInstructions
    private var loaded = false
    @Published var selected: UserInstructions.Kind = .memory
    @Published private(set) var files: [UserInstructions.FileState] = []
    @Published var drafts: [UserInstructions.Kind: String] = [:]
    @Published private(set) var baselines: [UserInstructions.Kind: String] = [:]
    @Published var status = ""
    @Published var error = ""

    init(instructions: UserInstructions) { self.instructions = instructions }

    var current: UserInstructions.FileState? { files.first { $0.kind == selected } }
    var dirty: Bool { UserInstructions.Kind.allCases.contains { (drafts[$0] ?? "") != (baselines[$0] ?? "") } }
    var currentDirty: Bool { (drafts[selected] ?? "") != (baselines[selected] ?? "") }
    var bytes: Int { (drafts[selected] ?? "").utf8.count }

    /// Called when returning to Mind. Dirty text and its original conflict-check baseline survive.
    func refreshIfClean() { if !loaded || !dirty { reload() } }
    func refreshStates() { files = instructions.snapshot().files }

    /// The UI explicitly confirms discarding dirty drafts before calling this.
    func reload() {
        let snapshot = instructions.snapshot()
        files = snapshot.files
        drafts = Dictionary(uniqueKeysWithValues: files.map { ($0.kind, $0.content ?? "") })
        baselines = Dictionary(uniqueKeysWithValues: files.compactMap { file in file.content.map { (file.kind, $0) } })
        status = ""
        error = snapshot.warnings.filter { warning in !files.contains { $0.warning == warning } }.joined(separator: "\n")
        loaded = true
    }

    func save() throws {
        try instructions.save(drafts[selected] ?? "", for: selected, expectedContent: baselines[selected])
        baselines[selected] = drafts[selected] ?? ""
        refreshStates()
        status = "Saved \(selected.filename). \(instructions.isEnabled(selected) ? "Changes apply to the next request." : "Enable Use in requests when you want Squipi to follow it.")"
    }
}
