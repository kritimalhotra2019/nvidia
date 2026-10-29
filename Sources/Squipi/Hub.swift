import AppKit
import AVFoundation
import ServiceManagement
import Speech
import SwiftUI
import UniformTypeIdentifiers

// MARK: - palette
extension Color {
    static var petBG: Color { Color(squipiHex: SquipiColour.current.background) }
    static var petAccent: Color { Color(squipiHex: SquipiColour.current.accent) }
    static var petAccentSoft: Color { Color(squipiHex: SquipiColour.current.soft) }
    static let petInk = Color(red: 23 / 255, green: 43 / 255, blue: 58 / 255)
    static let petMuted = Color(red: 82 / 255, green: 102 / 255, blue: 116 / 255)
    static func speaker(_ id: String) -> Color {
        switch id {
        case "me": return .petAccent
        case "s1": return Color(red: 43 / 255, green: 115 / 255, blue: 166 / 255)
        case "s2": return Color(red: 128 / 255, green: 89 / 255, blue: 65 / 255)
        case "s3": return Color(red: 142 / 255, green: 104 / 255, blue: 26 / 255)
        default: return .petMuted
        }
    }
}

struct Card: ViewModifier {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    func body(content: Content) -> some View {
        content.padding(16)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(.white))
            .shadow(color: Color.petInk.opacity(0.07), radius: 12, y: 5)
    }
}
extension View { func card() -> some View { modifier(Card()) } }

struct Pill: ButtonStyle {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var filled = true
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold, design: .rounded))
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(Capsule().fill(filled ? Color.petAccent : Color.petAccentSoft.opacity(0.6)))
            .foregroundStyle(filled ? .white : Color.petInk)
            .scaleEffect(configuration.isPressed ? 0.96 : 1)
            .animation(.spring(duration: 0.25), value: configuration.isPressed)
    }
}

struct SectionTitle: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var text: String
    var body: some View {
        Text(text.uppercased()).font(.system(size: 10.5, weight: .bold, design: .rounded)).tracking(1.2).foregroundStyle(Color.petMuted)
    }
}

// MARK: - window
final class HubPanel: NSPanel {
    var onCancel: (() -> Void)?
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { onCancel?() }
}

@MainActor
final class HubController {
    private weak var app: AppDelegate?
    private let panel: HubPanel
    static let size = NSSize(width: 520, height: 660)

    init(app: AppDelegate) {
        self.app = app
        panel = HubPanel(contentRect: NSRect(origin: .zero, size: Self.size),
                         styleMask: [.borderless, .nonactivatingPanel],
                         backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.isMovableByWindowBackground = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.animationBehavior = .utilityWindow
        let root = HubView(onEngineChange: { [weak app] id in app?.selectEngine(id) }, onClose: { [weak self] in self?.hide() })
            .environmentObject(Store.shared)
            .environmentObject(app.meeting)
        let host = NSHostingView(rootView: root)
        host.wantsLayer = true
        host.layer?.cornerRadius = 24
        host.layer?.masksToBounds = true
        panel.contentView = host
        panel.onCancel = { [weak self] in self?.hide() }
        // click anywhere outside: close, like a popover. Ignore our own child windows (rename popover).
        NotificationCenter.default.addObserver(forName: NSWindow.didResignKeyNotification, object: panel, queue: .main) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, self.panel.isVisible else { return }
                if self.panel.attachedSheet != nil { return }
                if let k = NSApp.keyWindow, k === self.panel || k.parent === self.panel { return }
                if self.panel.childWindows?.contains(where: { $0.isKeyWindow }) == true { return }
                self.hide()
            }
        }
    }

    func toggle() {
        if panel.isVisible && panel.isKeyWindow { hide(); return }
        show()
    }

    var contentView: NSView? { panel.contentView }
    /// Debug: switch tabs programmatically (the view observes `HubView.requestedTab`).
    func selectTab(_ i: Int) { HubView.requestedTab = i; NotificationCenter.default.post(name: .squipiSelectTab, object: nil) }

    var isVisible: Bool { panel.isVisible }

    func hide() { panel.orderOut(nil) }

    func show() {
        guard let app else { return }
        let pet = app.panel.frame
        var origin = NSPoint(x: pet.minX - Self.size.width - 8, y: pet.minY)
        if let screen = app.panel.screen ?? NSScreen.main {
            let f = screen.visibleFrame
            if origin.x < f.minX { origin.x = pet.maxX + 8 }
            origin.y = max(f.minY, min(origin.y, f.maxY - Self.size.height))
            origin.x = max(f.minX, min(origin.x, f.maxX - Self.size.width))
        }
        panel.setFrameOrigin(origin)
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(nil)
        NSLog("HUBFRAME \(NSStringFromRect(panel.frame))")
    }
}

// MARK: - root
extension Notification.Name { static let squipiSelectTab = Notification.Name("squipi.selectTab") }

struct HubView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    nonisolated(unsafe) static var currentTab = 0
    nonisolated(unsafe) static var requestedTab = 0
    private var app: AppDelegate? { NSApp?.delegate as? AppDelegate }
    var onEngineChange: (String) -> Void
    var onClose: () -> Void
    @State private var tab = ["chat": 0, "work": 1, "notes": 2, "detail": 2, "words": 3, "mind": 4, "me": 5][ProcessInfo.processInfo.environment["SQUIPI_DEMO_TAB"] ?? "chat"] ?? 0
    @Namespace private var ns

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                tabs
                Spacer(minLength: 0)
                Button(action: onClose) {
                    Image(systemName: "xmark").font(.system(size: 11, weight: .bold))
                        .foregroundStyle(Color.petMuted)
                        .frame(width: 30, height: 30)
                        .background(Circle().fill(Color.petAccentSoft.opacity(0.45)))
                }
                .buttonStyle(.plain).help("Close (Esc)")
            }
            .padding(.horizontal, 16).padding(.top, 16).padding(.bottom, 6)
            Group {
                switch tab {
                case 0: ChatView()
                case 1: WorkView()
                case 2: NotesView()
                case 3: WordsView()
                case 4: MindView()
                default: MeView(onEngineChange: onEngineChange)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(width: HubController.size.width, height: HubController.size.height)
        .background(Color.petBG)
        .tint(Color.petAccent)
        .preferredColorScheme(.light)
        .onChange(of: tab) { _, v in HubView.currentTab = v }
        .onReceive(NotificationCenter.default.publisher(for: .squipiSelectTab)) { _ in tab = HubView.requestedTab }
        .onDrop(of: [.fileURL], isTargeted: nil) { providers in
            for p in providers {
                p.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
                    guard let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil),
                          PetWebView.dataExtensions.contains(url.pathExtension.lowercased()) else { return }
                    Task { @MainActor in Conversation.shared.attach([url]) }
                }
            }
            return true
        }
        .foregroundStyle(Color.petInk)
        .font(.system(size: 13, design: .rounded))
    }

    var tabs: some View {
        HStack(spacing: 2) {
            ForEach(Array(["Chat", "Work", "Notes", "Words", "Mind", "Me"].enumerated()), id: \.offset) { i, name in
                Button {
                    withAnimation(.spring(duration: 0.35, bounce: 0.25)) { tab = i }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: ["bubble.left", "chart.bar", "note.text", "textformat.abc", "brain", "sparkles"][i]).font(.system(size: 12, weight: .bold))
                        if tab == i { Text(SquipiLanguage.text(name)) }
                    }
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .padding(.horizontal, tab == i ? 11 : 9).padding(.vertical, 8)
                    .foregroundStyle(tab == i ? .white : Color.petInk)
                    .background {
                        if tab == i { Capsule().fill(Color.petAccent).matchedGeometryEffect(id: "pill", in: ns) }
                    }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(4)
        .background(Capsule().fill(Color.petAccentSoft.opacity(0.45)))
    }
}

// MARK: - Notes
struct NotesView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @EnvironmentObject var store: Store
    @EnvironmentObject var meeting: MeetingRecorder
    @State private var selectedID: UUID? = ProcessInfo.processInfo.environment["SQUIPI_DEMO_TAB"] == "detail" ? Store.shared.notes.first?.id : nil

    var body: some View {
        if let id = selectedID, let note = store.notes.first(where: { $0.id == id }) {
            NoteDetail(note: note) { selectedID = nil }
        } else {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    recordCard
                    if Summarizer.active == nil {
                        HStack(spacing: 8) {
                            Image(systemName: "key").foregroundStyle(Color.petAccent)
                            Text("For summaries, choose Apple Intelligence or NVIDIA Nemotron on Nebius in Me. Transcripts work without either.")
                                .font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                        }.padding(.horizontal, 4)
                    }
                    if store.notes.isEmpty {
                        Text("Your meeting notes will live here.\nStart one before a call and Squipi listens to both sides, on this Mac.")
                            .foregroundStyle(Color.petMuted).multilineTextAlignment(.center)
                            .frame(maxWidth: .infinity).padding(.top, 30)
                    }
                    ForEach(store.notes) { note in
                        NoteRow(note: note).contentShape(Rectangle()).onTapGesture { selectedID = note.id }
                    }
                }
                .padding(.horizontal, 16).padding(.bottom, 16).padding(.top, 8)
            }
        }
    }

    var recordCard: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle().fill(Color.petAccentSoft).frame(width: 46, height: 46)
                if meeting.isRecording {
                    Circle().fill(Color.petAccent).frame(width: 14, height: 14)
                        .modifier(Pulse())
                } else if meeting.isProcessing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "waveform").font(.system(size: 18, weight: .bold)).foregroundStyle(Color.petAccent)
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                if meeting.isRecording {
                    Text("Listening · \(mmss(meeting.elapsed))").font(.system(size: 15, weight: .bold, design: .rounded))
                    Text("You and your call. Nothing leaves this Mac.").foregroundStyle(Color.petMuted).font(.system(size: 11.5, design: .rounded))
                } else if meeting.isProcessing {
                    Text("Writing your notes…").font(.system(size: 15, weight: .bold, design: .rounded))
                    Text("Transcribing microphone and call audio, then writing notes.").foregroundStyle(Color.petMuted).font(.system(size: 11.5, design: .rounded))
                } else {
                    Text("Take notes").font(.system(size: 15, weight: .bold, design: .rounded))
                    Text("Start before a call. I'll hear both sides.").foregroundStyle(Color.petMuted).font(.system(size: 11.5, design: .rounded))
                }
            }
            Spacer()
            if meeting.isRecording {
                Button("Stop") { meeting.stop() }.buttonStyle(Pill(filled: true))
            } else if meeting.isProcessing {
                Button("Stop processing") { meeting.cancelProcessing() }.buttonStyle(Pill(filled: false))
            } else {
                Button("Start") { meeting.start() }.buttonStyle(Pill(filled: true))
            }
        }
        .card()
    }
}

struct Pulse: ViewModifier {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @State private var on = false
    func body(content: Content) -> some View {
        content.scaleEffect(on ? 1.25 : 0.85).opacity(on ? 1 : 0.6)
            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: on)
            .onAppear { on = true }
    }
}

func mmss(_ t: Double) -> String { String(format: "%d:%02d", Int(t) / 60, Int(t) % 60) }

struct NoteRow: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var note: Note
    @State private var hover = false
    var people: Int { Set(note.segments.map { $0.speaker }).count }
    var body: some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 6).fill(note.status == "ready" ? Color.petAccent : Color.petAccentSoft).frame(width: 4, height: 34)
            VStack(alignment: .leading, spacing: 3) {
                Text(note.title).font(.system(size: 14, weight: .semibold, design: .rounded)).lineLimit(1)
                Text(subtitle).font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
            }
            Spacer()
            if note.status == "processing" { ProgressView().controlSize(.small) }
            else if note.status == "failed" { Image(systemName: "exclamationmark.circle").foregroundStyle(.orange) }
            else if note.status == "recording" { Circle().fill(Color.petAccent).frame(width: 8, height: 8).modifier(Pulse()) }
            else if note.summary.isEmpty { Image(systemName: "pencil.and.outline").foregroundStyle(Color.petMuted).help("No notes written yet") }
            else { Image(systemName: "chevron.right").font(.system(size: 11, weight: .bold)).foregroundStyle(Color.petAccentSoft) }
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(.white.opacity(hover ? 1 : 0.75)))
        .shadow(color: Color.petAccent.opacity(hover ? 0.12 : 0), radius: 10, y: 4)
        .scaleEffect(hover ? 1.01 : 1)
        .animation(.spring(duration: 0.25), value: hover)
        .onHover { hover = $0 }
    }
    var subtitle: String {
        var parts = [note.date.formatted(date: .abbreviated, time: .shortened)]
        if note.duration > 0 { parts.append(mmss(note.duration)) }
        if people > 1 { parts.append("\(people) people") }
        if note.status == "processing" { parts.append("writing…") }
        return parts.joined(separator: " · ")
    }
}

struct NoteDetail: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @EnvironmentObject var store: Store
    var note: Note
    var back: () -> Void
    @State private var title = ""
    @State private var renaming: String?
    @State private var newName = ""
    @State private var confirmDelete = false
    @State private var writing = false
    @State private var copied: String?
    @State private var saveError: String?
    @FocusState private var titleFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button { back() } label: { Label("Notes", systemImage: "chevron.left") }.buttonStyle(.plain).foregroundStyle(Color.petMuted)
                Spacer()
                if writing { ProgressView().controlSize(.small); Text("writing…").foregroundStyle(Color.petMuted).font(.system(size: 11.5, design: .rounded)) }
            }
            .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 6)

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    TextField("Title", text: $title)
                        .textFieldStyle(.plain).font(.system(size: 20, weight: .bold, design: .rounded))
                        .focused($titleFocused)
                        .onChange(of: title) { _, v in
                            guard v != note.title else { return }
                            var n = note; n.title = v; save(n)
                        }
                    Text(meta).font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    if let transcriptionProvider = note.transcriptionProvider {
                        Text("Transcription: " + transcriptionProvider).font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    }

                    // copy row
                    HStack(spacing: 8) {
                        copyPill("Copy notes", key: "notes", text: note.summary, disabled: note.summary.isEmpty)
                        copyPill("Copy transcript", key: "transcript", text: NoteProcessor.transcriptText(note), disabled: note.segments.isEmpty)
                        copyPill("Copy all", key: "all", text: everything, disabled: note.segments.isEmpty)
                    }

                    if let e = saveError ?? note.error {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.bubble").foregroundStyle(.orange)
                            Text(e).font(.system(size: 12, design: .rounded))
                        }.card()
                    }

                    // notes
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            SectionTitle(text: "Notes")
                            Spacer()
                            if !note.segments.isEmpty {
                                Button(note.summary.isEmpty ? "Write notes" : "Rewrite") { rewrite() }
                                    .buttonStyle(Pill(filled: note.summary.isEmpty)).disabled(writing)
                            }
                        }
                        if note.summary.isEmpty {
                            Text(Summarizer.active == nil ? "Choose Apple Intelligence or configure Nebius in Me, then press Write notes." : "Press Write notes and I'll summarize the transcript.")
                                .font(.system(size: 12, design: .rounded)).foregroundStyle(Color.petMuted)
                        } else {
                            NotesText(text: note.summary)
                        }
                    }.card()

                    // transcript
                    if !note.segments.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            SectionTitle(text: "Who")
                            Flow(spacing: 6) {
                                ForEach(speakers, id: \.self) { s in
                                    Button { renaming = s; newName = note.name(for: s) } label: {
                                        HStack(spacing: 6) {
                                            Circle().fill(Color.speaker(s)).frame(width: 9, height: 9)
                                            Text(note.name(for: s)).font(.system(size: 12, weight: .semibold, design: .rounded))
                                            Image(systemName: "pencil").font(.system(size: 8, weight: .bold)).foregroundStyle(Color.petMuted)
                                        }
                                        .padding(.horizontal, 10).padding(.vertical, 6)
                                        .background(Capsule().fill(Color.speaker(s).opacity(0.15)))
                                    }.buttonStyle(.plain).help("Rename")
                                    .popover(isPresented: Binding(get: { renaming == s }, set: { if !$0 { renaming = nil } })) {
                                        HStack {
                                            TextField("Name", text: $newName).textFieldStyle(.roundedBorder).frame(width: 150).onSubmit { rename(s) }
                                            Button("OK") { rename(s) }.buttonStyle(Pill())
                                        }.padding(12)
                                    }
                                }
                            }
                            SectionTitle(text: "Transcript").padding(.top, 6)
                            ForEach(note.segments) { seg in
                                HStack(alignment: .top, spacing: 10) {
                                    VStack(alignment: .trailing, spacing: 1) {
                                        Text(note.name(for: seg.speaker)).font(.system(size: 11, weight: .bold, design: .rounded)).foregroundStyle(Color.speaker(seg.speaker))
                                        Text(mmss(seg.start)).font(.system(size: 9.5, design: .rounded).monospacedDigit()).foregroundStyle(Color.petMuted.opacity(0.7))
                                    }.frame(width: 72, alignment: .trailing)
                                    Text(seg.text).textSelection(.enabled).lineSpacing(2)
                                }
                            }
                        }.card()
                    }

                    HStack {
                        Spacer()
                        Button {
                            if confirmDelete { store.delete(note); back() } else { confirmDelete = true; DispatchQueue.main.asyncAfter(deadline: .now() + 3) { confirmDelete = false } }
                        } label: { Text(confirmDelete ? "Delete for real?" : "Delete").foregroundStyle(confirmDelete ? .red : Color.petMuted) }
                        .buttonStyle(.plain).font(.system(size: 11.5, weight: .semibold, design: .rounded))
                    }.padding(.top, 4)
                }
                .padding(.horizontal, 16).padding(.bottom, 16).padding(.top, 4)
            }
        }
        .onAppear { title = note.title; DispatchQueue.main.async { titleFocused = false; NSApp.keyWindow?.makeFirstResponder(nil) } }
        .onChange(of: note.title) { _, v in if v != title { title = v } }
    }

    var meta: String {
        var parts = [note.date.formatted(date: .long, time: .shortened)]
        if note.duration > 0 { parts.append(mmss(note.duration)) }
        let words = note.segments.reduce(0) { $0 + $1.text.split(separator: " ").count }
        if words > 0 { parts.append("\(words) words") }
        return parts.joined(separator: " · ")
    }
    var speakers: [String] {
        var seen: [String] = []
        for s in note.segments where !seen.contains(s.speaker) { seen.append(s.speaker) }
        return seen
    }
    var everything: String {
        NoteProcessor.exportMarkdown(note)
    }
    func copyPill(_ label: String, key: String, text: String, disabled: Bool) -> some View {
        Button {
            NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string)
            copied = key
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) { if copied == key { copied = nil } }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: copied == key ? "checkmark" : "doc.on.doc").font(.system(size: 10, weight: .bold))
                Text(copied == key ? "Copied" : label)
            }
        }
        .buttonStyle(Pill(filled: false)).disabled(disabled).opacity(disabled ? 0.45 : 1)
    }
    func rename(_ s: String) {
        var n = note; n.speakerNames[s] = newName.trimmingCharacters(in: .whitespaces); save(n); renaming = nil
    }
    func save(_ note: Note) {
        do { try store.saveNote(note); saveError = nil }
        catch { saveError = "Could not save meeting notes: \(error.localizedDescription)" }
    }
    func rewrite() {
        writing = true
        Task {
            let n = await NoteProcessor.summarize(note)
            await MainActor.run { save(n); writing = false }
        }
    }
}

/// Renders the small Markdown subset the note writer produces: ## headings, **bold**, - bullets, - [ ] tasks.
struct NotesText: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var text: String
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(text.split(separator: "\n", omittingEmptySubsequences: false).enumerated()), id: \.offset) { _, raw in
                let line = String(raw).trimmingCharacters(in: .whitespaces)
                if line.isEmpty {
                    Spacer().frame(height: 2)
                } else if line.hasPrefix("#") {
                    SectionTitle(text: line.trimmingCharacters(in: CharacterSet(charactersIn: "# "))).padding(.top, 6)
                } else if line.hasPrefix("- [ ]") || line.hasPrefix("- [x]") || line.hasPrefix("- [X]") {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: line.hasPrefix("- [ ]") ? "circle" : "checkmark.circle.fill").font(.system(size: 12)).foregroundStyle(Color.petAccent).padding(.top, 2)
                        inline(String(line.dropFirst(5)))
                    }
                } else if line.hasPrefix("- ") || line.hasPrefix("• ") {
                    HStack(alignment: .top, spacing: 8) {
                        Circle().fill(Color.petAccent).frame(width: 6, height: 6).padding(.top, 6)
                        inline(String(line.dropFirst(2)))
                    }
                } else {
                    inline(line)
                }
            }
        }
        .textSelection(.enabled)
    }
    func inline(_ s: String) -> Text {
        if let a = try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) { return Text(a) }
        return Text(s)
    }
}

// MARK: - Words
struct WordsView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @EnvironmentObject var store: Store
    @State private var newWord = ""
    @State private var heard = ""
    @State private var meant = ""
    @State private var fixingID: UUID?
    @State private var fixText = ""
    @State private var copiedID: UUID?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Teach me a word")
                    Text("Names, acronyms and meanings. The speech engine listens for them, and Squipi reads this list when it chats, so \"Yellow means hello when I say it\" works in both. For a word the engine mishears, add a replacement below.").font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    HStack {
                        TextField("A name, a product, an acronym…", text: $newWord)
                            .textFieldStyle(.plain).padding(10)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                            .onSubmit { store.addWord(newWord); newWord = "" }
                        Button { store.addWord(newWord); newWord = "" } label: { Image(systemName: "plus").font(.system(size: 13, weight: .bold)) }
                            .buttonStyle(Pill())
                    }
                    if store.vocabulary.words.isEmpty {
                        Text("Nothing yet. I'll also suggest names I keep hearing.").font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    } else {
                        Flow(spacing: 6) {
                            ForEach(store.vocabulary.words, id: \.self) { w in
                                Chip(text: w, color: .petAccent) { store.removeWord(w) }
                            }
                        }
                    }
                }.card()

                if !store.suggestions.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        SectionTitle(text: "I keep hearing these")
                        Flow(spacing: 6) {
                            ForEach(store.suggestions, id: \.self) { w in
                                HStack(spacing: 4) {
                                    Button { store.addWord(w) } label: {
                                        HStack(spacing: 4) { Image(systemName: "plus").font(.system(size: 9, weight: .bold)); Text(w) }
                                    }.buttonStyle(Pill(filled: false))
                                    Button { store.ignoreSuggestion(w) } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)).foregroundStyle(Color.petMuted) }.buttonStyle(.plain)
                                }
                            }
                        }
                    }.card()
                }

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "When I say… write…")
                    HStack(spacing: 8) {
                        TextField("Ana", text: $heard).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                        Image(systemName: "arrow.right").foregroundStyle(Color.petMuted)
                        TextField("Anna", text: $meant).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                            .onSubmit { store.addReplacement(heard: heard, meant: meant); heard = ""; meant = "" }
                        Button { store.addReplacement(heard: heard, meant: meant); heard = ""; meant = "" } label: { Image(systemName: "plus").font(.system(size: 13, weight: .bold)) }.buttonStyle(Pill())
                    }
                    ForEach(store.vocabulary.replacements) { r in
                        HStack {
                            Text(r.heard).foregroundStyle(Color.petMuted)
                            Image(systemName: "arrow.right").font(.system(size: 10)).foregroundStyle(Color.petMuted)
                            Text(r.meant).fontWeight(.semibold)
                            Spacer()
                            Button { store.removeReplacement(r) } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .bold)).foregroundStyle(Color.petMuted) }.buttonStyle(.plain)
                        }
                    }
                }.card()

                if !store.dictations.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        SectionTitle(text: "Recent · copy, or fix one and I'll learn")
                        ForEach(store.dictations.prefix(8)) { d in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack(alignment: .top) {
                                    Text(d.date.formatted(date: .omitted, time: .shortened)).font(.system(size: 10.5, design: .rounded)).foregroundStyle(Color.petMuted).frame(width: 52, alignment: .leading)
                                    if fixingID == d.id {
                                        TextField("", text: $fixText, axis: .vertical).textFieldStyle(.plain)
                                            .onSubmit { store.learnFix(original: d.text, edited: fixText, dictationID: d.id); fixingID = nil }
                                    } else {
                                        Text(d.text).lineLimit(3).textSelection(.enabled)
                                    }
                                    Spacer(minLength: 4)
                                    Button {
                                        NSPasteboard.general.clearContents()
                                        NSPasteboard.general.setString(d.text, forType: .string)
                                        copiedID = d.id
                                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { if copiedID == d.id { copiedID = nil } }
                                    } label: { Image(systemName: copiedID == d.id ? "checkmark.circle.fill" : "doc.on.doc").font(.system(size: 11, weight: .bold)).foregroundStyle(Color.petAccent) }
                                    .buttonStyle(.plain).help("Copy")
                                    Button {
                                        if fixingID == d.id { store.learnFix(original: d.text, edited: fixText, dictationID: d.id); fixingID = nil }
                                        else { fixingID = d.id; fixText = d.text }
                                    } label: { Image(systemName: fixingID == d.id ? "checkmark" : "pencil").font(.system(size: 11, weight: .bold)).foregroundStyle(Color.petAccent) }
                                    .buttonStyle(.plain).help("Fix a word and I'll learn it")
                                }
                                Divider().opacity(0.3)
                            }
                        }
                    }.card()
                }
            }
            .padding(.horizontal, 16).padding(.bottom, 16).padding(.top, 8)
        }
    }
}

struct Chip: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var text: String
    var color: Color
    var remove: () -> Void
    var body: some View {
        HStack(spacing: 6) {
            Text(text).font(.system(size: 12, weight: .semibold, design: .rounded))
            Button(action: remove) { Image(systemName: "xmark").font(.system(size: 8, weight: .bold)) }.buttonStyle(.plain).opacity(0.6)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Capsule().fill(color.opacity(0.14)))
        .foregroundStyle(Color.petInk)
    }
}

struct Flow: Layout {
    var spacing: CGFloat = 8
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        layout(proposal.width ?? 360, subviews).size
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let l = layout(bounds.width, subviews)
        for (i, p) in l.points.enumerated() {
            subviews[i].place(at: CGPoint(x: bounds.minX + p.x, y: bounds.minY + p.y), proposal: .unspecified)
        }
    }
    private func layout(_ width: CGFloat, _ subviews: Subviews) -> (size: CGSize, points: [CGPoint]) {
        var x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0, pts: [CGPoint] = []
        for s in subviews {
            let sz = s.sizeThatFits(.unspecified)
            if x + sz.width > width, x > 0 { x = 0; y += rowH + spacing; rowH = 0 }
            pts.append(CGPoint(x: x, y: y))
            x += sz.width + spacing; rowH = max(rowH, sz.height)
        }
        return (CGSize(width: width, height: y + rowH), pts)
    }
}

// MARK: - Mind
struct MindView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    @ObservedObject var mind = Mind.shared
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Hold right ⌥ Option and talk, or type in Chat. Say things like: remember that the baseline survey was March 2023. Or: remind me at four to send the draft.")
                    .foregroundStyle(Color.petMuted).font(.system(size: 12, design: .rounded)).padding(.horizontal, 4)

                UserInstructionsView()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Reminders")
                    if mind.reminders.isEmpty { Text("None yet.").foregroundStyle(Color.petMuted).font(.system(size: 12, design: .rounded)) }
                    ForEach(mind.reminders.sorted { ($0.due ?? .distantFuture) < ($1.due ?? .distantFuture) }) { r in
                        HStack(alignment: .top, spacing: 10) {
                            Button { var x = r; x.done.toggle(); mind.update(x) } label: {
                                Image(systemName: r.done ? "checkmark.circle.fill" : "circle").foregroundStyle(Color.petAccent)
                            }.buttonStyle(.plain)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(r.text).strikethrough(r.done).foregroundStyle(r.done ? Color.petMuted : Color.petInk)
                                if let d = r.due { Text(d.formatted(date: .abbreviated, time: .shortened)).font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted) }
                            }
                            Spacer()
                            Button { mind.remove(r) } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .bold)).foregroundStyle(Color.petMuted) }.buttonStyle(.plain)
                        }
                    }
                }.card()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "What it remembers about you")
                    if mind.facts.isEmpty { Text("Nothing yet. Squipi learns from conversations; you can delete anything here.").foregroundStyle(Color.petMuted).font(.system(size: 12, design: .rounded)) }
                    ForEach(mind.facts, id: \.self) { f in
                        HStack(alignment: .top) {
                            Circle().fill(Color.petAccent).frame(width: 6, height: 6).padding(.top, 6)
                            Text(f)
                            Spacer()
                            Button { mind.forget(f) } label: { Image(systemName: "xmark").font(.system(size: 10, weight: .bold)).foregroundStyle(Color.petMuted) }.buttonStyle(.plain)
                        }
                    }
                }.card()

                HStack {
                    Spacer()
                    Button("Clear datasets and outputs") {
                        Task { @MainActor in
                            let preview = (try? await Worker.shared.clearProject(dryRun: true)) ?? [:]
                            let mb = Double(preview["bytes"] as? Int ?? 0) / 1_048_576
                            let alert = NSAlert(); alert.messageText = "Clear datasets and outputs?"
                            alert.informativeText = "Removes \(preview["datasets"] as? Int ?? 0) registered datasets, \(preview["documents"] as? Int ?? 0) documents, and \(preview["items"] as? Int ?? 0) saved outputs and caches (\(String(format: "%.0f", mb)) MB). Your original files are not touched. Research memory, the task journal and learning are kept."
                            alert.addButton(withTitle: "Clear"); alert.addButton(withTitle: "Cancel")
                            guard alert.runModal() == .alertFirstButtonReturn else { return }
                            let line = await Analysis.shared.clearAll()
                            (NSApp?.delegate as? AppDelegate)?.panel.js("pet.setCaption('\(Analysis.jsString(line))')")
                            NSLog("ANALYSIS %@", line)
                        }
                    }.buttonStyle(Pill(filled: false))
                    Button("Clear facts and reminders") { mind.forgetEverything(); (NSApp?.delegate as? AppDelegate)?.brain.forget() }.buttonStyle(Pill(filled: false))
                }
                ResearchMemoryView()
            }
            .padding(.horizontal, 16).padding(.bottom, 16).padding(.top, 8)
        }
    }
}

// MARK: - Me
struct MeView: View {
    @AppStorage("squipiColour") private var selectedAppearance = "blue"
    @AppStorage("squipiLanguage") private var selectedLanguage = "en"
    var onEngineChange: (String) -> Void
    @AppStorage("engine") private var engine = "apple"
    @AppStorage("summaryEngine") private var summaryEngine = "apple"
    @AppStorage("launchAtLogin") private var launchAtLogin = false
    @AppStorage("sounds") private var sounds = true
    @AppStorage("wander") private var wander = false
    @AppStorage("reducedMotion") private var reducedMotion = false
    @AppStorage("voiceOn") private var voiceOn = true
    @AppStorage("voiceReadBack") private var voiceReadBack = false
    @AppStorage("voiceName") private var voiceName = PetVoice.defaultVoice
    @AppStorage("brainOn") private var brainOn = true
    @AppStorage("chattiness") private var chattiness = "some"
    @AppStorage("brainModel") private var brainModel = Brain.defaultTier
    @AppStorage("skirtLook") private var skirtLook = "stars"
    @AppStorage("finFlower") private var finFlower = false
    @AppStorage("nebiusBaseURL") private var nebiusBaseURL = NebiusProvider.defaultBaseURL
    @AppStorage("nebiusModel") private var nebiusModel = NebiusProvider.defaultModel
    @AppStorage("nebiusMeetingModel") private var nebiusMeetingModel = ""
    @AppStorage("ollamaBaseURL") private var ollamaBaseURL = OllamaProvider.defaultBaseURL
    @AppStorage("ollamaModel") private var ollamaModel = OllamaProvider.defaultModel
    @AppStorage("ollamaConversationModel") private var ollamaConversationModel = ""
    @AppStorage("ollamaResearchModelOverride") private var ollamaResearchModelOverride = ""
    @State private var pullStatus = ""
    @State private var pulling = false
    @AppStorage("mlxThink") private var mlxThink = false
    @ObservedObject private var mlx = MLXRuntime.shared
    @AppStorage("ollamaThink") private var ollamaThink = false
    @AppStorage("ollamaKeepAlive") private var ollamaKeepAlive = OllamaProvider.defaultKeepAlive
    @AppStorage("quickConversation") private var quickConversation = true
    @AppStorage("localOnlyMode") private var localOnlyMode = false
    @AppStorage("relaxedSpeechPauses") private var relaxedSpeechPauses = false
    @AppStorage("hermesModel") private var hermesModel = ""
    @AppStorage("hermesAllowNetwork") private var hermesAllowNetwork = false
    @State private var hermesStatus = "checking…"
    @ObservedObject private var localContext = LocalContext.shared
    @State private var hermesBusy = false
    @State private var ollamaModels: [String] = []
    @State private var ollamaStatus = "checking…"
    @ObservedObject private var router = ModelRouter.shared
    @ObservedObject private var worker = Worker.shared
    @State private var selectedKumoTask: KumoSpecialist?
    @State private var brainStatus = (NSApp?.delegate as? AppDelegate)?.brain.status ?? "off"
    @State private var nebiusKey = ""
    @State private var nebiusKeySaved = false
    @State private var nebiusTest = ""
    @State private var nebiusTesting = false
    @State private var nebiusModels: [String] = []

    private var app: AppDelegate? { NSApp?.delegate as? AppDelegate }
    private var selectedMeetingModel: String { NebiusModels.meetingModel(saved: nebiusMeetingModel, researchModel: nebiusModel) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    Image(systemName: "hand.raised.fingers.spread").font(.system(size: 16)).foregroundStyle(Color.petAccent)
                    Text("**Double-click Squipi** (or press the mic in Chat) and talk; she stops listening when you pause. With Accessibility granted, holding **right ⌥ Option** works too, and **fn** dictates into the app in front of you.")
                        .foregroundStyle(Color.petMuted).font(.system(size: 12.5, design: .rounded))
                }
                .padding(.horizontal, 4)

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Brain")
                    choice("Squipi + Qwen 3.5 9B • on this Mac", "ollama", router.mode.rawValue) { _ in router.mode = .ollama }
                    choice("Squipi + Qwen 3.5 9B • MLX on this Mac (experimental)", "mlx", router.mode.rawValue) { _ in router.mode = .mlx }
                    choice("Nebius Token Factory • \(NebiusModels.displayName(for: nebiusModel)) (cloud; asks before sharing context)", "nebius", router.mode.rawValue) { _ in router.mode = .nebius }
                    Text("This brain understands your requests, plans research and explains results.")
                        .font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    Divider().padding(.vertical, 4)
                    SectionTitle(text: "NVIDIA Kumo · run a model directly")
                    Text("Choose a model below, select your data and run. These forms use Kumo directly, without Qwen or Nebius.")
                        .font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    ForEach(KumoSpecialist.allCases) { specialist in
                        Button { selectedKumoTask = specialist } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(specialist.title).font(.system(size: 12.5, weight: .semibold, design: .rounded))
                                    Spacer()
                                    Image(systemName: "arrow.up.forward.square").font(.system(size: 12))
                                }
                                Text(specialist.summary).font(.system(size: 11.5, design: .rounded))
                                Text("Example: " + specialist.example).font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                            }.foregroundStyle(Color.petInk).padding(11)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(RoundedRectangle(cornerRadius: 11).fill(Color.petAccentSoft.opacity(0.4)))
                                .contentShape(Rectangle())
                        }.buttonStyle(.plain)
                    }
                    Text("Experimental research models. Validate predictions on your data before making decisions.")
                        .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                }.card()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Ollama")
                    HStack {
                        Image(systemName: ollamaModels.isEmpty ? "circle.dotted" : "checkmark.circle.fill").foregroundStyle(ollamaModels.isEmpty ? Color.petMuted : Color.petAccent)
                        Text(ollamaStatus).font(.system(size: 12, design: .rounded))
                        Spacer()
                        Button("Refresh") { checkOllama() }.buttonStyle(Pill(filled: false))
                    }
                    Text("Local research model: \(ollamaResearchModelOverride.isEmpty ? "Qwen 3.5 9B" : ollamaResearchModelOverride + " (experimental override)")").font(.system(size: 12, design: .rounded))
                    Text("Tested for household-data cleaning and Word reports with charts. Exact PowerPoint-template completion is still being tested.").font(.caption).foregroundStyle(Color.petMuted)
                    TextField("Conversation model (optional, e.g. a small 4B model for greetings and quick questions)", text: $ollamaConversationModel).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                    TextField("Research model override (experimental; the nightly matrix is how to judge it)", text: $ollamaResearchModelOverride).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                    HStack(spacing: 8) {
                        Button(pulling ? "Pulling…" : "Pull conversation model") { pull(ollamaConversationModel) }.buttonStyle(Pill(filled: false)).disabled(pulling || ollamaConversationModel.trimmingCharacters(in: .whitespaces).isEmpty)
                        Button(pulling ? "Pulling…" : "Pull research override") { pull(ollamaResearchModelOverride) }.buttonStyle(Pill(filled: false)).disabled(pulling || ollamaResearchModelOverride.trimmingCharacters(in: .whitespaces).isEmpty)
                        if !pullStatus.isEmpty { Text(pullStatus).font(.caption).foregroundStyle(Color.petMuted).lineLimit(2) }
                    }
                    Text("Pulling downloads the model from the Ollama library (the only network call here). Conversation answers show the model in their footer; research tasks always run the research model behind the full set of checks.").font(.caption).foregroundStyle(Color.petMuted)
                    TextField("Server", text: $ollamaBaseURL).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                    Toggle("Fast answers for everyday questions", isOn: $quickConversation).toggleStyle(.switch).controlSize(.small)
                    Text("Greetings and simple explanations use a short prompt with thinking off. File work, reminders, personal memory and task follow-ups keep their tools. The first answer after a model unload may still take longer.").font(.caption).foregroundStyle(Color.petMuted)
                    Toggle("Extra thinking for the standard research agent", isOn: $ollamaThink).toggleStyle(.switch).controlSize(.small)
                    Text("Optional for difficult research; adds delay. Conversation and structured action recovery keep thinking off.").font(.caption).foregroundStyle(Color.petMuted)
                    HStack(spacing: 8) {
                        Text("Keep the model loaded after a reply").font(.system(size: 12, design: .rounded))
                        ForEach(OllamaProvider.keepAliveOptions, id: \.self) { v in Button(v) { ollamaKeepAlive = v }.buttonStyle(Pill(filled: ollamaKeepAlive == v)) }
                    }
                    Text("A loaded Qwen 3.5 9B holds about 6 GB of memory. A shorter time gives it back to other apps sooner; the next question then waits a few seconds while the model reloads.").font(.caption).foregroundStyle(Color.petMuted)
                    Text("Ollama + Qwen is the retained local setup; MLX is an experimental comparison. Hermes, NeMo and other local models remain in development testing. Nebius offers NVIDIA Nemotron and Moonshot Kimi K3 as cloud options.")
                        .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                }.card()
                .onAppear { checkOllama() }

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "MLX • experimental")
                    Text(mlx.status).font(.system(size: 12, design: .rounded))
                    HStack {
                        Button("Start MLX") { Task { do { try await mlx.ensureRunning() } catch { } } }.buttonStyle(Pill(filled: false))
                        Button("Refresh") { Task { await mlx.refresh() } }.buttonStyle(Pill(filled: false))
                        Button("Stop MLX") { mlx.stop() }.buttonStyle(Pill(filled: false))
                    }
                    Toggle("Extra thinking for MLX research", isOn: $mlxThink).toggleStyle(.switch).controlSize(.small)
                    Text("Qwen 3.5 9B, 4-bit, runs locally using Metal. The first request loads the model. Squipi keeps the same research tools and persistent memory. Full research reliability is still being tested. Setup downloads about 6 GB; no API key is used. Avoid running two large models together.").font(.caption).foregroundStyle(Color.petMuted)
                }.card()
                .onAppear { Task { await mlx.refresh() } }

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Conversation")
                    HStack(spacing: 6) {
                        Text("Humour").font(.system(size: 12, design: .rounded)).foregroundStyle(Color.petMuted)
                        ForEach([("quiet", "Off"), ("some", "Some"), ("lots", "Cheeky")], id: \.0) { id, label in
                            Button(label) { chattiness = id }.buttonStyle(Pill(filled: chattiness == id))
                        }
                    }
                }.card()
                .onAppear { app?.brain.onStatus = { s in brainStatus = s } }

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Privacy")
                    Toggle("Local-only mode (no cloud model, no web requests, nothing leaves this Mac)", isOn: $localOnlyMode).toggleStyle(.switch).controlSize(.small)
                    Text("Every request that would leave this Mac is recorded (time, destination, purpose, size; never content) in egress.jsonl in Squipi's data folder. Cloud approvals for a project objective expire after seven days.")
                        .font(.system(size: 11)).foregroundStyle(Color.petMuted)
                    SectionTitle(text: "Nebius • cloud research models")
                    Flow(spacing: 6) {
                        ForEach(NebiusModels.presets) { preset in
                            Button(preset.label) { selectNebiusModel(preset.id) }
                                .buttonStyle(Pill(filled: nebiusModel == preset.id))
                        }
                    }
                    Text("Choose a model or enter a custom ID below. Availability depends on your Nebius account; Test checks the selected model.").font(.caption).foregroundStyle(Color.petMuted)
                    HStack(spacing: 8) {
                        SecureField("Nebius Token Factory API key", text: $nebiusKey)
                            .textFieldStyle(.plain).padding(10)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                            .onSubmit { saveNebiusKey() }
                        Button(nebiusKeySaved ? "Saved" : "Save") { saveNebiusKey() }.buttonStyle(Pill(filled: !nebiusKeySaved))
                    }
                    TextField("Base URL", text: $nebiusBaseURL).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                    HStack(spacing: 8) {
                        TextField("Research model ID", text: $nebiusModel).textFieldStyle(.plain).padding(10).background(RoundedRectangle(cornerRadius: 12).fill(Color.petBG))
                        Button(nebiusTesting ? "Testing…" : "Test") { testNebius() }.buttonStyle(Pill(filled: true)).disabled(nebiusTesting)
                    }
                    if !nebiusModels.isEmpty {
                        Flow(spacing: 6) {
                            ForEach(NebiusModels.featuredAvailableModels(nebiusModels), id: \.self) { m in
                                Button(NebiusModels.displayName(for: m)) { selectNebiusModel(m) }.buttonStyle(Pill(filled: nebiusModel == m))
                            }
                        }
                        Menu("All available model IDs (\(nebiusModels.count))") {
                            ForEach(nebiusModels.sorted(), id: \.self) { m in
                                Button(m) { selectNebiusModel(m) }
                            }
                        }.font(.system(size: 11, design: .rounded))
                    }
                    if NebiusModels.isKimiK3(nebiusModel) {
                        Text("Kimi K3 uses reasoning while working. Research tools, files and memory remain in Squipi; cloud requests use your Nebius key.").font(.caption).foregroundStyle(Color.petMuted)
                    }
                    if !nebiusTest.isEmpty { Text(nebiusTest).font(.system(size: 11.5, design: .rounded)).foregroundStyle(nebiusTest.hasPrefix("Connected") || nebiusTest.hasPrefix("Key works") ? Color.petMuted : .orange).textSelection(.enabled) }
                    Text("Your Nebius key is stored in macOS Keychain and works for the available models on your account, including NVIDIA and Moonshot. Research approval shows the initial context. Project-objective approval survives resumptions and app restarts for the same provider and inputs; new inputs ask again. Meeting transcription and notes use Apple services locally.")
                        .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                    HStack { Spacer(); Button("Revoke project approvals") { CloudGate.shared.revokeAll() }.buttonStyle(Pill(filled: false)) }
                }.card()

                MacAccessView()
                FolderAccessView()
                VStack(alignment: .leading, spacing: 10) {
                    ResearchServicesView()
                    KumoSettingsView()
                    SectionTitle(text: "Data tools")
                    HStack {
                        Image(systemName: worker.status == .ready || worker.status == .stopped ? "checkmark.circle.fill" : "circle.dotted").foregroundStyle(worker.status == .ready || worker.status == .stopped ? Color.petAccent : Color.petMuted)
                        Text("Python worker: \(worker.status.label)")
                        Spacer()
                        Button(worker.isInstalled ? "Reinstall" : "Set up") { Task { await worker.install() } }.buttonStyle(Pill(filled: !worker.isInstalled)).disabled({ if case .installing = worker.status { return true } else { return false } }())
                    }
                    Text("Pinned pandas, numpy, scipy, pyarrow, openpyxl, pypdf, python-docx and python-pptx in ~/Library/Application Support/Squipi/worker-env. Set-up needs the network once; the tools then work offline. Tools: profile, clean (with personal-data removal), aggregate, compare groups, correlate, regress, charts, HTML/Word/PowerPoint reports, PDF/Word/PowerPoint reading.")
                        .font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                }.card()

                PersonalizationView()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Patterns")
                    Flow(spacing: 6) {
                        ForEach([("stars", "Star chart"), ("plain", "Plain"), ("floral", "Floral"), ("dark", "Deep colour"), ("darkFloral", "Dark floral")], id: \.0) { id, label in
                            Button(label) { skirtLook = id; app?.applyLookPref() }.buttonStyle(Pill(filled: skirtLook == id))
                        }
                    }
                    Toggle("Flower by her fin", isOn: $finFlower).toggleStyle(.switch).controlSize(.small).onChange(of: finFlower) { _, _ in app?.applyLookPref() }
                    Toggle("Reduced motion (no hops, slower springs)", isOn: $reducedMotion).toggleStyle(.switch).controlSize(.small).onChange(of: reducedMotion) { _, _ in app?.applyLookPref() }
                }.card()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Ears")
                    choice("Apple speech recognition, on this Mac", "apple", engine) { engine = $0; onEngineChange($0) }
                    Toggle("Wait longer for speech pauses", isOn: $relaxedSpeechPauses).toggleStyle(.switch).controlSize(.small)
                    Text("Spoken chat sends after about 1.2 seconds of silence. Turn this on for 2.8 seconds when dictating longer requests. You can also click the mic to send immediately.").font(.caption).foregroundStyle(Color.petMuted)
                    SpeechDiagnosticsView()
                    Text("Speech stays on this Mac. The language selected in Appearance chooses the recognition model; supported models may need a one-time download.").font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                }.card()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Voice")
                    Toggle("Talks back", isOn: $voiceOn).toggleStyle(.switch).controlSize(.small)
                    if voiceOn {
                        Flow(spacing: 6) {
                            ForEach(PetVoice.voices, id: \.self) { v in
                                Button(v) { voiceName = v; app?.voice.test() }
                                    .buttonStyle(Pill(filled: voiceName == v))
                            }
                        }
                        Toggle("Reads dictation back after typing it", isOn: $voiceReadBack).toggleStyle(.switch).controlSize(.small)
                        Text("Tap a voice to hear it. All voices are synthesised on this Mac.").font(.system(size: 11, design: .rounded)).foregroundStyle(Color.petMuted)
                    }
                }.card()

                MeetingTranscriptionSettingsView()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Meeting notes · Apple Intelligence")
                    Label(Summarizer.appleAvailable ? "Apple Intelligence is available on this Mac" : "Enable Apple Intelligence in System Settings", systemImage: Summarizer.appleAvailable ? "checkmark.circle.fill" : "exclamationmark.circle")
                    Text("Apple Speech transcribes the recording locally. Apple Intelligence turns that transcript into key points, decisions and action items. Meeting audio and transcript stay on this Mac.")
                        .font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    Text("Microphone and call audio retain separate channel labels. Individual-speaker diarisation is not enabled.")
                        .font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                }.card()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Permissions")
                    permRow("Microphone", ok: AVCaptureDevice.authorizationStatus(for: .audio) == .authorized, url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
                    permRow("Speech recognition", ok: SFSpeechRecognizer.authorizationStatus() == .authorized, url: "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition")
                    permRow("Accessibility (hotkeys right ⌥ / fn, and typing)", ok: Permissions.accessibilityTrusted, url: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
                    if !Permissions.accessibilityTrusted {
                        Text("Without Accessibility, macOS gives Squipi no key events, so the hotkeys stay silent. Double-click her or use the mic button instead, or add Squipi under Accessibility.")
                            .font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    }
                    permRow("System audio (hear your call)", ok: nil, url: "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture")
                    Divider().padding(.vertical, 2)
                    Text("Time, place and weather").font(.system(size: 12.5, weight: .semibold, design: .rounded))
                    Text("Time comes from the Mac's clock. Place uses Location (ask below). Weather comes from Apple Weather through a Shortcut you create once, so no developer account or outside service is needed.").font(.system(size: 11.5, design: .rounded)).foregroundStyle(Color.petMuted)
                    HStack(spacing: 8) {
                        Button("Allow location") { localContext.requestPlace() }.buttonStyle(Pill(filled: false))
                        Button("Test weather") { Task { _ = await localContext.weather() } }.buttonStyle(Pill(filled: false))
                    }
                    Text(localContext.placeStatus).font(.caption).foregroundStyle(Color.petMuted)
                    if !localContext.weatherStatus.isEmpty { Text(localContext.weatherStatus).font(.caption).foregroundStyle(Color.petMuted).textSelection(.enabled) }
                    if !localContext.lastWeather.isEmpty { Text(localContext.lastWeather).font(.system(size: 11.5, design: .rounded)).textSelection(.enabled) }
                    Text(LocalContext.setupText).font(.caption).foregroundStyle(Color.petMuted).textSelection(.enabled)
                    if !Permissions.accessibilityTrusted {
                        Text("Shows as granted but Squipi still can't type? Remove Squipi from the Accessibility list with the minus button, then add it again.")
                            .font(.system(size: 11.5, design: .rounded)).foregroundStyle(.orange)
                    }
                    if Permissions.fnKeyDoesSomethingElse {
                        Text("Your fn key also opens emoji/dictation. Set it to Do Nothing in System Settings › Keyboard.")
                            .font(.system(size: 11.5, design: .rounded)).foregroundStyle(.orange)
                    }
                }.card()

                VStack(alignment: .leading, spacing: 10) {
                    SectionTitle(text: "Manners")
                    Toggle("Sounds", isOn: $sounds).toggleStyle(.switch).controlSize(.small)
                        .onChange(of: sounds) { _, _ in app?.applySoundsPref() }
                    Toggle("Wanders around the screen", isOn: $wander).toggleStyle(.switch).controlSize(.small)
                        .onChange(of: wander) { _, _ in app?.applyWanderPref() }
                    Toggle("Wake up with my Mac", isOn: $launchAtLogin).toggleStyle(.switch).controlSize(.small)
                        .onChange(of: launchAtLogin) { _, v in
                            do { v ? try SMAppService.mainApp.register() : try SMAppService.mainApp.unregister() } catch { NSLog("login item: \(error)") }
                        }
                }.card()

                HStack { Spacer(); Button("Goodbye") { NSApp.terminate(nil) }.buttonStyle(Pill(filled: false)) }
            }
            .padding(.horizontal, 16).padding(.bottom, 16).padding(.top, 8)
        }
        .sheet(item: $selectedKumoTask) { KumoWorkbenchView(specialist: $0) }
        .onAppear {
            summaryEngine = "apple"
            nebiusKey = "" // Do not block Settings on a Keychain authorization prompt; enter a key to replace it.
        }
        .onChange(of: nebiusModel) { oldValue, _ in
            if nebiusMeetingModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                nebiusMeetingModel = NebiusModels.meetingModel(saved: nil, researchModel: oldValue)
            }
            nebiusTest = ""
            router.objectWillChange.send()
        }
    }

    func selectNebiusModel(_ model: String) {
        let selection = NebiusModels.selectingResearchModel(model, in: ["nebiusModel": nebiusModel, "nebiusMeetingModel": nebiusMeetingModel])
        nebiusMeetingModel = selection["nebiusMeetingModel"] ?? nebiusMeetingModel
        nebiusModel = selection["nebiusModel"] ?? nebiusModel
    }

    func checkHermes() {
        guard HermesRunner.isInstalled else { hermesStatus = "Hermes is not installed (~/.local/bin/hermes). See hermes-agent.nousresearch.com."; return }
        hermesStatus = "checking…"
        Task { hermesStatus = await HermesRunner.shared.isRegistered() ? "Ready: local Hermes profile is set up." : "Installed; local setup runs automatically on your next message." }
    }

    func setUpHermes() {
        hermesBusy = true
        Task { hermesStatus = await HermesRunner.shared.register(); hermesBusy = false }
    }

    /// `ollama pull <name>` through the Ollama CLI; progress lines land in the status label. User-triggered network call.
    func pull(_ name: String) {
        let model = name.trimmingCharacters(in: .whitespaces)
        guard !model.isEmpty, model.range(of: #"^[A-Za-z0-9._/:-]+$"#, options: .regularExpression) != nil else { pullStatus = "Model names use letters, digits, dots, slashes, colons and dashes."; return }
        pulling = true; pullStatus = "pulling \(model)…"
        Task.detached {
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/env"); p.arguments = ["ollama", "pull", model]
            var env = ProcessInfo.processInfo.environment; env["PATH"] = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"; p.environment = env
            let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
            var last = ""
            pipe.fileHandleForReading.readabilityHandler = { h in
                let chunk = String(decoding: h.availableData, as: UTF8.self)
                if let line = chunk.split(whereSeparator: { $0 == "\n" || $0 == "\r" }).last.map(String.init), !line.isEmpty { last = line; Task { @MainActor in self.pullStatus = String(line.prefix(80)) } }
            }
            do { try p.run(); p.waitUntilExit() } catch { last = error.localizedDescription }
            pipe.fileHandleForReading.readabilityHandler = nil
            let ok = p.terminationStatus == 0
            await MainActor.run { self.pulling = false; self.pullStatus = ok ? "\(model) is installed." : "Pull failed: \(last.prefix(100))"; self.checkOllama() }
        }
    }

    func checkOllama() {
        ollamaStatus = "checking…"
        Task {
            do {
                let models = try await OllamaProvider().listModels(key: OllamaProvider.key)
                ollamaModels = models.filter { $0 == SupportedResearchSetup.localModel }
                ollamaModel = SupportedResearchSetup.localModel
                let override = ollamaResearchModelOverride.trimmingCharacters(in: .whitespaces)
                let chat = ollamaConversationModel.trimmingCharacters(in: .whitespaces)
                var extras: [String] = []
                if !override.isEmpty { extras.append(models.contains(override) ? "override \(override) installed" : "override \(override) is not installed yet") }
                if !chat.isEmpty { extras.append(models.contains(chat) ? "conversation model \(chat) installed" : "conversation model \(chat) is not installed yet") }
                ollamaStatus = (ollamaModels.isEmpty
                    ? "Ollama is running. Install the supported model: `ollama pull qwen3.5:9b`."
                    : "Qwen 3.5 9B is installed and ready on this Mac.") + (extras.isEmpty ? "" : " " + extras.joined(separator: "; ") + ".")
            } catch {
                ollamaModels = []
                ollamaStatus = "Not reachable. Open the Ollama app (or run `ollama serve`)."
            }
        }
    }

    func saveNebiusKey() {
        guard !nebiusKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        NebiusProvider.setAPIKey(nebiusKey)
        nebiusKeySaved = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { nebiusKeySaved = false }
    }

    func testNebius() {
        if !nebiusKey.isEmpty { NebiusProvider.setAPIKey(nebiusKey) }
        guard let key = NebiusProvider.apiKey else { nebiusTest = ProviderError.noKey.localizedDescription; return }
        nebiusTesting = true
        nebiusTest = ""
        let testedModel = nebiusModel, testedBaseURL = nebiusBaseURL
        let provider = NebiusProvider()
        Task {
            defer { nebiusTesting = false }
            do {
                let models = try await provider.listModels(key: key)
                guard nebiusModel == testedModel, nebiusBaseURL == testedBaseURL else {
                    nebiusTest = "Settings changed. Test the selected model again."; return
                }
                nebiusModels = models
                let result = try await provider.testConnection(key: key)
                nebiusTest = nebiusModel == testedModel && nebiusBaseURL == testedBaseURL
                    ? result : "Settings changed. Test the selected model again."
            } catch {
                nebiusTest = nebiusModel == testedModel && nebiusBaseURL == testedBaseURL
                    ? error.localizedDescription : "Settings changed. Test the selected model again."
            }
        }
    }

    func choice(_ label: String, _ id: String, _ current: String, _ set: @escaping (String) -> Void) -> some View {
        Button { set(id) } label: {
            HStack {
                Image(systemName: current == id ? "largecircle.fill.circle" : "circle").foregroundStyle(Color.petAccent)
                Text(label)
                Spacer()
            }
        }.buttonStyle(.plain)
    }

    func permRow(_ label: String, ok: Bool?, url: String) -> some View {
        HStack {
            Image(systemName: ok == true ? "checkmark.circle.fill" : (ok == false ? "circle" : "circle.dotted")).foregroundStyle(ok == true ? Color.petAccent : Color.petMuted)
            Text(label)
            Spacer()
            Button("Open") { if let u = URL(string: url) { NSWorkspace.shared.open(u) } }.buttonStyle(.plain).foregroundStyle(Color.petMuted).font(.system(size: 11.5, weight: .semibold, design: .rounded))
        }
    }
}
