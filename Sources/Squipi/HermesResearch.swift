import Foundation

/// Shared production Hermes workflow, used by Chat and isolated end-to-end checks.
@MainActor
extension HermesRunner {
    func research(instruction: String, history: [ChatMessage], spoken: Bool = false,
                  configuration: HermesLocalConfiguration,
                  onActivity: ((String) -> Void)? = nil) async -> Agent.Reply {
        let personalInstructions = UserInstructions.shared.snapshot()
        let savedTemplates = SavedTemplates.shared.snapshot()
        let inputs = Analysis.shared.researchInputIDs(for: instruction)
        let session = await ResearchSession.open(instruction: instruction, inputs: inputs)
        let recentHistory = history.map { "\($0.role == "user" ? "User" : "Assistant"): \($0.content)" }.joined(separator: "\n\n")
        let prompt = Agent.shared.hermesPrompt(for: instruction, spoken: spoken, userInstructions: personalInstructions.promptSection, savedTemplates: savedTemplates.promptSection)
            + (recentHistory.isEmpty ? "" : "\n\nRecent conversation (context only; the current task and latest user constraints above take precedence):\n" + recentHistory) + "\n\n" + session.context
            + (session.taskID.map { "\nCurrent research task id: \($0). Save the plan using this id. Execution events are recorded by the MCP server." } ?? "")
        var r = await HermesRunner.shared.ask(prompt + SquipiLanguage.prompt, configuration: configuration, researchTaskID: session.taskID, templateDefaults: savedTemplates.defaults, onActivity: onActivity)
        var recorded: [[String: Any]] = []
        var readIssue: String?
        var planComplete: Bool?
        let documentIDs = Analysis.shared.documents.compactMap { $0["id"] as? String }.filter { inputs.contains($0) }
        let needsWholeReading = !documentIDs.isEmpty && ResearchPolicy.requiresWholeDocumentReading(instruction: instruction)
        var documentAudit: ResearchPolicy.DocumentAudit?
        var previousCoverage = -1
        var retriedExecution = false
        var missingArtifacts: [String] = []
        // A model can stop early even with remaining cursors. Verify actual journaled spans
        // and resume supported reading automatically; a repeated stall remains explicitly partial.
        for attempt in 0..<3 {
            if let id = session.taskID {
                do {
                    let saved = try await Worker.shared.call("read_research_task", ["task_id": id])
                    recorded = saved["steps"] as? [[String: Any]] ?? []
                    planComplete = ResearchPolicy.planCompletion(inTaskResult: saved)
                } catch { readIssue = "Could not verify the saved Hermes steps: \(error.localizedDescription)" }
            }
            if ResearchPolicy.isAwaitingHost(r.text), attempt < 2, r.error == nil, !Task.isCancelled {
                let resumed = await HermesRunner.shared.ask(prompt + "\n\nYour last answer only printed a host-waiting status. There is no pending host request. Issue the structured tool call now, or answer from the actual evidence already returned. read_document reads attached source files; read_research_task only reads execution history. Continue the original task and do not return another placeholder.", configuration: configuration, researchTaskID: session.taskID, templateDefaults: savedTemplates.defaults, onActivity: onActivity)
                let elapsed = r.seconds + resumed.seconds
                r = resumed; r.seconds = elapsed
                continue
            }
            missingArtifacts = ResearchPolicy.missingArtifacts(instruction: instruction, steps: recorded)
            let noWork = !recorded.contains { $0["ok"] as? Bool == true && ResearchPolicy.isWorkEvidence(tool: $0["tool"] as? String ?? "") }
            if !retriedExecution, attempt < 2, r.error == nil, !Task.isCancelled,
               ResearchPolicy.requiresExecution(instruction: instruction, hasInputs: !inputs.isEmpty), noWork || !missingArtifacts.isEmpty {
                retriedExecution = true
                let missing = missingArtifacts.isEmpty ? "No successful work tool has run." : "Required outputs still missing: " + missingArtifacts.joined(separator: ", ")
                let resumed = await HermesRunner.shared.ask(prompt + "\n\nThe host checked the saved execution record. " + missing + " Continue the original task using structured tools now. User answers and exceptions are already recorded in the current task. Do not repeat an answered clarification or ask for an output filename. Inspect the source if a column is uncertain, make routine reversible choices, then produce the requested files. If an essential input is truly missing, explain exactly what is missing.", configuration: configuration, researchTaskID: session.taskID, templateDefaults: savedTemplates.defaults, onActivity: onActivity)
                let elapsed = r.seconds + resumed.seconds
                r = resumed; r.seconds = elapsed
                continue
            }
            guard needsWholeReading else { break }
            let audit = ResearchPolicy.documentAudit(ids: documentIDs, steps: recorded)
            documentAudit = audit
            guard !audit.complete, audit.canContinue, attempt < 2, r.error == nil,
                  readIssue == nil, !Task.isCancelled, audit.coveredCharacters > previousCoverage else { break }
            previousCoverage = audit.coveredCharacters
            onActivity?("reading")
            let resumed = await HermesRunner.shared.ask(prompt + "\n\nThe host verified actual reading coverage and found gaps:\n\(audit.notice)\nContinue from these cursors. Use the current research task's saved steps to recover earlier evidence. Read the remaining spans, combine their evidence with earlier findings, update the plan honestly and answer the original request. The earlier answer does not establish coverage.\n<earlier_answer>\n\(r.text)\n</earlier_answer>", configuration: configuration, researchTaskID: session.taskID, templateDefaults: savedTemplates.defaults, onActivity: onActivity)
            let elapsed = r.seconds + resumed.seconds
            r = resumed; r.seconds = elapsed
        }
        if let audit = documentAudit, !audit.complete { readIssue = [readIssue, "Document reading is incomplete. " + audit.notice].compactMap { $0 }.joined(separator: "\n") }
        if ResearchPolicy.isAwaitingHost(r.text) {
            readIssue = [readIssue, "The model stopped at a proposed step without issuing the required tool call."].compactMap { $0 }.joined(separator: "\n")
            r.text = "I could not complete this task: the model returned an unexecuted step after retrying. The actual completed tool steps are saved in research history."
        }
        missingArtifacts = ResearchPolicy.missingArtifacts(instruction: instruction, steps: recorded)
        if !missingArtifacts.isEmpty { readIssue = [readIssue, "Requested outputs are still missing: " + missingArtifacts.joined(separator: ", ")].compactMap { $0 }.joined(separator: "\n") }
        let status = ResearchPolicy.completionStatus(finalText: r.text, finishReason: r.error == nil ? "stop" : "error",
            successfulCalls: recorded.filter { $0["ok"] as? Bool == true && ResearchPolicy.isWorkEvidence(tool: $0["tool"] as? String ?? "") }.count,
            failedCalls: ResearchPolicy.unresolvedFailureCount(recorded.map { ($0["tool"] as? String ?? "unknown", $0["ok"] as? Bool ?? false) }),
            cancelled: Task.isCancelled, requiresToolEvidence: ResearchPolicy.requiresExecution(instruction: instruction, hasInputs: !inputs.isEmpty),
            requirementsMet: readIssue == nil ? planComplete : false)
        await session.finish(status: status, summary: r.text.isEmpty ? (r.error ?? "No answer") : r.text)
        let steps = recorded.map { entry -> Agent.Step in
            let result = entry["result"] as? [String: Any] ?? [:]
            let step = Agent.Step(tool: entry["tool"] as? String ?? "tool", args: entry["arguments"] as? [String: Any] ?? [:], ok: entry["ok"] as? Bool ?? false,
                summary: result["summary_text"] as? String ?? result["error"] as? String ?? "Recorded tool result", outputs: Agent.outputs(of: result), elapsed: 0)
            if ResearchPolicy.isWorkEvidence(tool: step.tool) { Analysis.shared.record(step: step, result: result) }
            return step
        }
        let issues = [r.error, readIssue, session.persistenceNotice].compactMap { $0 }
        return Agent.Reply(text: r.text, via: "Hermes · on this Mac · " + configuration.model,
                           steps: steps, error: issues.isEmpty ? nil : issues.joined(separator: "\n"),
                           taskID: session.taskID, status: status)
    }
}
