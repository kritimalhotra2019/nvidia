import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct SavedTemplatesView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @ObservedObject private var library = SavedTemplates.shared
    @State private var importAsDefault = true
    @State private var removal: SavedTemplates.Entry?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(text: "Saved report and presentation templates")
            Text("Import a Word report or PowerPoint deck whose formatting you want Squipi to reuse. Templates are saved as local copies; importing one does not start an analysis.")
                .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
            HStack {
                Button("Import template…") { chooseTemplate() }
                Button("Refresh") { library.refresh() }
                Spacer()
                if library.busy { ProgressView().controlSize(.small) }
            }.controlSize(.small).disabled(library.busy)
            Toggle("Use new imports as the default for their format", isOn: $importAsDefault)
                .font(.system(size: 11)).toggleStyle(.checkbox).disabled(library.busy)
            if library.templates.isEmpty {
                Text("No saved templates yet. Choose an editable .docx or .pptx file above.")
                    .font(.system(size: 11)).foregroundStyle(Color.petMuted)
                    .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    .background(RoundedRectangle(cornerRadius: 8).fill(Color.petBG))
            } else {
                ForEach(library.templates) { entry in
                    SavedTemplateRow(entry: entry, library: library, remove: { removal = entry })
                }
            }
            if !library.status.isEmpty { Text(library.status).font(.system(size: 11)).foregroundStyle(Color.petMuted) }
            if !library.error.isEmpty { Text(library.error).font(.system(size: 11)).foregroundStyle(.orange).textSelection(.enabled) }
            ForEach(Array(library.warnings.enumerated()), id: \.offset) { _, warning in
                Text(warning).font(.system(size: 11)).foregroundStyle(.orange).textSelection(.enabled)
            }
            Text("Ask: “Analyse this dataset and create a Word report using my Research report template.” Defaults apply to future exports; say “no template” to skip one. Your current request takes priority.")
                .font(.system(size: 10)).foregroundStyle(Color.petMuted)
            Text("Word keeps reusable styles, headings, headers, footers and page settings. PowerPoint keeps masters, layouts, themes and slide size. Existing report text and sample slides are replaced; complex sample layouts may need adjustment. Put only reusable branding in headers, footers and slide masters because those are retained.")
                .font(.system(size: 10)).foregroundStyle(Color.petMuted)
            Text("Template files stay on this Mac. Models receive template names and formatting metadata; the local export worker applies the files. With Nebius, that metadata is included in the existing cloud approval preview.")
                .font(.system(size: 10)).foregroundStyle(Color.petMuted)
        }.card().onAppear { library.refresh() }
            .confirmationDialog("Remove this saved template copy? Your original file will stay unchanged.", isPresented: Binding(get: { removal != nil }, set: { if !$0 { removal = nil } }), presenting: removal) { entry in
                Button("Remove “\(entry.name)”", role: .destructive) {
                    removal = nil
                    Task { await library.remove(entry) }
                }
            }
    }

    private func chooseTemplate() {
        let panel = NSOpenPanel()
        panel.title = "Save a Squipi output template"
        panel.message = "Choose an editable Word (.docx) or PowerPoint (.pptx) file. Squipi saves its own copy and leaves the original untouched."
        panel.canChooseDirectories = false; panel.canChooseFiles = true; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [UTType(filenameExtension: "docx"), UTType(filenameExtension: "pptx")].compactMap { $0 }
        let useAsDefault = importAsDefault
        let selected: (URL) -> Void = { url in Task { await library.importTemplate(from: url, makeDefault: useAsDefault) } }
        if let window = NSApp.keyWindow {
            panel.beginSheetModal(for: window) { response in
                if response == .OK, let url = panel.url { selected(url) }
            }
        } else if panel.runModal() == .OK, let url = panel.url { selected(url) }
    }
}

private struct SavedTemplateRow: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    let entry: SavedTemplates.Entry
    @ObservedObject var library: SavedTemplates
    let remove: () -> Void
    @State private var editingName = false
    @State private var proposedName = ""
    private var isDefault: Bool { library.defaults[entry.format] == entry.id }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .top) {
                Image(systemName: entry.format == "docx" ? "doc.text" : "rectangle.stack")
                    .foregroundStyle(Color.petAccent).padding(.top, 2)
                VStack(alignment: .leading, spacing: 3) {
                    Text(entry.name).font(.system(size: 12, weight: .semibold, design: .rounded))
                        .foregroundStyle(Color.petInk).textSelection(.enabled)
                    Text("\(entry.formatLabel)\(isDefault ? " · Default" : "")")
                        .font(.system(size: 10, weight: isDefault ? .semibold : .regular)).foregroundStyle(Color.petMuted)
                }
                Spacer(minLength: 4)
                Button("Rename") { proposedName = entry.name; editingName.toggle() }
                    .controlSize(.mini)
            }
            if editingName {
                HStack {
                    TextField("Template name", text: $proposedName).textFieldStyle(.roundedBorder)
                    Button("Save name") {
                        Task { await library.rename(entry, to: proposedName); if library.error.isEmpty { editingName = false } }
                    }.disabled(proposedName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || proposedName == entry.name || proposedName.count > 120)
                    Button("Cancel") { editingName = false }
                }.controlSize(.small)
            }
            Text(entry.summary).font(.system(size: 10)).foregroundStyle(Color.petMuted)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button(isDefault ? "Clear default" : "Use by default") { Task { await library.setDefault(entry, enabled: !isDefault) } }
                Button("Open copy") {
                    do {
                        let url = try library.fileURL(for: entry)
                        if !NSWorkspace.shared.open(url) { library.error = "macOS could not open this template. Install an app that supports \(entry.format.uppercased()) files." }
                    } catch { library.error = error.localizedDescription }
                }
                Spacer(minLength: 0)
                Button("Remove", role: .destructive, action: remove)
            }.controlSize(.small)
        }.padding(10).background(RoundedRectangle(cornerRadius: 8).fill(Color.petBG))
            .disabled(library.busy)
    }
}
