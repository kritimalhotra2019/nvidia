import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct UserInstructionsView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @ObservedObject private var instructions = UserInstructions.shared
    @ObservedObject private var editor = UserInstructionEditor.shared
    @State private var confirmReload = false

    private var selected: UserInstructions.Kind { editor.selected }
    private var current: UserInstructions.FileState? { editor.current }
    private var dirty: Bool { editor.dirty }
    private var currentDirty: Bool { editor.currentDirty }
    private var bytes: Int { editor.bytes }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            instructionCard
            SavedTemplatesView()
        }
    }

    private var instructionCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Personality, memory and skills")
            Text("Edit personality.md for Squipi’s identity and humour. Add preferences Squipi should use across chats: your report colours, writing style, or a repeatable research approach. Saved changes apply to the next request; your current request takes priority.")
                .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
            Text(instructions.folder.path).font(.system(size: 10, design: .monospaced))
                .foregroundStyle(Color.petMuted).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Open folder") { openFolder() }
                Button("Choose folder…") { chooseFolder() }.disabled(dirty)
                if !instructions.usesDefaultFolder {
                    Button("Use default") { perform { try instructions.useDefaultFolder(); reload() } }.disabled(dirty)
                }
                Spacer()
            }.controlSize(.small)
            if dirty {
                Text("Drafts stay while switching tabs. Save before quitting, or reload to discard them. Save or reload before choosing a different folder.")
                    .font(.system(size: 10)).foregroundStyle(Color.petMuted)
            }
            Picker("Instruction file", selection: $editor.selected) {
                ForEach(UserInstructions.Kind.allCases) { kind in Text(kind.filename).tag(kind) }
            }.pickerStyle(.segmented)
            HStack {
                Text(selected.detail).font(.system(size: 11, weight: .semibold))
                Spacer()
                Toggle(selected == .personality ? "Use custom personality" : "Use in requests", isOn: Binding(
                    get: { instructions.isEnabled(selected) },
                    set: { enabled in perform { try instructions.setEnabled(enabled, for: selected) }; refreshStates() }
                )).toggleStyle(.switch).controlSize(.small)
            }
            if let warning = current?.warning {
                Text(warning).font(.system(size: 11)).foregroundStyle(.orange).textSelection(.enabled)
            } else if current?.exists != true {
                Text("No file yet. Write your preferences below, or import a Markdown file, then Save.")
                    .font(.system(size: 11)).foregroundStyle(Color.petMuted)
            }
            TextEditor(text: Binding(get: { editor.drafts[selected] ?? "" }, set: { editor.drafts[selected] = $0 }))
                .font(.system(size: 11, design: .monospaced)).scrollContentBackground(.hidden)
                .padding(6).frame(minHeight: 150, maxHeight: 220)
                .background(RoundedRectangle(cornerRadius: 8).fill(Color.petBG))
                .accessibilityLabel("\(selected.filename) editor")
            HStack {
                Text("\(bytes) / \(UserInstructions.maximumFileBytes) bytes")
                    .font(.system(size: 10)).foregroundStyle(bytes > UserInstructions.maximumFileBytes ? Color.orange : Color.petMuted)
                Spacer()
                Button("Example") { editor.drafts[selected] = selected.example; editor.status = "Example added to the editor. Review it and Save to use it." }
                    .disabled(!(editor.drafts[selected] ?? "").isEmpty)
                Button("Import…") { importDraft() }
                Button("Save") { save() }
                    .disabled(!currentDirty || bytes > UserInstructions.maximumFileBytes || current?.warning != nil)
            }.controlSize(.small)
            HStack {
                Button("Reload files") { if dirty { confirmReload = true } else { reload() } }
                Spacer()
                if let file = current, file.active { Text("Enabled for the next request").foregroundStyle(Color.petMuted) }
                else { Text("Not currently included").foregroundStyle(Color.petMuted) }
            }.font(.system(size: 10)).controlSize(.small)
            if !editor.status.isEmpty { Text(editor.status).font(.system(size: 11)).foregroundStyle(Color.petMuted) }
            if !editor.error.isEmpty { Text(editor.error).font(.system(size: 11)).foregroundStyle(.orange).textSelection(.enabled) }
            Text("Word and PowerPoint exports can use colours, fonts and chart palettes. Describe structure and tone here; import existing report and deck formats in Saved templates below. Only personality.md, memory.md and skill.md directly inside this folder are used, up to 16 KiB each. Scripts and linked files are not run or loaded.")
                .font(.system(size: 10)).foregroundStyle(Color.petMuted)
            Text("Files stay on this Mac. Enabled contents are included in model requests: Ollama and MLX run locally; Nebius includes the relevant profile and preferences in its cloud approval preview. Do not store API keys or other secrets here.")
                .font(.system(size: 10)).foregroundStyle(Color.petMuted)
        }.card().onAppear { editor.refreshIfClean() }
            .confirmationDialog("Discard unsaved edits and reload the instruction files?", isPresented: $confirmReload) {
                Button("Discard edits and reload", role: .destructive) { reload() }
            }
    }

    private func perform(_ operation: () throws -> Void) {
        editor.error = ""
        do { try operation() } catch { editor.error = error.localizedDescription }
    }
    private func refreshStates() { editor.refreshStates() }
    private func reload() { editor.reload() }
    private func save() { perform { try editor.save() } }
    private func openFolder() {
        perform { try instructions.ensureFolder(); NSWorkspace.shared.open(instructions.folder) }
    }
    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.title = "Choose your Squipi instruction folder"
        panel.message = "Squipi reads only personality.md, memory.md and skill.md directly inside this folder."
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.directoryURL = instructions.folder
        present(panel) { url in
            perform { try instructions.selectFolder(url); reload() }
        }
    }
    private func importDraft() {
        let panel = NSOpenPanel()
        panel.title = "Import text into \(selected.filename)"
        panel.message = "Import fills the editor only. Review and Save to replace \(selected.filename) in the selected folder."
        panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
        let destination = selected
        present(panel) { url in
            perform {
                editor.drafts[destination] = try UserInstructions.importText(from: url)
                editor.status = "Imported a draft from \(url.lastPathComponent). Review and Save to apply it."
            }
        }
    }
    private func present(_ panel: NSOpenPanel, selected: @escaping (URL) -> Void) {
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window) { response in
                if response == .OK, let url = panel.url { selected(url) }
            }
        } else if panel.runModal() == .OK, let url = panel.url {
            selected(url)
        }
    }
}
