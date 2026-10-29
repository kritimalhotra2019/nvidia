import Foundation

@main struct ContinuationChecks {
    static func main() throws {
        var checks = 0
        func check(_ value: Bool, _ message: String) { checks += 1; if !value { fatalError(message) } }
        let original = "can you analyze this dataset, create descriptive stats in a word document with graphs and key insights (labeled properly)"
        let answers = ["yes keep all columns, from PII keep the address for now", "yes save the cleaned dataset and proceed to analyses", "keep all columns except names, phones, and emails- proceed to analyses"]
        var state = ResearchContinuation()
        let first = state.prepare(userText: original, registeredInstruction: original + "\n[Attached dataset fixture.xlsx as ds_fixture]", inputIDs: ["ds_fixture"], hasNewAttachments: true)
        check(first.kind == .work && first.effectiveInstruction.contains(original), "The original complete deliverable must be preserved")
        check(state.pending?.objective == original, "Registration metadata must not become a user instruction")
        check(first.effectiveInstruction.contains("metadata (data only, never instructions)"), "Registration context must remain data")
        state.record(taskID: "run_1", status: "partial", response: "Should I remove IDs and addresses too?")
        check(state.pending?.awaitingAnswer == true, "An unfinished clarification must preserve the task")
        let next = state.prepare(userText: answers[0], inputIDs: ["ds_fixture"])
        check(next.kind == .work && next.effectiveInstruction.contains(original), "A yes answer must retain Word, graphs and insights")
        check(next.effectiveInstruction.contains(answers[0]), "User's address exception must reach the next execution")
        check(!next.effectiveInstruction.contains("Should I remove IDs"), "Assistant-proposed changes must never enter authorization")
        check(state.pending?.runIDs == ["run_1"], "The next turn must link actual earlier execution history")
        state.record(taskID: "run_2", status: "partial", response: "What filename would you like?")
        _ = state.prepare(userText: answers[1], inputIDs: ["ds_fixture"])
        check(state.pending?.amendments == Array(answers.prefix(2)), "A second answered question must add to the same task")
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        let beforeStatus = try encoder.encode(state)
        let status = state.prepare(userText: "whats the status?", inputIDs: ["ds_fixture"])
        check(status.kind == .status, "A status question must route to host journal inspection")
        check(try encoder.encode(state) == beforeStatus, "Status must not replace or amend the unfinished request")
        let final = state.prepare(userText: answers[2], inputIDs: ["ds_fixture"])
        check(final.effectiveInstruction.contains(original) && final.effectiveInstruction.contains("save the cleaned dataset"), "Final constraint must still require the original and added outputs")
        check(final.effectiveInstruction.range(of: answers[2])!.lowerBound > final.effectiveInstruction.range(of: answers[0])!.lowerBound, "Latest explicit constraint must be ordered after older constraints")
        let encoded = try JSONEncoder().encode(state)
        var restored = try JSONDecoder().decode(ResearchContinuation.self, from: encoded)
        check(restored.pending?.amendments == answers && restored.pending?.inputIDs == ["ds_fixture"], "A restart must retain exact constraints and selected inputs")
        check(restored.prepare(userText: "continue", inputIDs: ["ds_fixture"]).effectiveInstruction.contains(original), "A backend switch or restart can resume the same task")
        restored.record(taskID: "run_3", status: "completed", response: "Actual files are ready.")
        check(restored.pending == nil, "Verified completion must clear the pending task")
        check(restored.prepare(userText: "status?", inputIDs: []).kind == .status, "Status with no pending task must still avoid generating a new task")

        var recovered = ResearchContinuation.recover(from: [
            .init(role: "user", text: original + "\n📎 fixture.xlsx"),
            .init(role: "squipi", text: "Remove IDs and pseudonymize household_id?"),
            .init(role: "system", text: "This task is incomplete."),
            .init(role: "user", text: answers[0]),
            .init(role: "squipi", text: "What should the cleaned dataset be named?"),
            .init(role: "user", text: answers[1]),
            .init(role: "squipi", text: "Remove IDs and pseudonymize household_id?"),
            .init(role: "user", text: "whats the status?"),
            .init(role: "squipi", text: "Remove IDs and pseudonymize household_id?"),
            .init(role: "user", text: answers[2]),
            .init(role: "squipi", text: "Remove IDs and pseudonymize household_id?"),
            .init(role: "system", text: "This task is incomplete.")])
        check(recovered.pending?.objective == original && recovered.pending?.amendments == answers, "Upgrade must recover the exact broken conversation without status noise")
        let recoveredTurn = recovered.prepare(userText: "continue", inputIDs: ["ds_fixture"])
        check(recoveredTurn.effectiveInstruction.contains(answers[2]) && !recoveredTurn.effectiveInstruction.contains("pseudonymize household_id"), "Migration must not promote assistant suggestions")
        check(recovered.pending?.inputIDs == ["ds_fixture"], "A recovered filename-only job must adopt its registered source ID")
        let different = recovered.prepare(userText: "analyze this new dataset", inputIDs: ["ds_other"], hasNewAttachments: true)
        check(different.kind == .work && recovered.pending?.amendments.isEmpty == true && !different.effectiveInstruction.contains("names, phones"), "A newly attached file must not inherit earlier cleaning constraints")
        _ = recovered.prepare(userText: "What is the weather tomorrow?", inputIDs: ["ds_other"])
        check(recovered.pending != nil, "An independent question preserves the paused research objective without replaying it")
        var boundary = state
        _ = boundary.prepare(userText: "keep only age", inputIDs: ["ds_unrelated"], inputSelectionIsExplicit: true)
        check(boundary.pending?.inputIDs != ["ds_fixture"], "Explicit selection of a different input cannot continue the old file")
        var derived = state
        let afterCleaning = derived.prepare(userText: "continue", inputIDs: ["ds_cleaned_copy"])
        check(afterCleaning.kind == .work && afterCleaning.effectiveInstruction.contains(original) && derived.pending?.inputIDs == ["ds_fixture"], "A tool changing the active input to a cleaned derivative must not discard the original objective")
        var cancelled = state; cancelled.cancel()
        check(cancelled.pending?.status == "cancelled", "Cancellation must persist without restarting work")
        let cancelledStatus = cancelled.prepare(userText: "what's the status?", inputIDs: ["ds_fixture"])
        check(cancelledStatus.kind == .status && cancelled.pending?.status == "cancelled", "Status must not resume a cancelled task")
        check(cancelled.prepare(userText: "yes", inputIDs: ["ds_fixture"]).kind == .paused, "An ambiguous confirmation must not restart cancelled work")
        check(cancelled.prepare(userText: "continue", inputIDs: ["ds_fixture"]).kind == .work, "Explicit resume can continue the saved task")
        let oldJobID = cancelled.pending!.id
        _ = cancelled.prepare(userText: "analyze this new dataset", inputIDs: ["ds_other"], hasNewAttachments: true)
        cancelled.record(taskID: "late_old_run", status: "completed", response: "Done", jobID: oldJobID)
        check(cancelled.pending?.inputIDs == ["ds_other"], "A cancelled run finishing late must not clear a newer job")
        check(cancelled.prepare(userText: "cancel this", inputIDs: ["ds_other"]).kind == .paused, "An explicit stop request must not launch another model task")
        let completeHistory = ResearchContinuation.recover(from: [.init(role: "user", text: original + "\n📎 fixture.xlsx"), .init(role: "squipi", text: "Done", taskID: "done", status: "completed")])
        check(completeHistory.pending == nil, "Migration must not revive verified completed tasks")
        let latestLegacyAnswer = "YES KEEP ALL AND DON'T ASK AGAIN- JUST EXECUTE THE TASK"
        let legacyCancelled: [ResearchContinuation.HistoryEntry] = [
            .init(role: "user", text: original + "\n📎 fixture.xlsx"),
            .init(role: "squipi", text: "Remove all PII, including addresses and IDs?"),
            .init(role: "user", text: answers[0]),
            .init(role: "system", text: "Cancelled."),
            .init(role: "user", text: "keep all non-pii columns and produce a word doc with analyses, visuals and key insights"),
            .init(role: "system", text: "Canceled."),
            .init(role: "user", text: latestLegacyAnswer),
            .init(role: "system", text: "Cancelled.")]
        var legacy = ResearchContinuation.recover(from: legacyCancelled)
        check(legacy.pending?.objective == original && legacy.pending?.status == "cancelled", "Legacy system cancellation must preserve the original stopped request")
        check(legacy.pending?.amendments.last == latestLegacyAnswer && legacy.pending?.amendments.count == 3, "Later legacy user answers must survive earlier cancelled turns")
        check(legacy.prepare(userText: "yes", inputIDs: ["ds_fixture"]).kind == .paused, "Migration must not turn cancellation into automatic authorization to restart")
        let legacyResume = legacy.prepare(userText: "continue", inputIDs: ["ds_fixture"])
        check(legacyResume.kind == .work && legacyResume.effectiveInstruction.contains(latestLegacyAnswer), "Explicit continuation must restore the latest legacy answer")
        let emptySaved = try JSONDecoder().decode(ResearchContinuation.self, from: Data("{}".utf8))
        let recoveredEmpty = ResearchContinuation.restoring(saved: emptySaved, history: legacyCancelled)
        check(recoveredEmpty.pending?.status == "cancelled" && recoveredEmpty.pending?.amendments.last == latestLegacyAnswer, "A saved nil from an older migration must not prevent cancellation recovery on load")
        let completedEntries = legacyCancelled + [.init(role: "squipi", text: "Done", taskID: "verified", status: "completed")]
        check(ResearchContinuation.restoring(saved: emptySaved, history: completedEntries).pending == nil, "Saved-nil fallback must not resurrect a task with completed metadata")
        check(ResearchContinuation.restoring(saved: emptySaved, history: []).pending == nil, "New chat must remain empty after saved-nil fallback")
        let unrelatedEntries = legacyCancelled + [.init(role: "user", text: "What is the weather tomorrow?"), .init(role: "system", text: "Cancelled.")]
        check(ResearchContinuation.restoring(saved: emptySaved, history: unrelatedEntries).pending?.status == "cancelled", "A cancelled side conversation preserves the stopped research job without running it")
        let currentSaved = state
        check(ResearchContinuation.restoring(saved: currentSaved, history: legacyCancelled).pending?.id == currentSaved.pending?.id, "A current saved pending job must win over historical cancellation markers")
        let statusAfterCancel = legacyCancelled + [.init(role: "user", text: "what's the status?"), .init(role: "squipi", text: "The task is stopped. Say continue to resume.")]
        check(ResearchContinuation.restoring(saved: emptySaved, history: statusAfterCancel).pending?.status == "cancelled", "A status-only answer must not erase recovered cancellation")
        var noisy: [ResearchContinuation.HistoryEntry] = [.init(role: "user", text: original)]
        for _ in 0..<20 { noisy.append(.init(role: "system", text: "This task is incomplete.")) }
        noisy.append(.init(role: "user", text: answers[2]))
        let history = ResearchContinuation.boundedHistory(noisy, maxMessages: 2)
        check(history.count == 2 && history.first?.text == original, "System notices must be filtered before the history limit")
        check(ResearchContinuation.boundedHistory(noisy, maxCharacters: 40).reduce(0) { $0 + $1.text.count } <= 40, "History must remain bounded for local model context")
        let brief = "Create a one-page Word document and matching PowerPoint about the benefits of walking. Use my saved report colours."
        var documentOnly = ResearchContinuation()
        let briefTurn = documentOnly.prepare(userText: brief, inputIDs: [])
        check(briefTurn.kind == .work && documentOnly.pending?.objective == brief && documentOnly.pending?.inputIDs.isEmpty == true, "A document brief with no attachment must create a pending artifact task")
        documentOnly.record(taskID: "brief_run", status: "partial", response: "Should I use your saved colours for both files?")
        let answerTurn = documentOnly.prepare(userText: "Yes, use the saved colours and create both files.", inputIDs: [])
        check(answerTurn.kind == .work && answerTurn.effectiveInstruction.contains(brief), "A short answer must retain the no-input Word and PowerPoint objective")
        check(answerTurn.effectiveInstruction.contains("Yes, use the saved colours") && documentOnly.pending?.runIDs == ["brief_run"], "No-input artifact follow-ups must preserve the answer and prior execution reference")
        let briefSaved = try JSONEncoder().encode(documentOnly)
        var briefRestored = try JSONDecoder().decode(ResearchContinuation.self, from: briefSaved)
        check(briefRestored.prepare(userText: "continue", inputIDs: []).effectiveInstruction.contains(brief), "An input-free artifact objective must survive relaunch")
        _ = briefRestored.prepare(userText: "What is the weather tomorrow?", inputIDs: [])
        check(briefRestored.pending != nil, "An independent question must preserve the input-free artifact objective")
        var briefLegacy = ResearchContinuation.recover(from: [
            .init(role: "user", text: brief),
            .init(role: "squipi", text: "Should I use your saved colours?", taskID: "legacy_brief", status: "partial")])
        let legacyBriefAnswer = briefLegacy.prepare(userText: "Yes, proceed.", inputIDs: [])
        check(legacyBriefAnswer.kind == .work && legacyBriefAnswer.effectiveInstruction.contains(brief), "Legacy replay must recover a no-attachment document request before its answer")
        let creationRequests = [
            "Please create a Word doc on gardening.",
            "Can you please write a report about walking?",
            "Could you make a presentation about walking?",
            "Would you generate a document explaining the plan?",
            "Please build five slides from this brief: healthier streets.",
            "Export this as Word: Meeting agenda.",
            "Generate a DOCX brief about survey design.",
            "Will you please create a PPTX with three slides?"
        ]
        for request in creationRequests {
            var fixture = ResearchContinuation()
            check(fixture.prepare(userText: request, inputIDs: []).kind == .work, "Explicit no-input artifact request was lost: \(request)")
        }
        for question in ["How do I create a Word doc?", "What is PowerPoint?", "Can you explain how to create a presentation?", "Please tell me how to export a DOCX.", "Would you tell me how to make slides?", "Write the word hello."] {
            var fixture = ResearchContinuation()
            check(fixture.prepare(userText: question, inputIDs: []).kind == .conversation && fixture.pending == nil, "Informational text must not start artifact execution: \(question)")
        }
        documentOnly.record(taskID: "question", status: "partial", response: "Should I continue the walking brief?")
        let replacementBrief = "Generate a presentation about a new school project."
        let replacementTurn = documentOnly.prepare(userText: replacementBrief, inputIDs: [])
        check(replacementTurn.kind == .work && documentOnly.pending?.objective == replacementBrief && documentOnly.pending?.amendments.isEmpty == true, "An independently phrased artifact request must replace the earlier pending objective")
        check(!replacementTurn.effectiveInstruction.contains("benefits of walking"), "New artifact topics must not inherit old content or style amendments")
        documentOnly.record(taskID: "another_question", status: "partial", response: "Should I use a blue title?")
        let politeReplacement = "Would you create a document about nutrition instead."
        let politeReplacementTurn = documentOnly.prepare(userText: politeReplacement, inputIDs: [])
        check(politeReplacementTurn.kind == .work && documentOnly.pending?.objective == politeReplacement && documentOnly.pending?.amendments.isEmpty == true, "A polite independent artifact request without a question mark must start a new objective")
        var social = ResearchContinuation()
        _ = social.prepare(userText: "Clean the data and make a Word report", inputIDs: ["ds_keep"])
        let savedSocialJob = social.pending?.id
        let greeting = social.prepare(userText: "hi", inputIDs: ["ds_keep"])
        check(greeting.kind == .conversation && social.pending?.id == savedSocialJob, "A greeting must preserve the unfinished research task")
        check(ResearchContinuation.isLightweightChat("नमस्ते"), "Hindi greeting uses lightweight chat")
        check(!ResearchContinuation.isLightweightChat("Hi, remove names from my data"), "Mixed greetings and work must retain tools")
        for question in ["hi how are you", "Hi! How are you?", "Hi Swipey, how are you?", "what all can you do?", "what's your fav colour", "hello, how are you doing today?", "Hi\nhow are you?", "What is inflation?", "Can you explain sampling bias?", "Why does the sky look blue?"] {
            check(ResearchContinuation.isLightweightChat(question), "Short conversation: " + question)
        }
        for instruction in ["Hi! Remove names from my data", "What is my name?", "What is in this document?", "What is the latest inflation rate?", "What is today's weather?", "What is the average income in households.csv?", "Explain regression and create a report", "Explain that again", "Can you remind me tomorrow?", "Continue", "What is 389 times 762?", "What is the status?"] {
            check(!ResearchContinuation.isLightweightChat(instruction), "Needs context or tools: " + instruction)
        }
        let explanation = social.prepare(userText: "What is sampling bias?", inputIDs: ["ds_keep"])
        check(explanation.kind == .conversation && social.pending?.id == savedSocialJob, "A standalone explanation preserves the unfinished task")
        let attachedGreeting = social.prepare(userText: "hi", inputIDs: ["ds_new"], hasNewAttachments: true)
        check(attachedGreeting.kind != .conversation, "An attached file must not enter short chat")
        let chatHistory: [ResearchContinuation.HistoryEntry] = [.init(role: "user", text: "Hi how are you"), .init(role: "squipi", text: "I'm hanging out with friends.")]
        for followup in ["what friends", "Good too, what's up", "Why did you say that?"] {
            check(ResearchContinuation.isLightweightChat(followup, history: chatHistory), "Conversational follow-up retains short path: " + followup)
        }
        for action in ["yes", "remove names", "What is in my file?", "How can you create a report?", "Remind me tomorrow", "continue"] {
            check(!ResearchContinuation.isLightweightChat(action, history: chatHistory), "Follow-up must not bypass tools: " + action)
        }
        let researchHistory: [ResearchContinuation.HistoryEntry] = [.init(role: "user", text: "Remove PII from data"), .init(role: "squipi", text: "Which columns?")]
        check(!ResearchContinuation.isLightweightChat("what do you mean", history: researchHistory), "Research follow-up retains research context")
        var preserved = ResearchContinuation()
        _ = preserved.prepare(userText: "Clean the dataset", inputIDs: ["ds_keep"])
        let preservedID = preserved.pending?.id
        check(preserved.prepare(userText: "what friends", inputIDs: ["ds_keep"], history: chatHistory).kind == .conversation && preserved.pending?.id == preservedID, "Casual follow-up must preserve pending work")
        let unfinished: [ResearchContinuation.HistoryEntry] = [.init(role: "user", text: "Clean the data\n📎 households.xlsx"), .init(role: "squipi", text: "Timed out", taskID: "old", status: "partial")]
        let restoredChat = ResearchContinuation.recover(from: unfinished + chatHistory + [.init(role: "user", text: "what friends"), .init(role: "squipi", text: "I meant I'm here to chat.", status: "completed")])
        check(restoredChat.pending?.objective == "Clean the data", "Reopening after a completed casual follow-up must retain unfinished research")
        var durable = ResearchContinuation()
        _ = durable.prepare(userText: "Combine both datasets and create a policy brief", inputIDs: ["ds_a"])
        let objective = durable.pending!.id
        durable.record(taskID: "task_saved", status: "partial", response: "Saved progress")
        let extra = durable.prepare(userText: "attached both datasets", registeredInstruction: "attached both datasets\n[ds_b registered]", inputIDs: ["ds_a","ds_b"], hasNewAttachments: true)
        check(extra.kind == .work && durable.pending!.id == objective && durable.pending!.inputIDs == ["ds_a","ds_b"], "Attachment-only followup extends the same objective and selected inputs")
        check(extra.effectiveInstruction.contains("Combine both datasets"), "Adding attachments does not replace the requested deliverable")
        let durableEncoder = JSONEncoder(); durableEncoder.outputFormatting = .sortedKeys
        let beforeQuestion = try durableEncoder.encode(durable)
        check(durable.prepare(userText: "What does this result mean?", inputIDs: [], sideQuestion: true).kind == .conversation, "Explicit side question does not become an amendment")
        check(try durableEncoder.encode(durable) == beforeQuestion, "Side question preserves exact run ids, inputs and constraints")
        durable.cancel()
        var restart = try JSONDecoder().decode(ResearchContinuation.self, from: JSONEncoder().encode(durable))
        let resumed = restart.prepare(userText: "resume", inputIDs: [])
        check(resumed.kind == .work && restart.pending!.id == objective && restart.pending!.runIDs == ["task_saved"], "Stop and process restart preserve the objective and prior execution handles")
        check(resumed.effectiveInstruction.contains("Research objective id:"), "Every run carries stable objective identity")
        check(ResearchContinuation.isSideQuestion("What does confidence interval mean?"), "Questions can interrupt work")
        check(!ResearchContinuation.isSideQuestion("Drop the email column and continue"), "A direct work amendment is not treated as a side question")
        var blocked = ResearchContinuation()
        _ = blocked.prepare(userText: "Analyse this workbook", inputIDs: [], hasNewAttachments: true)
        blocked.pending?.blockedInput = "Required attachment failed"
        check(blocked.prepare(userText: "continue", inputIDs: ["stale_dataset"]).kind == .paused, "Resume cannot silently substitute an old dataset after attachment failure")
        check(blocked.pending?.inputIDs.isEmpty == true, "Blocked input scope remains empty")
        _ = blocked.prepare(userText: "here is the replacement", inputIDs: ["new_dataset"], hasNewAttachments: true)
        check(blocked.pending?.blockedInput == nil && blocked.pending?.inputIDs == ["new_dataset"], "A successfully registered replacement unblocks the task")
        var mac = ResearchContinuation()
        check(mac.prepare(userText: "Open TextEdit", inputIDs: []).kind == .work && mac.pending?.objective == "Open TextEdit", "Mac work without attachments retains a durable objective")
        check(ResearchContinuation.requestsMacAction("List running applications"), "Live application lists require Mac tools")
        check(!ResearchContinuation.requestsMacAction("What does opening an app mean?"), "Mac explanations do not request actions")
        for question in ["what all research tools do you have", "Who are you?", "what are your research skills", "list all your research tools", "what's your personality"] {
            check(ResearchContinuation.isCapabilityQuestion(question) && ResearchContinuation.isLightweightChat(question), "Identity/capability questions must receive the personality directory without starting research: \(question)")
        }
        var corrected = ResearchContinuation()
        let goal = "Analyse the cases and create a Word report with graphs"
        _ = corrected.prepare(userText: goal, inputIDs: ["cases"])
        let sameJob = corrected.pending!.id
        let correction = corrected.prepare(userText: "Ah UP is a state in India, Uttar Pradesh", inputIDs: ["cases"])
        check(correction.kind == .work && !correction.contextCorrection && correction.jobID == sameJob && correction.effectiveInstruction.contains(goal), "A contextual correction must preserve unfinished work")
        corrected.record(taskID: "morning", status: "completed", response: "Report saved", jobID: sameJob)
        let after = corrected.prepare(userText: "UP stands for Uttar Pradesh", inputIDs: ["cases"])
        check(after.kind == .conversation && after.contextCorrection && after.effectiveInstruction.contains(goal) && after.effectiveInstruction.contains("do not restart"), "Completed context correction must not restart work or ask for its objective")
        let restoredCorrection = try JSONDecoder().decode(ResearchContinuation.self, from: JSONEncoder().encode(corrected))
        check(restoredCorrection.lastCompleted?.amendments.last == "UP stands for Uttar Pradesh", "Completed context survives restart")
        check(ResearchContinuation.restoring(saved: restoredCorrection, history: []).lastCompleted?.amendments.last == "UP stands for Uttar Pradesh", "Bounded or empty history must not erase saved completed context")
        check(!ResearchContinuation.isContextCorrection("Actually create a report about UP"), "Fresh deliverables must not be swallowed as corrections")
        check(!ResearchContinuation.isContextCorrection("New task: UP means Uttar Pradesh"), "An explicit new topic must not attach to prior work")
        check(!ResearchContinuation.isContextCorrection("UP means Uttar Pradesh, now create a report"), "A correction containing new work must not swallow that action")
        let oldState = try JSONDecoder().decode(ResearchContinuation.self, from: Data("{}".utf8))
        check(oldState.lastCompleted == nil, "Older saved continuation states remain decodable")
        var files = ResearchContinuation()
        let fileGoal = "Find the PDF evaluation report in my Downloads, read and summarise its limitations"
        let request = files.prepare(userText: fileGoal, inputIDs: [])
        check(request.kind == .work && files.pending?.objective == fileGoal, "A folder lookup retains the whole research goal before import")
        files.pending?.inputIDs = ["old_doc"]
        files.record(taskID: "old_wrong_source", status: "partial", response: "Need the requested file")
        let replacement = files.prepare(userText: "/Downloads/New report.pdf", inputIDs: [], inputSelectionIsExplicit: true)
        check(replacement.kind == .work && replacement.effectiveInstruction.contains(fileGoal), "A path-only follow-up retains the original reading objective")
        check(files.pending?.inputIDs.isEmpty == true && files.pending?.id != request.jobID, "A new exact path retires the stale source contract")
        let arrival = files.prepare(userText: "the PDF file wasn't there earlier, but it is now", inputIDs: [])
        check(arrival.kind == .work && arrival.effectiveInstruction.contains(fileGoal), "A newly arrived file continues the original research request")
        print("Continuation checks passed: \(checks)")
    }
}
