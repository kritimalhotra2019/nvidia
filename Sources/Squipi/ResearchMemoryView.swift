import AppKit
import SwiftUI

/// The same project memory is used by the native loop and the Hermes MCP server.
struct ResearchMemoryView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    struct Item: Identifiable {
        let id: String
        let title: String
        let detail: String
        let isTask: Bool
    }
    @State private var query = ""
    @State private var items: [Item] = []
    @State private var status = ""
    @State private var loading = false
    @State private var confirmClear = false
    @ObservedObject private var conversation = Conversation.shared
    @ObservedObject private var learning = ExperienceLearning.shared
    @AppStorage("experienceLearning") private var learningEnabled = true
    @State private var lessons: [[String: Any]] = []
    @State private var showArchived = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Project memory and research history")
            Text("Saved locally across chats and brain choices. Removing a memory leaves past task records; clearing this history removes both. Your original files and generated reports stay.")
                .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
            HStack {
                TextField("Search preferences, findings and past work", text: $query).onSubmit { reload() }
                Button("Search") { reload() }.disabled(loading)
            }
            Toggle("Learn reusable procedures from task experience", isOn: $learningEnabled)
                .onChange(of: learningEnabled) { _, enabled in
                    if enabled { learning.reviewWhenIdle() } else { learning.interrupt() }
                }
            Text("Reviews run locally when idle. Trial procedures are suggestions, not verified research findings. Your instruction files remain user-owned.")
                .font(.system(size: 11)).foregroundStyle(Color.petMuted)
            if !learning.notice.isEmpty { Text(learning.notice).font(.system(size: 11)).textSelection(.enabled) }
            Toggle("Show archived procedures", isOn: $showArchived).onChange(of: showArchived) { _, _ in reload() }
            ForEach(lessons.indices, id: \.self) { index in
                let lesson = lessons[index]
                VStack(alignment: .leading, spacing: 5) {
                    Text("\(lesson["title"] as? String ?? "Procedure") · \(lesson["state"] as? String ?? "pending") · v\(lesson["version"] as? Int ?? 1)")
                        .font(.system(size: 12, weight: .semibold))
                    Text(lesson["body"] as? String ?? "").font(.system(size: 11)).textSelection(.enabled)
                    Text("Verify: " + (lesson["checks"] as? [String] ?? []).joined(separator: "; "))
                        .font(.system(size: 10)).foregroundStyle(Color.petMuted)
                    Text("Evidence: \(lesson["origin_task"] as? String ?? "") · " + (lesson["evidence"] as? [String] ?? []).joined(separator: ", "))
                        .font(.system(size: 10)).textSelection(.enabled)
                    HStack {
                        Button("Activate") { manageLesson(lesson, action: "approve") }
                        Button("Archive") { manageLesson(lesson, action: "archive") }
                        if (lesson["version"] as? Int ?? 1) > 1 {
                            Button("Undo last change") { manageLesson(lesson, action: "rollback") }
                        }
                    }.disabled(conversation.busy || loading)
                }.padding(.vertical, 5)
            }
            if !status.isEmpty { Text(status).font(.system(size: 11)).foregroundStyle(Color.petMuted) }
            ForEach(items) { item in
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title).font(.system(size: 12, weight: .semibold))
                    Text(item.detail).font(.system(size: 11)).foregroundStyle(Color.petMuted).textSelection(.enabled)
                    HStack {
                        Spacer()
                        if item.isTask {
                            Button("Continue in Chat") {
                                Task { @MainActor in
                                    let ok = await Conversation.shared.resume(taskID: item.id)
                                    if !ok { status = "Could not read that saved task; nothing was started." }
                                    (NSApp?.delegate as? AppDelegate)?.hub.selectTab(0)
                                }
                            }.disabled(conversation.busy || loading)
                        } else {
                            Button("Delete memory") { remove(item.id) }.disabled(conversation.busy || loading)
                        }
                    }
                }.padding(.vertical, 4)
            }
            Button("Clear project memory and history…") { confirmClear = true }
                .disabled(conversation.busy || loading)
                .confirmationDialog("Delete all saved project memories and research run history?", isPresented: $confirmClear) {
                    Button("Clear memory and history", role: .destructive) { clear() }
                } message: {
                    Text("This cannot be undone. Chat messages, learned procedures, facts and reminders, original files and generated artifacts are managed separately.")
                }
        }.card().task { reload() }
    }

    private func reload() {
        guard !loading else { return }
        guard Worker.shared.isInstalled else { status = "Set up Data tools in Me to use project memory."; return }
        loading = true
        Task { @MainActor in
            defer { loading = false }
            do {
                let result = try await Worker.shared.call("recall_research", ["query": String(query.prefix(1000)), "limit": 20])
                let memories = (result["memories"] as? [[String: Any]] ?? []).compactMap { row -> Item? in
                    guard let id = row["memory_id"] as? String else { return nil }
                    return Item(id: id, title: row["kind"] as? String ?? "Memory", detail: row["text"] as? String ?? "", isTask: false)
                }
                let tasks = (result["tasks"] as? [[String: Any]] ?? []).compactMap { row -> Item? in
                    guard let id = row["task_id"] as? String else { return nil }
                    return Item(id: id, title: "\(row["status"] as? String ?? "saved") · \(row["instruction"] as? String ?? id)", detail: row["summary"] as? String ?? "", isTask: true)
                }
                items = memories + tasks
                let learned = try await Worker.shared.learning(["action":"list", "query":String(query.prefix(1000)), "include_archived":showArchived])
                lessons = learned["lessons"] as? [[String:Any]] ?? []
                status = items.isEmpty ? "No matching memories or research runs yet." : "Showing up to 20 matching records."
            } catch { status = "Could not load research memory: \(error.localizedDescription)" }
        }
    }

    private func manageLesson(_ lesson: [String:Any], action: String) {
        guard let id = lesson["id"] as? String else { return }
        loading = true
        Task { @MainActor in
            do {
                var args: [String:Any] = ["action":"manage", "lesson_id":id, "operation":action]
                if action == "rollback" { args["version"] = (lesson["version"] as? Int ?? 2) - 1 }
                _ = try await Worker.shared.learning(args)
                loading = false; reload()
            } catch { status = "Could not update lesson: \(error.localizedDescription)"; loading = false }
        }
    }

    private func remove(_ id: String) {
        loading = true
        Task { @MainActor in
            do {
                _ = try await Worker.shared.call("forget_research", ["memory_id": id])
                loading = false; reload()
            } catch { status = "Could not delete memory: \(error.localizedDescription)"; loading = false }
        }
    }

    private func clear() {
        loading = true
        Task { @MainActor in
            do {
                _ = try await Worker.shared.call("clear_research_history", [:])
                loading = false; reload()
            } catch { status = "Could not clear research history: \(error.localizedDescription)"; loading = false }
        }
    }
}
