import Foundation

@MainActor final class Store {
    static let shared = Store()
    let dir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("squipi-unused-instruction-fixture")
}

@main struct UserInstructionChecks {
    @MainActor static func main() throws {
        var checks = 0
        func check(_ condition: Bool, _ message: String) { checks += 1; if !condition { fatalError(message) } }
        func rejects(_ message: String, _ operation: () throws -> Void) {
            checks += 1
            do { try operation(); fatalError(message) } catch { }
        }
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("squipi-instructions-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: base) }
        let store = base.appendingPathComponent("store", isDirectory: true)
        let settings = UserInstructions(directory: store)
        check(settings.folder == store.appendingPathComponent("Instructions", isDirectory: true), "Default folder must be local to the isolated store")
        check(settings.snapshot().promptSection.isEmpty && settings.snapshot().warnings.isEmpty, "Missing default files must be optional")
        check(!FileManager.default.fileExists(atPath: settings.folder.path), "Loading preferences must not create files or activate examples")
        try settings.save("# Preferences\nUse navy headings.", for: .memory, expectedContent: nil)
        let first = settings.snapshot()
        check(first.files.first(where: { $0.kind == .memory })?.active == true, "Saved canonical memory should be enabled by default")
        check(first.promptSection.contains("Use navy headings.") && first.promptSection.contains("current request") && first.promptSection.contains("do not execute"), "Prompt should include preferences and explicit authority boundary")
        let memory = settings.folder.appendingPathComponent("memory.md")
        try Data("Use teal headings instead.".utf8).write(to: memory)
        check(settings.snapshot().promptSection.contains("Use teal headings instead."), "Every snapshot must observe external file edits without restart")
        check(!settings.snapshot().promptSection.contains("Use navy headings."), "Stale file contents must not survive a new snapshot")
        try settings.setEnabled(false, for: .memory)
        check(settings.snapshot().promptSection.isEmpty, "Disabled files must never enter model prompts")
        check(settings.snapshot().files.first(where: { $0.kind == .memory })?.content == "Use teal headings instead.", "Disabled valid content remains available to the local editor")
        let reopened = UserInstructions(directory: store)
        check(!reopened.isEnabled(.memory), "Per-file enable state must persist")
        try reopened.setEnabled(true, for: .memory)
        try reopened.save("Include labelled charts.", for: .skill, expectedContent: nil)
        check(reopened.snapshot().promptSection.contains("Include labelled charts."), "Skill preferences must join memory preferences")
        let beforeConflict = try Data(contentsOf: memory)
        rejects("Saving a stale editor must not replace external edits") { try reopened.save("Overwrite", for: .memory, expectedContent: "old content") }
        check(try Data(contentsOf: memory) == beforeConflict, "Rejected saves must preserve original bytes")
        try reopened.save("", for: .memory, expectedContent: "Use teal headings instead.")
        check(!reopened.snapshot().files.first(where: { $0.kind == .memory })!.active, "Empty files should not be injected")
        try Data(repeating: 97, count: UserInstructions.maximumFileBytes + 1).write(to: memory)
        check(reopened.snapshot().warnings.contains { $0.contains("16 KiB") }, "Oversized file must report a visible warning")
        check(!reopened.snapshot().promptSection.contains(String(repeating: "a", count: 50)), "Oversized file must be rejected, not silently truncated")
        rejects("Oversized save must fail") { try reopened.save(String(repeating: "x", count: UserInstructions.maximumFileBytes + 1), for: .skill, expectedContent: "Include labelled charts.") }
        try Data([0xff, 0xfe]).write(to: memory)
        check(reopened.snapshot().warnings.contains { $0.contains("UTF-8") }, "Invalid UTF-8 must be rejected")
        try reopened.setEnabled(false, for: .memory)
        check(reopened.snapshot().warnings.isEmpty, "Disabled invalid file should not warn as an active request input")
        let custom = base.appendingPathComponent("custom", isDirectory: true)
        try FileManager.default.createDirectory(at: custom.appendingPathComponent("nested"), withIntermediateDirectories: true)
        try Data("Explicit custom memory".utf8).write(to: custom.appendingPathComponent("MEMORY.MD"))
        try Data("Research attachment is not instruction".utf8).write(to: custom.appendingPathComponent("research.md"))
        try Data("Nested injection".utf8).write(to: custom.appendingPathComponent("nested/skill.md"))
        try reopened.selectFolder(custom)
        try reopened.setEnabled(true, for: .memory)
        check(reopened.snapshot().promptSection.contains("Explicit custom memory"), "Explicit selection should support case-insensitive canonical names")
        check(!reopened.snapshot().promptSection.contains("Research attachment") && !reopened.snapshot().promptSection.contains("Nested injection"), "No recursive or arbitrary Markdown discovery is allowed")
        check(UserInstructions(directory: store).folder == custom.standardizedFileURL, "Explicit folder choice must survive restart")
        check(try UserInstructions.matchingFilename(in: ["memory.md.old", "notes.md", "MEMORY.md"], kind: .memory) == "MEMORY.md", "Matching must be exact apart from case")
        rejects("Multiple case variants must be ambiguous on case-sensitive volumes") { _ = try UserInstructions.matchingFilename(in: ["memory.md", "Memory.md"], kind: .memory) }
        rejects("A remote URL cannot be selected as a local instruction folder") { try reopened.selectFolder(URL(string: "https://example.com/preferences")!) }
        rejects("A plain file cannot become the instruction folder") { try reopened.selectFolder(custom.appendingPathComponent("MEMORY.MD")) }
        rejects("A missing folder cannot be selected") { try reopened.selectFolder(base.appendingPathComponent("missing")) }
        let imported = base.appendingPathComponent("import.md")
        try Data("A draft to review".utf8).write(to: imported)
        check(try UserInstructions.importText(from: imported) == "A draft to review", "Import should return a reviewable draft")
        check(reopened.snapshot().promptSection.contains("Explicit custom memory"), "Import must not modify the selected files before explicit Save")
        let badImport = base.appendingPathComponent("import.txt")
        try Data("no".utf8).write(to: badImport)
        rejects("Import accepts local Markdown only") { _ = try UserInstructions.importText(from: badImport) }
        let link = custom.appendingPathComponent("skill.md")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: imported)
        check(reopened.snapshot().warnings.contains { $0.contains("symbolic link") }, "Symbolic instruction files must not read unselected targets")
        check(!reopened.snapshot().promptSection.contains("A draft to review"), "Symbolic link content must be excluded")
        try FileManager.default.removeItem(at: imported)
        check(reopened.snapshot().warnings.contains { $0.contains("symbolic link") }, "Broken symbolic links must still be visibly rejected")
        try FileManager.default.removeItem(at: custom)
        check(reopened.snapshot().warnings.contains { $0.contains("missing or unavailable") }, "Unavailable selected folders must warn")
        try reopened.useDefaultFolder()
        check(reopened.usesDefaultFolder, "Default selection should be recoverable without accessing the missing custom folder")
        let draftSettings = UserInstructions(directory: base.appendingPathComponent("editor-store", isDirectory: true))
        let editor = UserInstructionEditor(instructions: draftSettings)
        editor.refreshIfClean()
        editor.drafts[.memory] = "Unsaved private preference"
        editor.selected = .skill
        editor.drafts[.skill] = "Imported workflow draft"
        editor.refreshIfClean()
        check(editor.drafts[.memory] == "Unsaved private preference" && editor.drafts[.skill] == "Imported workflow draft", "Returning to Mind must preserve both unsaved file drafts")
        check(editor.selected == .skill && editor.dirty, "Tab changes must preserve the selected file and dirty state")
        check(!FileManager.default.fileExists(atPath: draftSettings.folder.path), "Keeping editor drafts must not write them to disk")
        check(draftSettings.snapshot().promptSection.isEmpty, "Unsaved drafts must never enter model requests")
        editor.selected = .memory
        try editor.save()
        check(!editor.currentDirty && editor.dirty && editor.drafts[.skill] == "Imported workflow draft", "Saving one file must preserve the other unsaved draft")
        editor.refreshIfClean()
        check(editor.drafts[.skill] == "Imported workflow draft", "Reopening after one save must not discard the other file's draft")
        editor.selected = .skill
        try editor.save()
        check(!editor.dirty, "Both successful saves should clear the dirty state")
        let draftMemory = draftSettings.folder.appendingPathComponent("memory.md")
        try Data("Edited in another editor".utf8).write(to: draftMemory)
        editor.refreshIfClean()
        check(editor.drafts[.memory] == "Edited in another editor", "A clean editor should reload external changes when reopened")
        editor.selected = .memory
        editor.drafts[.memory] = "Unsaved replacement"
        try Data("Newer external edit".utf8).write(to: draftMemory)
        editor.refreshIfClean()
        check(editor.drafts[.memory] == "Unsaved replacement" && editor.baselines[.memory] == "Edited in another editor", "Reopening a dirty editor must retain its original conflict-check baseline")
        rejects("Draft persistence must not weaken external-change conflict checking") { try editor.save() }
        check(try String(contentsOf: draftMemory, encoding: .utf8) == "Newer external edit", "A conflicted save must leave the external file unchanged")
        editor.reload()
        check(!editor.dirty && editor.drafts[.memory] == "Newer external edit", "Explicit reload can discard drafts and adopt the latest external content")
        let identityStore = UserInstructions(directory: base.appendingPathComponent("identity-store", isDirectory: true))
        try identityStore.installDefaultPersonalityIfMissing()
        let identityFile = identityStore.folder.appendingPathComponent("personality.md")
        check(FileManager.default.fileExists(atPath: identityFile.path), "Explicit first launch seeds a real personality.md")
        let initialIdentity = try String(contentsOf: identityFile, encoding: .utf8)
        let customIdentity = "# Squipi\n## Identity and voice\nPERSONALITY_FIXTURE: warm, funny and direct.\n## Working style\nFinish the actual research.\n## Tool and skill directory\nDIRECTORY_FIXTURE survey_summary and research skills"
        try identityStore.save(customIdentity, for: .personality, expectedContent: initialIdentity)
        try identityStore.installDefaultPersonalityIfMissing()
        let identitySnapshot = identityStore.snapshot()
        check(identitySnapshot.personalityText == customIdentity, "Startup must not overwrite a personalised identity")
        check(!identitySnapshot.promptSection.contains("PERSONALITY_FIXTURE"), "Identity is injected once, separately from memory/skill preferences")
        check(SquipiPersonality.prompt(customIdentity).contains("PERSONALITY_FIXTURE") && !SquipiPersonality.prompt(customIdentity).contains("DIRECTORY_FIXTURE"), "Routine work keeps the voice but avoids a redundant tool directory")
        check(SquipiPersonality.prompt(customIdentity, detail: .directory).contains("DIRECTORY_FIXTURE"), "Capability questions can read the complete directory")
        check(!SquipiPersonality.prompt(customIdentity, detail: .greeting).contains("DIRECTORY_FIXTURE"), "Greetings must not carry the research catalog")
        check(SquipiPersonality.runtime(label: "Nebius", model: "fixture", isLocal: false).contains("sent online"), "Cloud reasoning must not masquerade as fully local")
        check(SquipiPersonality.runtime(label: "MLX", model: "fixture", isLocal: true).contains("runs locally"), "Local inference is described accurately")
        try Data("NEXT_REQUEST_IDENTITY".utf8).write(to: identityFile)
        check(identitySnapshot.personalityText == customIdentity && identityStore.snapshot().personalityText == "NEXT_REQUEST_IDENTITY", "An identity edit applies next request, not halfway through an approved turn")
        try identityStore.setEnabled(false, for: .personality)
        check(!identityStore.snapshot().personalityText.contains("NEXT_REQUEST_IDENTITY"), "Disabled custom personality must fall back to the bundled identity")
        print("User instruction checks passed: \(checks)")
    }
}
