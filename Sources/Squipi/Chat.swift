import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// The conversation with Squipi, typed or spoken. Ollama/Nebius answers go through the tool-using Agent; the
/// built-in model answers from the profile alone. Spoken turns are read aloud and also appear here.
@MainActor
final class Conversation: ObservableObject {
    static let shared = Conversation()
    struct StepInfo: Identifiable, Codable {
        var id = UUID()
        var tool: String
        var ok: Bool
        var summary: String
        var outputs: [String]     // file paths that exist
        var elapsed: Double
    }
    struct Message: Identifiable, Codable {
        var id = UUID()
        var role: String      // user | squipi | system
        var text: String
        var via: String       // "Local", "Ollama • model · tools used"
        var reasoning: String = ""
        var spoken = false
        var steps: [StepInfo] = []
        var at = Date()
        var taskID: String? = nil
        var taskStatus: String? = nil
    }
    @Published var messages: [Message] = [] { didSet { save() } }
    @Published var streaming = ""
    @Published var streamingReasoning = ""
    @Published var liveSteps: [StepInfo] = []       // steps of the turn in progress
    private var turnStartedAt = Date()
    private var sideQuestionQueue: [String] = []     // "/btw …" questions asked during a task, answered when it ends
    @Published private(set) var attachments: [URL] = [] // staged only; read after an instruction is sent
    @Published var draft = ""
    @Published var busy = false
    @Published private(set) var interrupting = false
    @Published private(set) var hasSavedTask = false
    private var interruptionID: UUID?
    private var submittedAsSideQuestion = false
    private var task: Task<Void, Never>?
    private var settlingTask: Task<Void, Never>?
    private var url: URL { Store.shared.dir.appendingPathComponent("chat.json") }
    private var continuationURL: URL { Store.shared.dir.appendingPathComponent("research-continuation.json") }
    private var continuation = ResearchContinuation()
    private var submittedInstruction = ""
    private var submittedHasAttachments = false

    private init() {
        if let d = try? Data(contentsOf: url), let m = try? JSONDecoder().decode([Message].self, from: d) { messages = Array(m.suffix(200)) }
        let savedContinuation = (try? Data(contentsOf: continuationURL)).flatMap { try? JSONDecoder().decode(ResearchContinuation.self, from: $0) }
        continuation = ResearchContinuation.restoring(saved: savedContinuation,
            history: messages.map { .init(role: $0.role, text: $0.text, taskID: $0.taskID, status: $0.taskStatus) })
        hasSavedTask = continuation.pending != nil || !(continuation.suspended?.isEmpty ?? true)
        if savedContinuation?.pending == nil { saveContinuation() }
        Agent.shared.onStep = { [weak self] step in
            self?.liveSteps.append(StepInfo(tool: step.tool, ok: step.ok, summary: step.summary, outputs: step.outputs.map(\.path), elapsed: step.elapsed))
        }
    }

    private func save() {
        let enc = JSONEncoder(); enc.outputFormatting = [.prettyPrinted]
        if let d = try? enc.encode(Array(messages.suffix(200))) { try? d.write(to: url, options: .atomic) }
    }

    private func saveContinuation() {
        hasSavedTask = continuation.pending != nil || !(continuation.suspended?.isEmpty ?? true)
        do { try JSONEncoder().encode(continuation).write(to: continuationURL, options: .atomic) }
        catch { NSLog("Could not save the pending research task: %@", error.localizedDescription) }
    }

    /// Start over: the model forgets the thread (files and the research log stay).
    func clear() {
        cancel()
        ExperienceLearning.shared.newConversation()
        messages = []
        continuation = ResearchContinuation(); saveContinuation()
        draft = ""
        attachments = []
        (NSApp?.delegate as? AppDelegate)?.brain.forget()
    }

    /// File selection is not an instruction. Never read, import, profile or call a model here.
    /// In particular, an open panel can hide the hub without changing the user's intent.
    func attach(_ files: [URL], showChat: Bool = true) {
        var existing = Set(attachments.map { $0.standardizedFileURL })
        let supported = files.filter { $0.isFileURL && PetWebView.dataExtensions.contains($0.pathExtension.lowercased()) }
        attachments.append(contentsOf: supported.filter { existing.insert($0.standardizedFileURL).inserted })
        guard !supported.isEmpty else { return }
        if showChat, let app = NSApp?.delegate as? AppDelegate {
            app.panel.show()
            app.hub.show()
            app.hub.selectTab(0)
        }
    }

    func removeAttachment(_ file: URL) { attachments.removeAll { $0.standardizedFileURL == file.standardizedFileURL } }

    func pickFiles(showChat: Bool = true) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.commaSeparatedText, .tabSeparatedText, .plainText, .spreadsheet, .pdf] + ["docx", "pptx", "md", "parquet"].compactMap { UTType(filenameExtension: $0) }
        panel.allowsOtherFileTypes = true
        panel.message = "Attach files, then describe what you want Squipi to do. Nothing runs until you send an instruction."
        panel.prompt = "Attach"
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = true
        NSApp.activate(ignoringOtherApps: true)
        panel.begin { [weak self] response in
            guard response == .OK else { return }
            Task { @MainActor in self?.attach(panel.urls, showChat: showChat) }
        }
    }

    @discardableResult
    /// The host's record of the latest task for a status question: its state, each step with its result line, and
    /// the answer so far if one was written. Empty when no task with steps is in the chat.
    func taskStatusRecord() -> String {
        // The last task that did work; a stopped turn that only read the journal is not the task the user asks about.
        let housekeeping: Set<String> = ["read_research_task", "recall_research", "turn_phases", "objective_requirement_review"]
        guard let task = messages.dropLast().last(where: { m in m.steps.contains { !housekeeping.contains($0.tool) } })
                ?? messages.dropLast().last(where: { !$0.steps.isEmpty }) else { return "" }
        let request = messages.dropLast().last(where: { $0.role == "user" && !ResearchPolicy.isStatusCheck($0.text) })?.text ?? ""
        let state = task.role == "system" ? "stopped by the user (\(task.text))" : (task.taskStatus ?? "finished")
        let total = task.steps.reduce(0.0) { $0 + $1.elapsed }
        var lines = ["Task record (host, not the model): request: \(String(request.prefix(300))). State: \(state). Steps run: \(task.steps.count)" + (total > 0 ? ", about \(Int(total.rounded())) s in tools" : "") + "."]
        for s in task.steps where s.tool != "turn_phases" {
            lines.append("- \(s.tool): \(s.ok ? "ok" : "FAILED") — \(String(s.summary.prefix(260)))" + (s.outputs.isEmpty ? "" : " → \(s.outputs.map { URL(fileURLWithPath: $0).lastPathComponent }.joined(separator: ", "))"))
        }
        if task.role == "squipi", !task.text.isEmpty { lines.append("Answer written so far: \(String(task.text.prefix(900)))") }
        else { lines.append("No written answer yet.") }
        return lines.joined(separator: "\n")
    }

    func send(_ text: String, spoken: Bool = false, sideQuestion: Bool = false, resumeAfterQuestion: Bool = false, automatic: Bool = false) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return false }
        if busy || settlingTask != nil {
            guard !interrupting else { return false }
            // A side channel that never interrupts the work (28 Oct, user: "?" for an update cancelled a 15-minute task).
            // "?" / "status" gets an instant host status from the live steps; "/btw <question>" is noted and answered by
            // the model as soon as the task finishes. The task keeps running either way.
            let btwPrefix = t.range(of: #"^/?btw\b[:,]?\s*"#, options: [.regularExpression, .caseInsensitive])
            if attachments.isEmpty, btwPrefix != nil || ResearchPolicy.isStatusCheck(t) {
                let question = btwPrefix.map { String(t[$0.upperBound...]).trimmingCharacters(in: .whitespaces) } ?? t
                messages.append(Message(role: "user", text: t, via: ""))
                let status = liveStatusRecord()
                if question.isEmpty || ResearchPolicy.isStatusCheck(question) {
                    messages.append(Message(role: "squipi", text: status, via: "Squipi · status, the task continues"))
                } else {
                    sideQuestionQueue.append(question)
                    messages.append(Message(role: "squipi", text: status + "\n\nNoted. I will answer your question as soon as this task finishes.", via: "Squipi · the task continues"))
                }
                NSLog("CHAT side channel while busy: %@", question.isEmpty ? "status" : "question queued")
                return true
            }
            if attachments.isEmpty, ResearchContinuation.isNegligibleInterjection(t) {
                // A greeting, a misheard word or a filler must not cancel running research.
                messages.append(Message(role: "system", text: "Heard “\(t)”. The current task continues; say Stop or Pause to interrupt it.", via: "Squipi"))
                return true
            }
            let previous = settlingTask ?? task
            let question = attachments.isEmpty && ResearchContinuation.isSideQuestion(t)
            cancel(pausing: true); settlingTask = nil
            let token = UUID(); interruptionID = token; interrupting = true; busy = true
            task = Task {
                await previous?.value
                guard !Task.isCancelled, interruptionID == token else { return }
                interruptionID = nil; interrupting = false; busy = false
                continuation.pending?.status = "partial"; saveContinuation()
                _ = send(t, spoken: spoken, sideQuestion: question, resumeAfterQuestion: question)
            }
            return true
        }
        ExperienceLearning.shared.interrupt()
        let files = attachments
        submittedInstruction = t; submittedHasAttachments = !files.isEmpty; submittedAsSideQuestion = sideQuestion
        attachments = []
        messages.append(Message(role: automatic ? "system" : "user", text: (automatic ? "Resuming saved research." : t) + (files.isEmpty ? "" : "\n📎 " + files.map(\.lastPathComponent).joined(separator: ", ")), via: "", spoken: spoken))
        busy = true
        task = Task {
            var full = t
            var registrationErrors: [String] = []
            for f in files {
                guard !Task.isCancelled else { return }
                let result = await Analysis.shared.registerChecked(file: f)
                full += "\n\n[\(result.line)]"
                if result.failed || result.inputID == nil { registrationErrors.append(result.line) }
            }
            guard !Task.isCancelled else { return }
            if !registrationErrors.isEmpty {
                _ = continuation.prepare(userText: t, registeredInstruction: full, inputIDs: [], hasNewAttachments: true)
                continuation.pending?.blockedInput = "A required attachment failed to import. Attach a readable replacement before resuming; an older dataset cannot be substituted."
                saveContinuation()
                messages.append(Message(role: "system", text: registrationErrors.joined(separator: "\n") + "\nThis request is paused. No analysis ran against an older file. Open an encrypted workbook in Excel and save an unencrypted copy, or attach a readable replacement.", via: "Attachment check"))
                busy = false
                return
            }
            await reply(to: full, spoken: spoken)
            if resumeAfterQuestion, !Task.isCancelled, continuation.pending != nil,
               messages.last(where: { $0.role == "squipi" })?.taskStatus == "completed" {
                _ = send("continue", automatic: true)
            }
        }
        return true
    }

    /// Continue a saved task by its record: the objective text and the exact input ids come from the
    /// journal, never from a sentence the router has to parse. Returns false if the task cannot be read.
    @discardableResult
    func resume(taskID: String) async -> Bool {
        guard !busy else { return false }
        guard let record = try? await Worker.shared.call("read_research_task", ["task_id": taskID]),
              let task = record["task"] as? [String: Any] else { return false }
        var objective = task["instruction"] as? String ?? ""
        if let range = objective.range(of: "Original user request:\n") {
            let tail = objective[range.upperBound...]
            objective = String(tail.components(separatedBy: "\n\n").first ?? String(tail))
        }
        guard !objective.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        let inputs = (task["inputs"] as? [String]) ?? ((task["inputs_json"] as? String).flatMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String] }) ?? []
        if let old = continuation.pending { continuation.suspended = Array(((continuation.suspended ?? []) + [old]).suffix(20)) }
        var job = ResearchContinuation.Job(objective: objective, inputIDs: inputs)
        job.runIDs = [taskID]; job.status = "partial"
        continuation.pending = job
        if let first = inputs.first {
            if Analysis.shared.datasets.contains(where: { $0["id"] as? String == first }) { Analysis.shared.setCurrent(dataset: first) }
            else if Analysis.shared.documents.contains(where: { $0["id"] as? String == first }) { Analysis.shared.setCurrent(document: first) }
        }
        saveContinuation()
        return send("continue")
    }

    func pause() {
        let previous = task
        let objective = continuation.pending?.id
        cancel(pausing: true)
        Task { @MainActor in
            await previous?.value
            if continuation.pending?.id == objective {
                continuation.pending?.status = "partial"
                saveContinuation()
            }
        }
    }

    func cancel(pausing: Bool = false) {
        Agent.shared.cancellationState = pausing ? "paused" : "cancelled"
        if busy { settlingTask = task }
        interruptionID = nil; interrupting = false
        if busy { continuation.cancel(); saveContinuation() }
        task?.cancel()
        (NSApp?.delegate as? AppDelegate)?.brain.stop()
        (NSApp?.delegate as? AppDelegate)?.hush()
        NemoRunner.shared.stop()
        HermesRunner.shared.cancel()
        Worker.shared.cancelAll()
        busy = NemoRunner.shared.isRunning
        streaming = ""
        (NSApp?.delegate as? AppDelegate)?.panel.js("pet.setCaption('');pet.setState('idle')")
    }

    /// Where the running task stands, from the host's live record: elapsed time, steps so far with their result
    /// lines, and the text being written if any. Instant; no model call; the task is not touched.
    func liveStatusRecord() -> String {
        let minutes = Int(Date().timeIntervalSince(turnStartedAt) / 60), seconds = Int(Date().timeIntervalSince(turnStartedAt)) % 60
        let request = messages.last(where: { $0.role == "user" && !ResearchPolicy.isStatusCheck($0.text) && $0.text.range(of: #"^/?btw\b"#, options: [.regularExpression, .caseInsensitive]) == nil })?.text ?? "the current request"
        var lines = ["Still working on: \(String(request.prefix(160))) (\(minutes > 0 ? "\(minutes) min \(seconds) s" : "\(seconds) s") so far)."]
        let steps = liveSteps.filter { $0.tool != "turn_phases" }
        if steps.isEmpty { lines.append("No tool has returned yet; the model is on its first round.") }
        else {
            lines.append("Done so far:")
            for s in steps { lines.append("• \(s.tool): \(s.ok ? "" : "failed — ")\(String(s.summary.prefix(220)))") }
        }
        let partial = streaming.trimmingCharacters(in: .whitespacesAndNewlines)
        if !partial.isEmpty { lines.append("Writing now: " + String(partial.prefix(300)) + (partial.count > 300 ? "…" : "")) }
        lines.append("The task continues. Say Stop to interrupt it, or ask with /btw and I will answer when it is done.")
        return lines.joined(separator: "\n")
    }

    private func reply(to text: String, spoken: Bool) async {
        liveSteps = []; turnStartedAt = Date()
        var checkpointObjective = ""
        var checkpointInputs: [String] = []
        defer {
            busy = false; streaming = ""; streamingReasoning = ""; liveSteps = []
            Task { @MainActor in
                _ = await ExperienceLearning.shared.checkpoint(self.messages, objective: checkpointObjective, inputs: checkpointInputs)
                ExperienceLearning.shared.reviewWhenIdle()
            }
            // Questions asked with /btw during the task are answered now, one at a time, as conversation.
            if !sideQuestionQueue.isEmpty {
                let next = sideQuestionQueue.removeFirst()
                Task { @MainActor in _ = self.send(next, sideQuestion: true) }
            }
        }
        guard let app = NSApp?.delegate as? AppDelegate else { return }
        defer {
            app.panel.js("pet.setCaption('')")
            if Task.isCancelled { app.panel.js("pet.setState('idle')") }
        }
        let userText = submittedInstruction.isEmpty ? text : submittedInstruction
        // The sources of a turn are decided here; on a cold launch the inventory must exist first (F19).
        if Worker.shared.isInstalled, Analysis.shared.datasets.isEmpty, Analysis.shared.documents.isEmpty { await Analysis.shared.refreshInventory() }
        let explicitInput = !ResearchPolicy.explicitSourcePaths(userText).isEmpty || (Analysis.shared.datasets + Analysis.shared.documents).contains { item in
            [item["id"] as? String, item["name"] as? String, item["path"] as? String].compactMap { $0 }
                .contains { !$0.isEmpty && userText.localizedCaseInsensitiveContains($0) }
        }
        let turn = continuation.prepare(userText: userText, registeredInstruction: text,
            inputIDs: Analysis.shared.researchInputIDs(for: text), hasNewAttachments: submittedHasAttachments, inputSelectionIsExplicit: explicitInput,
            history: messages.dropLast().map { .init(role: $0.role, text: $0.text) }, sideQuestion: submittedAsSideQuestion)
        saveContinuation()
        let previousAnswer = messages.dropLast().last { $0.role == "squipi" }
        let completedWorkIsCurrent = previousAnswer?.taskID.map { continuation.lastCompleted?.runIDs.contains($0) ?? false } ?? false
        let discussion = turn.kind == .conversation && !ResearchPolicy.requestsExternalVerification(userText) && !submittedHasAttachments && !explicitInput &&
            ConversationCheckpoint.isDiscussion(userText, hasCompletedWork: completedWorkIsCurrent)
        checkpointObjective = turn.kind == .work ? (continuation.pending?.objective ?? "") : (discussion ? (continuation.lastCompleted?.objective ?? "") : "")
        checkpointInputs = turn.kind == .work ? (continuation.pending?.inputIDs ?? []) : (discussion ? (continuation.lastCompleted?.inputIDs ?? []) : [])
        let conversationContext = await ExperienceLearning.shared.checkpoint(Array(messages.dropLast()), objective:checkpointObjective, inputs:checkpointInputs)
        let effectiveText = turn.effectiveInstruction
        // "?" / "status" / "what have you got so far": a chat answer from the task record, not another tool loop.
        let statusContext = ResearchPolicy.isStatusCheck(userText) && !submittedHasAttachments ? taskStatusRecord() : ""
        if !statusContext.isEmpty { NSLog("CHAT status check answered from the task record (%d chars)", statusContext.count) }
        if turn.kind == .paused {
            let line = continuation.pending?.blockedInput ?? "The task is stopped. Your request and constraints remain saved; say ‘continue’ when you want to resume."
            messages.append(Message(role: "squipi", text: line, via: "Saved research task"))
            if spoken { app.voice.say(line, force: true) }
            app.panel.js("pet.setState('idle')")
            return
        }
        if turn.kind == .status {
            await showPendingStatus(spoken: spoken, app: app)
            app.panel.js("pet.setState('idle')")
            return
        }
        app.panel.js("pet.setState('thinking')")
        let register: Brain.Register = spoken ? .spoken : .written
        if ModelRouter.shared.mode == .ollama, UserDefaults.standard.bool(forKey: "nemoResearchEnabled"), turn.kind == .work, !submittedInstruction.lowercased().contains("remind"), !["http://", "https://", "search the web", "find papers"].contains(where: submittedInstruction.lowercased().contains), let jobID = turn.jobID {
            let job = continuation.pending
            let ids = Set(job?.inputIDs ?? Analysis.shared.researchInputIDs(for: effectiveText))
            let inputs = (Analysis.shared.datasets + Analysis.shared.documents).filter { ids.contains($0["id"] as? String ?? "") }.compactMap { ($0["path"] as? String).map { URL(fileURLWithPath: $0) } }
            let snapshot = SavedTemplates.shared.snapshot()
            let selected = snapshot.templates.filter { entry in snapshot.defaults[entry.format] == entry.id || userText.localizedCaseInsensitiveContains(entry.name) || userText.contains(entry.id) }
            let templates = selected.compactMap { try? SavedTemplates.shared.fileURL(for: $0) }
            app.panel.js("pet.setState('analysing')")
            let objective = [job?.objective ?? userText] + (job?.amendments ?? [])
            let r = await NemoRunner.shared.run(jobID: jobID, objective: objective.joined(separator: "\n"), followup: userText, inputs: inputs, templates: templates)
            guard continuation.pending?.id == jobID else { return }
            continuation.record(taskID: r.taskID, status: r.status?.rawValue, response: r.text, jobID: jobID); saveContinuation()
            let steps = r.steps.map { StepInfo(tool: $0.tool, ok: $0.ok, summary: $0.summary, outputs: $0.outputs.map(\.path), elapsed: $0.elapsed) }
            messages.append(Message(role: "squipi", text: r.text.isEmpty ? (r.error ?? "Progress saved.") : r.text, via: r.via, spoken: spoken, steps: steps, taskID: r.taskID, taskStatus: r.status?.rawValue))
            if let error = r.error, !r.text.isEmpty { messages.append(Message(role: "system", text: error, via: r.via)) }
            app.panel.js("pet.setState('idle')")
            if spoken { app.voice.say(r.text, force: true) }
            return
        }
        switch ModelRouter.shared.mode {
        case .local:
            guard turn.kind != .work else {
                let line = "The built-in chat model cannot execute research tools or create files. Your request is saved. Choose Squipi + Qwen in Me, then say ‘continue’ to run it."
                messages.append(Message(role: "squipi", text: line, via: "Local", spoken: spoken, taskStatus: "partial"))
                app.panel.js("pet.setState('idle')")
                if spoken { app.voice.say(line, force: true) }
                return
            }
            guard app.brain.enabled, app.brain.isReady else {
                let line = app.brainUnavailableLine()
                messages.append(Message(role: "system", text: line, via: "Local"))
                if spoken { app.voice.say(line, force: true) }
                app.panel.js("pet.setState('idle')")
                return
            }
            for warning in UserInstructions.shared.snapshot().warnings {
                messages.append(Message(role: "system", text: "Personal instructions: " + warning, via: "Local"))
            }
            app.brain.onToken = { [weak self] delta in if let d = delta { self?.streaming += d } }
            let out = spoken ? await app.brain.chat(effectiveText) : await app.brain.write(effectiveText)
            app.brain.onToken = nil
            app.panel.js("pet.setState('idle')")
            if let out {
                messages.append(Message(role: "squipi", text: out, via: "Local", spoken: spoken))
                if spoken { app.voice.say(out, force: true) }
            } else {
                messages.append(Message(role: "system", text: "The local model returned nothing.", via: "Local"))
                if spoken { app.voice.say("I heard you, but nothing came out. Try me again.", force: true) }
            }
        case .hermes:
            let personalInstructions = UserInstructions.shared.snapshot()
            let savedTemplates = SavedTemplates.shared.snapshot()
            guard HermesRunner.isInstalled else {
                messages.append(Message(role: "system", text: "Hermes Agent is not installed (expected at ~/.local/bin/hermes).", via: "Hermes")); app.panel.js("pet.setState('idle')"); return
            }
            let configuration: HermesLocalConfiguration
            do { configuration = try HermesRunner.localConfiguration() }
            catch {
                messages.append(Message(role: "system", text: error.localizedDescription, via: "Hermes · on this Mac"))
                app.panel.js("pet.setState('idle')")
                return
            }
            app.panel.js("pet.setState('analysing')")
            app.panel.js("pet.setCaption('Hermes is working locally…')")
            for warning in personalInstructions.warnings + savedTemplates.warnings {
                messages.append(Message(role: "system", text: "Personal instructions: " + warning, via: "Hermes · on this Mac"))
            }
            let history = ResearchContinuation.boundedHistory(messages.dropLast().map { .init(role: $0.role, text: $0.text) })
                .map { ChatMessage(role: $0.role == "user" ? "user" : "assistant", content: $0.text) }
            let r = await HermesRunner.shared.research(instruction: effectiveText, history: history, spoken: spoken, configuration: configuration) { state in
                guard !Task.isCancelled else { return }
                app.panel.js(state == "reading" ? "pet.setState('reading')" : "pet.setState('analysing')")
                app.panel.js(state == "reading" ? "pet.setCaption('Hermes is reading the document…')" : "pet.setCaption('Hermes is working locally…')")
            }
            if turn.kind == .work { continuation.record(taskID: r.taskID, status: r.status?.rawValue, response: r.text, jobID: turn.jobID); saveContinuation() }
            let steps = r.steps.map { StepInfo(tool: $0.tool, ok: $0.ok, summary: $0.summary, outputs: $0.outputs.map(\.path), elapsed: $0.elapsed) }
            messages.append(Message(role: "squipi", text: r.text.isEmpty ? (r.error ?? "No answer was returned.") : r.text, via: r.via, spoken: spoken, steps: steps, taskID: r.taskID, taskStatus: r.status?.rawValue))
            if let error = r.error, !r.text.isEmpty { messages.append(Message(role: "system", text: error, via: r.via)) }
            app.panel.js("pet.setCaption('');pet.setState('idle')")
            if spoken { app.voice.say(Brain.trimSpoken(r.text) ?? r.text, force: true) }
        case .ollama, .nebius, .mlx:
            let history = ResearchContinuation.boundedHistory(messages.dropLast().map { .init(role: $0.role, text: $0.text) })
                .map { ChatMessage(role: $0.role == "user" ? "user" : "assistant", content: $0.text) }
            // Chat / Work switch under the composer: Chat forces the fast conversational path (unless files were
            // attached or a source was named), Work forces the research loop, Auto keeps the routing rules.
            let chatMode = UserDefaults.standard.string(forKey: "chatMode") ?? "auto"
            let forcedChat = chatMode == "chat" && !submittedHasAttachments && !explicitInput
            let r = await Agent.shared.respond(to: effectiveText, history: history, register: register,
                preferStructuredActions: turn.kind == .work && !(continuation.pending?.amendments.isEmpty ?? true),
                allowQuickConversation: chatMode == "work" ? false : (forcedChat || (turn.kind == .conversation && !submittedHasAttachments && !explicitInput)),
                forceConversation: forcedChat || !statusContext.isEmpty,
                contextCorrection: turn.contextCorrection, discussionOnly: discussion, conversationContext: conversationContext, statusContext: statusContext,
                resumeRunIDs: (turn.kind == .work && userText.range(of: #"^\s*(?:continue|resume|go on|carry on|finish (?:it|the task))\b"#, options: [.regularExpression, .caseInsensitive]) != nil) ? (continuation.pending?.runIDs ?? []) : [],
                objectiveID: (turn.jobID ?? (submittedAsSideQuestion ? continuation.pending?.id : nil))?.uuidString.lowercased(),
                // A follow-up work request that names no source ("produce the full report with all findings", 28 Oct) continues on
                // the inputs of the turn just completed in this chat, never on an arbitrary older project file.
                objectiveInputs: (turn.kind == .work || submittedAsSideQuestion)
                    ? (continuation.pending?.inputIDs ?? ((turn.kind == .work && !submittedHasAttachments && !explicitInput && Analysis.shared.researchInputIDs(for: userText).isEmpty)
                        // The turn just completed worked on a dataset even when nothing was bound: a follow-up continues on it,
                        // never on "which file should I work on?" (29 Oct).
                        ? ((continuation.lastCompleted?.inputIDs.isEmpty == false) ? continuation.lastCompleted?.inputIDs : (continuation.lastCompleted != nil ? Analysis.shared.currentDatasetID.map { [$0] } : nil)) : nil))
                    : (discussion ? continuation.lastCompleted?.inputIDs : nil), userInstruction: userText, onTaskStarted: { [weak self] id in
                    guard turn.kind == .work else { return }
                    self?.continuation.record(taskID: id, status: "running", response: "", jobID: turn.jobID)
                    self?.saveContinuation()
                }, onReset: { [weak self] in
                self?.streaming = ""; self?.streamingReasoning = ""
            }) { [weak self] t, rz in
                Task { @MainActor in
                    if let t { self?.streaming += t }
                    if let rz { self?.streamingReasoning += rz }
                }
            }
            if turn.kind == .work, continuation.pending?.inputIDs.isEmpty == true, !r.workedInputIDs.isEmpty { continuation.pending?.inputIDs = r.workedInputIDs }
            if turn.kind == .work { continuation.record(taskID: r.taskID, status: r.status?.rawValue, response: r.text, jobID: turn.jobID); saveContinuation() }
            ExperienceLearning.shared.enqueue(r.taskID)
            let used = r.steps.map { $0.tool }
            let via = r.via + (used.isEmpty ? "" : " · " + Array(NSOrderedSet(array: used)).compactMap { $0 as? String }.joined(separator: ", "))
            let steps = r.steps.map { StepInfo(tool: $0.tool, ok: $0.ok, summary: $0.summary, outputs: $0.outputs.map(\.path), elapsed: $0.elapsed) }
            if let e = r.error, r.text.isEmpty {
                let line = Self.plain(e)
                app.panel.js(Task.isCancelled ? "pet.setState('idle')" : "pet.setState('error')")
                // A stop while the answer is being written keeps what was written (28 Oct, 22:49: 3.5 minutes of a
                // country ranking lost to "Cancelled."). The partial text is shown as Squipi's, marked as cut short.
                let partial = streaming.trimmingCharacters(in: .whitespacesAndNewlines)
                if Task.isCancelled, partial.count >= 80, !ResearchPolicy.isNotAnAnswer(partial) {
                    messages.append(Message(role: "squipi", text: partial + "\n\n(Stopped here at your request; the answer was cut short.)", via: via,
                                            reasoning: streamingReasoning, spoken: false, steps: steps, taskID: r.taskID, taskStatus: "cancelled"))
                    messages.append(Message(role: "system", text: line, via: via))
                    return
                }
                messages.append(Message(role: "system", text: line, via: via, steps: steps))
                if spoken { app.voice.say(Brain.trimSpoken(line) ?? line, force: true) }
                return
            }
            messages.append(Message(role: "squipi", text: r.text.isEmpty ? "(the model only returned reasoning)" : r.text, via: via,
                                    reasoning: r.reasoning.isEmpty ? streamingReasoning : r.reasoning, spoken: spoken, steps: steps, taskID: r.taskID, taskStatus: r.status?.rawValue))
            if spoken { app.voice.say(r.text.isEmpty ? "Done. The Chat tab has the details." : r.text, force: true) }
            if let e = r.error { messages.append(Message(role: "system", text: Self.plain(e), via: via)) }
        }
    }

    /// Status is a read of observed work, never another model generation or a new task.
    private func showPendingStatus(spoken: Bool, app: AppDelegate) async {
        guard let job = continuation.pending else {
            let line = "There is no unfinished research task saved in this chat."
            messages.append(Message(role: "squipi", text: line, via: "Saved research status"))
            if spoken { app.voice.say(line, force: true) }
            return
        }
        if job.runIDs.contains(where: { $0.hasPrefix("nemo_") }) {
            let status: String
            do { status = try await NemoRunner.shared.status(jobID: job.id) }
            catch { status = "Could not read the saved NeMo task: " + error.localizedDescription }
            messages.append(Message(role: "squipi", text: status, via: "Saved NeMo research status"))
            return
        }
        var successful = 0, failed = 0
        var outputs = Set<String>()
        var savedStatus = job.status
        var readError: String?
        for id in job.runIDs.suffix(6) {
            do {
                let record = try await Worker.shared.call("read_research_task", ["task_id": id])
                savedStatus = (record["task"] as? [String: Any])?["status"] as? String ?? savedStatus
                for step in record["steps"] as? [[String: Any]] ?? [] {
                    guard ResearchPolicy.isWorkEvidence(tool: step["tool"] as? String ?? "") else { continue }
                    if step["ok"] as? Bool == true {
                        successful += 1
                        for path in Agent.outputs(of: step["result"] as? [String: Any] ?? [:]).map(\.path) { outputs.insert(path) }
                    } else { failed += 1 }
                }
            } catch { readError = error.localizedDescription }
        }
        var line = successful == 0 ? "The requested work has not completed any verified analysis steps yet." : "The saved task has \(successful) successful work steps\(failed > 0 ? " and \(failed) failed attempts" : "")."
        line += " Status: \(savedStatus)."
        if !outputs.isEmpty { line += "\nFiles produced so far:\n" + outputs.sorted().joined(separator: "\n") }
        if job.runIDs.isEmpty { line += " The earlier conversation has been recovered, but it has no linked execution record yet." }
        if let readError { line += "\nCould not verify part of the saved record: " + readError }
        line += "\nYour original request and later constraints are saved. Say ‘continue’ to resume them."
        messages.append(Message(role: "squipi", text: line, via: "Saved research status"))
        if spoken { app.voice.say(line, force: true) }
    }

    private static func plain(_ e: String) -> String {
        if e.contains("Could not reach") && e.contains("localhost") { return "Ollama is not running on this Mac. Open the Ollama app (or run `ollama serve`), then try again. The Me tab shows the connection." }
        return e
    }
}

struct ChatView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var inWorkspace = false
    @ObservedObject var convo = Conversation.shared
    @ObservedObject var router = ModelRouter.shared
    @ObservedObject var talk = TalkState.shared
    @FocusState private var instructionFocused: Bool
    @State private var showReasoning: Set<UUID> = []
    @AppStorage("chatMode") private var chatMode = "auto"
    @State private var feedbackSheet: FeedbackTarget? = nil
    @State private var feedbackGiven: [UUID: String] = [:]

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        if convo.messages.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Talk or type. Press the mic (or double-click Squipi) and speak; she stops listening when you pause.")
                                Text(router.usesTools
                                     ? "With a dataset or document dropped on her, ask things like: “clean this and remove personal data”, “average income by district”, “is spending different for treated households?”, “regress income on age and district”, “summarise this PDF”, “write a Word report of what you found”. The numbers come from local tools; the model only decides what to run and explains the results."
                                     : "The built-in model only talks about the profile. Pick Ollama in the Me tab to let her run the data tools.")
                                Text("Attach files, type your instruction, then Send. Research memory stays across chats and brain choices. Qwen runs on this Mac; NVIDIA Nemotron or Kimi K3 via Nebius asks before sharing context with the cloud.")
                            }
                            .foregroundStyle(Color.petMuted).font(.system(size: 12, design: .rounded)).padding(.horizontal, 4)
                        }
                        ForEach(convo.messages) { m in bubble(m) }
                        if convo.busy {
                            VStack(alignment: .leading, spacing: 6) {
                                ForEach(convo.liveSteps) { st in stepRow(st) }
                                HStack(alignment: .top, spacing: 8) {
                                    ProgressView().controlSize(.small)
                                    Text(convo.streaming.isEmpty ? (convo.liveSteps.isEmpty ? "thinking…" : "working…") : convo.streaming).textSelection(.enabled)
                                }
                            }.card().id("streaming")
                        }
                    }
                    .padding(.horizontal, 16).padding(.vertical, 8)
                }
                .onChange(of: convo.messages.count) { _, _ in if let last = convo.messages.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } } }
                .onChange(of: convo.streaming) { _, _ in proxy.scrollTo("streaming", anchor: .bottom) }
            }
            if !convo.attachments.isEmpty {
                HStack(spacing: 6) {
                    ForEach(convo.attachments, id: \.self) { u in
                        HStack(spacing: 4) {
                            Image(systemName: "paperclip").font(.system(size: 10, weight: .bold))
                            Text(u.lastPathComponent).font(.system(size: 11, design: .rounded)).lineLimit(1)
                            Button { convo.removeAttachment(u) } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }.buttonStyle(.plain)
                        }
                        .padding(.horizontal, 8).padding(.vertical, 4).background(Capsule().fill(Color.petAccentSoft.opacity(0.6))).foregroundStyle(Color.petInk)
                    }
                    Spacer()
                    Text("say what to do with it, then Send").font(.system(size: 10.5, design: .rounded)).foregroundStyle(Color.petMuted)
                }.padding(.horizontal, 16).padding(.bottom, 6)
            }
            HStack(spacing: 8) {
                Button { (NSApp?.delegate as? AppDelegate)?.toggleTalk() } label: {
                    Image(systemName: talk.listening ? "stop.circle.fill" : "mic.fill").font(.system(size: 14, weight: .bold))
                        .foregroundStyle(talk.listening ? .white : Color.petAccent)
                        .frame(width: 34, height: 34)
                        .background(Circle().fill(talk.listening ? Color.petAccent : Color.petAccentSoft.opacity(0.6)))
                }
                .buttonStyle(.plain).help(talk.listening ? "Stop listening" : "Talk to Squipi")
                Button { convo.pickFiles(showChat: !inWorkspace) } label: {
                    Image(systemName: "paperclip").font(.system(size: 14, weight: .bold)).foregroundStyle(Color.petAccent)
                        .frame(width: 34, height: 34).background(Circle().fill(Color.petAccentSoft.opacity(0.6)))
                }
                .buttonStyle(.plain).help("Attach a dataset or document (or drop one on Squipi)")
                TextField(talk.listening ? "Listening… she stops when you pause" : (convo.attachments.isEmpty ? SquipiLanguage.text("Ask Squipi…") : SquipiLanguage.text("What should I do with these files?")), text: $convo.draft, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1...4).padding(10)
                    .background(RoundedRectangle(cornerRadius: 14).fill(.white))
                    .focused($instructionFocused)
                    .onSubmit { send() }
                if convo.busy {
                    Button(convo.interrupting ? "Pausing…" : "Pause & send") { send() }.buttonStyle(Pill(filled: true))
                        .disabled(convo.interrupting || convo.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button("Pause") { convo.pause() }.buttonStyle(Pill(filled: false))
                    Button(SquipiLanguage.text("Stop")) { convo.cancel() }.buttonStyle(Pill(filled: false))
                } else if talk.speaking {
                    Button("Hush") { (NSApp?.delegate as? AppDelegate)?.hush() }.buttonStyle(Pill(filled: false))
                } else {
                    Button(SquipiLanguage.text("Send")) { send() }.buttonStyle(Pill(filled: true)).disabled(convo.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 12)
            HStack(spacing: 10) {
                Picker("", selection: $chatMode) { Text("Auto").tag("auto"); Text("Chat").tag("chat"); Text("Work").tag("work") }
                    .pickerStyle(.segmented).controlSize(.mini).frame(width: 150).labelsHidden()
                    .help("Chat answers fast with no tools or task. Work runs the research loop with tools and checks. Auto decides from the message.")
                Text(router.displayLabel).font(.system(size: 10.5, weight: .semibold, design: .rounded)).foregroundStyle(Color.petMuted)
                if convo.hasSavedTask && !convo.busy {
                    Button("Resume saved task") { _ = convo.send("continue") }.buttonStyle(.plain).font(.system(size: 10.5, weight: .semibold, design: .rounded)).foregroundStyle(Color.petAccent)
                }
                if !convo.messages.isEmpty {
                    Button(SquipiLanguage.text("New chat")) { convo.clear() }.buttonStyle(.plain).font(.system(size: 10.5, weight: .semibold, design: .rounded)).foregroundStyle(Color.petAccent)
                }
            }.padding(.bottom, 8)
        }
        .environment(\.layoutDirection, SquipiLanguage.current == .ar ? .rightToLeft : .leftToRight)
        .onChange(of: convo.attachments) { _, files in if !files.isEmpty { instructionFocused = true } }
        .onAppear { if !convo.attachments.isEmpty { instructionFocused = true } }
    }

    private func send() { if convo.send(convo.draft) { convo.draft = "" } }

    /// Replies carry light Markdown (bold, code, lists); show it rather than the asterisks.
    static func markdown(_ text: String) -> Text {
        let cleaned = text.replacingOccurrences(of: "\n- ", with: "\n• ").replacingOccurrences(of: "\n* ", with: "\n• ")
        if let a = try? AttributedString(markdown: cleaned, options: AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)) { return Text(a) }
        return Text(text)
    }

    /// One tool step: what ran, how it went, and buttons for anything it produced.
    @ViewBuilder private func stepRow(_ st: Conversation.StepInfo) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: st.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill").font(.system(size: 10)).foregroundStyle(st.ok ? Color.petAccent : .orange).padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(st.tool == "forecast_series" ? KumoRuntime.displayName : st.tool.replacingOccurrences(of: "_", with: " ")).font(.system(size: 10.5, weight: .bold, design: .rounded)).foregroundStyle(Color.petMuted)
                    if st.elapsed >= 0.5 { Text(String(format: "%.0fs", st.elapsed)).font(.system(size: 10, design: .rounded)).foregroundStyle(Color.petMuted) }
                }
                Text(st.summary).font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted).lineLimit(3)
                if !st.outputs.isEmpty {
                    HStack(spacing: 4) {
                        ForEach(st.outputs.prefix(4), id: \.self) { p in
                            let u = URL(fileURLWithPath: p)
                            Button(WorkView.openLabel(u)) { openOutput(u) }.buttonStyle(Pill(filled: ["html", "docx", "pptx", "svg"].contains(u.pathExtension)))
                        }
                    }
                }
            }
        }
    }

    private func openOutput(_ u: URL) {
        guard let app = NSApp?.delegate as? AppDelegate else { return }
        if ["html", "svg"].contains(u.pathExtension) { app.workspace.show(file: u, title: u.deletingLastPathComponent().lastPathComponent, subtitle: u.path) }
        else { NSWorkspace.shared.open(u) }
    }

    @ViewBuilder private func bubble(_ m: Conversation.Message) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(m.role == "user" ? (m.spoken ? "You said" : "You") : (m.role == "squipi" ? "Squipi" : "Note")).font(.system(size: 11, weight: .bold, design: .rounded)).foregroundStyle(m.role == "user" ? Color.petAccent : Color.petMuted)
                if !m.via.isEmpty { Text(m.via).font(.system(size: 10, design: .rounded)).foregroundStyle(Color.petMuted) }
                Spacer()
                if !m.reasoning.isEmpty {
                    Button(showReasoning.contains(m.id) ? "hide reasoning" : "reasoning") { if showReasoning.contains(m.id) { showReasoning.remove(m.id) } else { showReasoning.insert(m.id) } }
                        .buttonStyle(.plain).font(.system(size: 10, design: .rounded)).foregroundStyle(Color.petMuted)
                }
                if m.role == "squipi", let taskID = m.taskID {
                    // Thumbs feed the learning store and the nightly case bank; nothing leaves the Mac.
                    if let given = feedbackGiven[m.id] {
                        Text(given == "up" ? "thanks 👍" : "noted 👎").font(.system(size: 10, design: .rounded)).foregroundStyle(Color.petMuted)
                    } else {
                        Button { feedbackSheet = FeedbackTarget(messageID: m.id, taskID: taskID, verdict: "up") } label: { Image(systemName: "hand.thumbsup").font(.system(size: 11)) }
                            .buttonStyle(.plain).foregroundStyle(Color.petMuted).help("This was good")
                        Button { feedbackSheet = FeedbackTarget(messageID: m.id, taskID: taskID, verdict: "down") } label: { Image(systemName: "hand.thumbsdown").font(.system(size: 11)) }
                            .buttonStyle(.plain).foregroundStyle(Color.petMuted).help("Something was wrong")
                    }
                }
            }
            if !m.steps.isEmpty {
                VStack(alignment: .leading, spacing: 4) { ForEach(m.steps) { st in stepRow(st) } }.padding(.bottom, 2)
            }
            Self.markdown(m.text).textSelection(.enabled)
            if showReasoning.contains(m.id) {
                Text(m.reasoning).font(.system(size: 11, design: .monospaced)).foregroundStyle(Color.petMuted).textSelection(.enabled)
            }
        }
        .card()
        .popover(item: Binding(get: { feedbackSheet.flatMap { $0.messageID == m.id ? $0 : nil } }, set: { if $0 == nil { feedbackSheet = nil } })) { target in
            FeedbackPopover(target: target) { verdict, tags, note in
                feedbackSheet = nil
                feedbackGiven[m.id] = verdict
                Task {
                    do {
                        var params: [String: Any] = ["action": "feedback", "task_id": target.taskID, "verdict": verdict, "tags": tags]
                        if !note.isEmpty { params["note"] = note }
                        _ = try await Worker.shared.learning(params)
                    } catch { NSLog("FEEDBACK not stored: %@", error.localizedDescription) }
                }
            }
        }
    }
}

struct FeedbackTarget: Identifiable, Equatable {
    let messageID: UUID
    let taskID: String
    let verdict: String
    var id: String { "\(messageID)-\(verdict)" }
}

/// One tag and an optional sentence make a thumb actionable: each tag routes to a specific mechanism
/// (wrong file → binding, wrong numbers → ledger fixtures, too slow → phase timeline, …) and every
/// rated task becomes a nightly regression case.
struct FeedbackPopover: View {
    let target: FeedbackTarget
    let send: (String, [String], String) -> Void
    @State private var tags: Set<String> = []
    @State private var note = ""
    private var options: [(String, String)] {
        target.verdict == "up"
            ? [("helpful", "Helpful"), ("accurate", "Accurate"), ("fast", "Fast enough"), ("well_formatted", "Well formatted")]
            : [("wrong_file", "Wrong file or data"), ("wrong_numbers", "Wrong numbers"), ("too_slow", "Too slow"), ("wrong_format", "Wrong format or output"), ("missed_question", "Missed the question"), ("unsafe_or_private", "Privacy or safety concern"), ("other", "Other")]
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(target.verdict == "up" ? "What went well?" : "What went wrong?").font(.system(size: 13, weight: .bold, design: .rounded))
            ForEach(options, id: \.0) { key, label in
                Toggle(label, isOn: Binding(get: { tags.contains(key) }, set: { on in if on { tags.insert(key) } else { tags.remove(key) } }))
                    .toggleStyle(.checkbox).font(.system(size: 12, design: .rounded))
            }
            TextField("Optional note (stays on this Mac)", text: $note).textFieldStyle(.roundedBorder).font(.system(size: 12))
            HStack {
                Spacer()
                Button("Send") { send(target.verdict, Array(tags).sorted(), note) }.buttonStyle(Pill(filled: true))
            }
            Text("Feedback tunes Squipi's learned procedures and becomes a local regression case. It never leaves this Mac.").font(.system(size: 10, design: .rounded)).foregroundStyle(Color.petMuted)
        }.padding(12).frame(width: 300)
    }
}

/// What Squipi has done: each entry says what was done, which data, the main result, limits, and offers Open/Reveal.
struct WorkView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @ObservedObject var analysis = Analysis.shared
    @ObservedObject var worker = Worker.shared
    @ObservedObject var pressure = MemoryPressure.shared

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if pressure.constrained {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "memorychip").foregroundStyle(.orange)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Running slowly: \(pressure.summary)").font(.system(size: 13, weight: .bold, design: .rounded))
                            Text(pressure.advice).font(.system(size: 12, design: .rounded)).foregroundStyle(Color.petMuted)
                        }
                    }.card()
                }
                HStack(spacing: 14) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Attach files and choose what to do").font(.system(size: 15, weight: .bold, design: .rounded))
                        Text("Files wait in Chat until you send an instruction. Originals stay unchanged; cleaned copies and reports go to the project folder. Data tools: \(worker.status.label).").foregroundStyle(Color.petMuted).font(.system(size: 11.5, design: .rounded))
                    }
                    Spacer()
                    Button("Attach files") { Conversation.shared.pickFiles() }.buttonStyle(Pill(filled: true))
                }.card()
                if !analysis.datasets.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        SectionTitle(text: "Datasets in this project")
                        ForEach(Array(analysis.datasets.enumerated()), id: \.offset) { _, d in
                            HStack {
                                Text(d["name"] as? String ?? "?").font(.system(size: 12.5, weight: .semibold, design: .rounded))
                                Text("\((d["rows"] as? Int ?? 0).formatted()) × \(d["cols"] as? Int ?? 0)").font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                                Spacer()
                                Text(d["id"] as? String ?? "").font(.system(size: 10, design: .monospaced)).foregroundStyle(Color.petMuted)
                                Button { analysis.forget(d["id"] as? String ?? "") } label: { Image(systemName: "xmark.circle").font(.system(size: 11)).foregroundStyle(Color.petMuted) }.buttonStyle(.plain).help("Remove from this project (the file stays)")
                            }
                        }
                    }.card()
                }
                if !analysis.documents.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        SectionTitle(text: "Documents in this project")
                        ForEach(Array(analysis.documents.enumerated()), id: \.offset) { _, d in
                            HStack {
                                Text(d["name"] as? String ?? "?").font(.system(size: 12.5, weight: .semibold, design: .rounded))
                                Text("\((d["pages"] as? Int ?? 0).formatted()) \(d["kind"] as? String == "pptx" ? "slides" : (d["kind"] as? String == "pdf" ? "pages" : "sections")) · \((d["words"] as? Int ?? 0).formatted()) words").font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                                Spacer()
                                Text(d["id"] as? String ?? "").font(.system(size: 10, design: .monospaced)).foregroundStyle(Color.petMuted)
                                Button { analysis.forget(d["id"] as? String ?? "") } label: { Image(systemName: "xmark.circle").font(.system(size: 11)).foregroundStyle(Color.petMuted) }.buttonStyle(.plain).help("Remove from this project (the file stays)")
                            }
                        }
                    }.card()
                }
                if analysis.entries.isEmpty {
                    Text("Nothing yet. The first overview takes a few seconds; the first ever drop also sets up the Python tools once.")
                        .foregroundStyle(Color.petMuted).font(.system(size: 12, design: .rounded)).padding(.horizontal, 4)
                }
                ForEach(analysis.entries) { e in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Image(systemName: e.ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill").foregroundStyle(e.ok ? Color.petAccent : .orange)
                            Text(e.title).font(.system(size: 13.5, weight: .bold, design: .rounded))
                            Spacer()
                            Text(e.date.formatted(date: .omitted, time: .shortened)).font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                        }
                        row("Data", e.inputs)
                        row("Result", e.result)
                        if !e.limits.isEmpty { row("Limits", e.limits) }
                        if !e.outputs.isEmpty {
                            HStack(spacing: 6) {
                                ForEach(e.outputs, id: \.self) { u in
                                    Button(Self.openLabel(u)) { open(u) }.buttonStyle(Pill(filled: ["html", "docx", "pptx", "svg"].contains(u.pathExtension)))
                                }
                                Button("Reveal") { NSWorkspace.shared.activateFileViewerSelecting(e.outputs) }.buttonStyle(Pill(filled: false))
                            }
                        }
                    }.card()
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 16).padding(.top, 8)
        }
    }

    static func openLabel(_ u: URL) -> String {
        switch u.pathExtension {
        case "html": return "Open report"
        case "svg": return "Open chart"
        case "docx": return "Open Word file"
        case "pptx": return "Open slides"
        case "csv": return "Open CSV"
        case "md": return "Open text"
        default: return "Open \(u.pathExtension)"
        }
    }

    private func row(_ k: String, _ v: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(k).font(.system(size: 11, weight: .bold, design: .rounded)).foregroundStyle(Color.petMuted).frame(width: 46, alignment: .leading)
            Text(v).font(.system(size: 12, design: .rounded)).textSelection(.enabled)
        }
    }

    private func open(_ u: URL) {
        guard let app = NSApp?.delegate as? AppDelegate else { return }
        if ["html", "svg"].contains(u.pathExtension) { app.workspace.show(file: u, title: u.deletingLastPathComponent().lastPathComponent, subtitle: u.path) }
        else { NSWorkspace.shared.open(u) }
    }
}
