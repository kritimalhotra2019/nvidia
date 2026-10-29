import Foundation

// All external boundaries are test doubles. The shell harness compiles the
// production Conversation methods for attachment, picker, send, cancel and clear.
@MainActor enum AttachmentFixture {
    static var replies: [(String, Bool)] = []
}

@MainActor final class Store {
    static let shared = Store()
    let dir = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
}

@MainActor final class Agent {
    struct Step {
        var tool: String
        var ok: Bool
        var summary: String
        var outputs: [URL]
        var elapsed: Double
    }
    static let shared = Agent()
    var onStep: ((Step) -> Void)?
}

@MainActor final class Analysis {
    static let shared = Analysis()
    var registered: [URL] = []
    func register(file: URL) async -> String {
        registered.append(file)
        await Task.yield()
        return "Synthetic registration for \(file.lastPathComponent)"
    }
}

@MainActor final class Worker {
    static let shared = Worker()
    func cancelAll() {}
}
@MainActor final class HermesRunner {
    static let shared = HermesRunner()
    func cancel() {}
}

enum PetWebView {
    static let dataExtensions: Set<String> = ["csv", "tsv", "txt", "xlsx", "xls", "pdf", "docx", "pptx", "md", "parquet"]
}
struct UTType {
    static let commaSeparatedText = UTType()
    static let tabSeparatedText = UTType()
    static let plainText = UTType()
    static let spreadsheet = UTType()
    static let pdf = UTType()
    init() {}
    init?(filenameExtension: String) {}
}

@MainActor final class FixtureApplication {
    var delegate: AnyObject?
    func activate(ignoringOtherApps: Bool) {}
}
@MainActor var NSApp: FixtureApplication! = FixtureApplication()

@MainActor final class AppDelegate {
    final class Panel {
        var visible = false
        func show() { visible = true }
        func js(_ source: String) {}
    }
    final class Hub {
        var visible = false
        var tab = 4
        var showCount = 0
        func show() { visible = true; showCount += 1 }
        func selectTab(_ tab: Int) { self.tab = tab }
    }
    final class Voice {
        var lines: [String] = []
        func say(_ text: String, force: Bool) { lines.append(text) }
    }
    final class Brain {
        func forget() {}
        func stop() {}
    }
    let panel = Panel()
    let hub = Hub()
    let voice = Voice()
    let brain = Brain()
    func hush() {}
}

@MainActor final class NSOpenPanel {
    enum Response { case OK, cancel }
    static var latest: NSOpenPanel?
    var allowedContentTypes: [UTType] = []
    var allowsOtherFileTypes = false
    var message = ""
    var prompt = ""
    var canChooseDirectories = true
    var allowsMultipleSelection = false
    var urls: [URL] = []
    private var completion: ((Response) -> Void)?
    init() { Self.latest = self }
    func begin(completionHandler: @escaping (Response) -> Void) {
        completion = completionHandler
        // The real open panel makes the hub resign key and hide. This is the
        // exact condition that previously triggered an unsolicited overview.
        (NSApp.delegate as? AppDelegate)?.hub.visible = false
    }
    func finish(_ response: Response, files: [URL] = []) {
        urls = files
        completion?(response)
        completion = nil
    }
}

@main struct AttachmentChecks {
    @MainActor static func main() async throws {
        var checks = 0
        func check(_ condition: Bool, _ message: String) {
            checks += 1
            if !condition { fatalError(message) }
        }
        func settle(until condition: @MainActor () -> Bool) async {
            for _ in 0..<1_000 {
                if condition() { return }
                await Task.yield()
            }
            fatalError("Asynchronous composer callback did not finish")
        }
        let app = AppDelegate()
        NSApp.delegate = app
        let convo = Conversation.shared
        let dir = Store.shared.dir
        let data = dir.appendingPathComponent("study data.csv")
        let document = dir.appendingPathComponent("research notes.md")
        let nextFile = dir.appendingPathComponent("next study.csv")
        try "group,value\na,1\nb,2\n".write(to: data, atomically: true, encoding: .utf8)
        try "# Synthetic study\nOnly a test fixture.\n".write(to: document, atomically: true, encoding: .utf8)
        try "value\n3\n".write(to: nextFile, atomically: true, encoding: .utf8)
        check(convo.messages.isEmpty, "The harness must use an isolated empty store")

        convo.draft = "Compare the groups; do not create an overview."
        app.hub.visible = true
        app.hub.tab = 0
        convo.pickFiles()
        let picker = NSOpenPanel.latest!
        check(!app.hub.visible, "Picker test must reproduce the hidden hub condition")
        check(picker.allowsMultipleSelection && !picker.canChooseDirectories, "The picker must accept multiple files")
        check(convo.attachments.isEmpty && Analysis.shared.registered.isEmpty, "Opening the picker must not stage or inspect a file")
        picker.finish(.OK, files: [data, document, data])
        await settle { convo.attachments.count == 2 }
        check(convo.attachments == [data, document], "Picker selection must stage files once and retain order even after the hub hides")
        check(app.hub.visible && app.hub.tab == 0, "A completed attachment must restore Chat")
        check(convo.draft == "Compare the groups; do not create an overview.", "Picking files must preserve the user's typed instruction")
        check(convo.messages.isEmpty && !convo.busy, "Attaching must not start a conversation turn")
        check(Analysis.shared.registered.isEmpty && AttachmentFixture.replies.isEmpty, "Attaching must not inspect a file or call the model")

        for empty in ["", " ", "\n\t \n"] {
            check(!convo.send(empty), "An attached file without an instruction must not submit")
        }
        check(convo.attachments == [data, document] && convo.messages.isEmpty, "Blank submissions must preserve pending files and messages")
        check(Analysis.shared.registered.isEmpty && AttachmentFixture.replies.isEmpty, "Blank submissions must not create any work")

        convo.busy = true
        check(!convo.send("Clean this file"), "Busy submissions must be rejected")
        check(!convo.send("Clean this file", spoken: true), "Busy spoken submissions must also be rejected")
        check(convo.attachments == [data, document] && !convo.draft.isEmpty, "Rejected submissions must retain the draft and its files")
        check(convo.messages.isEmpty && Analysis.shared.registered.isEmpty, "A rejected submission must not register files or create a message")
        convo.busy = false

        convo.removeAttachment(dir.appendingPathComponent("nested/../study data.csv"))
        check(convo.attachments == [document], "Removing a path alias must remove only that attachment")
        convo.attach([data, dir.appendingPathComponent("nested/../study data.csv"), URL(string: "https://fixture.invalid/file.csv")!, dir.appendingPathComponent("program.exe")])
        check(convo.attachments == [document, data], "Duplicate paths and non-file/unsupported URLs must not create extra attachments")
        convo.pickFiles()
        NSOpenPanel.latest!.finish(.cancel, files: [nextFile])
        await Task.yield()
        check(convo.attachments == [document, data] && !convo.draft.isEmpty, "Cancelling a file picker must preserve the composed turn")
        check(Analysis.shared.registered.isEmpty && AttachmentFixture.replies.isEmpty, "Picker cancellation and attachment removal must not create work")

        let instruction = "Remove duplicate rows only. Preserve all columns."
        check(convo.send(" \n" + instruction + "\n "), "A real instruction with staged files must submit")
        check(convo.busy && convo.attachments.isEmpty, "Accepted submission must consume only its current pending files")
        check(convo.messages.count == 1 && convo.messages[0].text == instruction + "\n📎 research notes.md, study data.csv", "The user message must contain their exact instruction and selected file names")
        convo.attach([nextFile])
        await settle { AttachmentFixture.replies.count == 1 }
        check(Analysis.shared.registered == [document, data], "Only the files attached at explicit submission may be registered")
        check(convo.attachments == [nextFile], "Files attached during a running turn must wait for the next instruction")
        check(AttachmentFixture.replies[0].0 == instruction + "\n\n[Synthetic registration for research notes.md]\n\n[Synthetic registration for study data.csv]", "The model must receive the user's instruction without any invented overview request")
        check(!convo.busy, "The submitted turn must finish")
        check(!convo.send("\n"), "A later attachment must also wait for its own instruction")
        check(convo.attachments == [nextFile] && Analysis.shared.registered.count == 2, "A blank follow-up must not consume the next attachment")

        convo.clear()
        check(convo.attachments.isEmpty && convo.draft.isEmpty && convo.messages.isEmpty, "Explicit New chat must clear the composition")
        check(Analysis.shared.registered.count == 2 && AttachmentFixture.replies.count == 1, "Clearing chat must not create extra file work")

        app.hub.visible = false
        let priorHubShows = app.hub.showCount
        convo.draft = "Read this in the workspace."
        convo.pickFiles(showChat: false)
        NSOpenPanel.latest!.finish(.OK, files: [document])
        await settle { convo.attachments == [document] }
        check(!app.hub.visible && app.hub.showCount == priorHubShows, "Workspace file selection must retain the workspace composer instead of opening another window")
        check(convo.draft == "Read this in the workspace.", "Workspace file selection must preserve its shared draft")
        convo.attach([nextFile], showChat: false)
        check(convo.attachments == [document, nextFile] && app.hub.showCount == priorHubShows, "Workspace drops must stage in the same composer")
        check(Analysis.shared.registered.count == 2 && AttachmentFixture.replies.count == 1, "Workspace attachment must wait for an instruction too")
        convo.clear()
        print("Attachment checks passed: \(checks) assertions using production composer methods; no live services or user state.")
    }
}
