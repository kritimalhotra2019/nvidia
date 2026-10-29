import Foundation
import AVFoundation

// The isolated harness never reads real credentials or calls a real model/network endpoint.
enum Keychain {
    static func get(_ account: String) -> String? { fatalError("Tests must not access credentials") }
    static func set(_ value: String, account: String) { fatalError("Tests must not write credentials") }
}
@MainActor final class CloudGate {
    static let shared = CloudGate()
    var requests: [[String: Any]] = []
    var scopes: [String] = []
    var allowed = false
    var onApproval: (() -> Void)?
    static func logEgress(decision: String, project: String, scope: String, purpose: String, bytes: Int) { fatalError("No API uploads in meeting checks") }
    func approve(payload: [String: Any], project: String, scopeKey: String, purpose: String) async -> Bool {
        requests.append(payload); scopes.append(scopeKey); onApproval?(); return allowed
    }
}

struct ChatMessage {
    var role: String
    var content: String
    var json: [String: Any] { ["role": role, "content": content] }
}
struct FixtureModelReply {
    var text = "# Research review\n\n## Decisions\n- Send the draft."
    var finishReason: String? = "stop"
    var toolCalls: [String] = []
}
enum ProviderError: Error { case noKey, badURL, localOnly }
final class InferenceCoordinator {
    static let shared = InferenceCoordinator()
    func beginModel() throws -> UUID { fatalError("No API models in meeting checks") }
    func endModel(_ id: UUID) {}
}
final class NebiusProvider {
    static let keychainAccount = "nebius"
    @MainActor static var apiKey: String? = "synthetic-key"
    let baseURL: String
    let model: String
    var reply = FixtureModelReply()
    var failure: Error?
    var calls = 0
    var sentMessages: [ChatMessage] = []
    var sentKey = ""
    var tools: [[String: Any]]?
    var streamed = false
    var tokenLimit = 0
    init(baseURL: String = "https://fixture.invalid/v1", model: String = "nvidia/Nemotron-3.5-Super-VL", modelOverride: String? = nil) {
        self.baseURL = baseURL; self.model = modelOverride ?? model
    }
    func complete(messages: [ChatMessage], tools: [[String: Any]]?, key: String, stream: Bool,
                  temperature: Double, maxTokens: Int, think: Bool,
                  onDelta: ((String?, String?) -> Void)?) async throws -> FixtureModelReply {
        calls += 1; sentMessages = messages; sentKey = key; self.tools = tools; streamed = stream; tokenLimit = maxTokens
        if let failure { throw failure }
        return reply
    }
}

final class FixtureTranscriber: Transcriber {
    let name = "Synthetic fixture"
    var vocabulary: [String] = []
    var prepared = 0
    var callCount = 0
    var failOnCall: Int?
    var responses: [[TimedWord]]
    init(_ responses: [[TimedWord]]) { self.responses = responses }
    func prepare() async throws { prepared += 1 }
    func transcribe(_ samples16k: [Float]) async throws -> String { AppleTranscriber.join(try await transcribeTimed(samples16k)) }
    func transcribeTimed(_ samples16k: [Float]) async throws -> [TimedWord] {
        callCount += 1
        if callCount == failOnCall { throw NSError(domain: "fixture", code: 3, userInfo: [NSLocalizedDescriptionKey: "Synthetic recognition error"]) }
        return responses.removeFirst()
    }
}

@main struct MeetingChecks {
    @MainActor static func main() async throws {
        func check(_ condition: Bool, _ message: String) { if !condition { fatalError(message) } }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("squipi-meeting-checks-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let originalWords = (0..<3001).map { "word\($0)" }
        let chunks = Summarizer.chunk(originalWords.joined(separator: " "), maxWords: 1000)
        check(chunks.count == 4, "A long single-speaker turn must be split")
        check(chunks.allSatisfy { $0.split(whereSeparator: { $0.isWhitespace }).count <= 1000 }, "Chunk word limit must hold")
        check(chunks.flatMap { $0.split(whereSeparator: { $0.isWhitespace }).map(String.init) } == originalWords, "Chunking must retain every word in order")
        let unicode = String(repeating: "研究🐙", count: 200)
        let unicodeChunks = Summarizer.chunk(unicode, maxWords: 20)
        check(unicodeChunks.allSatisfy { $0.count <= 240 } && unicodeChunks.joined().replacingOccurrences(of: " ", with: "") == unicode, "Unicode without spaces must be bounded without lost characters")
        check(Summarizer.chunk(" \n ", maxWords: 10).isEmpty, "An empty transcript is not a model prompt")
        check(Summarizer.selectedEngine(nil) == .apple && Summarizer.selectedEngine("claude") == .apple && Summarizer.selectedEngine("unknown") == .apple, "Legacy or absent preferences must select Apple without reading another provider's key")
        check(Summarizer.selectedEngine("nebius") == .apple && Summarizer.selectedEngine("apple") == .apple, "Retired cloud preferences must select the Apple meeting writer")
        let savedPreferences = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        UserDefaults.standard.setVolatileDomain(["summaryEngine": "nebius", "meetingTranscriptionEngine": "nemotron"], forName: UserDefaults.argumentDomain)
        check(Summarizer.active == (Summarizer.appleAvailable ? .apple : nil), "Meeting availability must depend only on Apple Intelligence, without reading cloud credentials")
        UserDefaults.standard.setVolatileDomain(savedPreferences, forName: UserDefaults.argumentDomain)
        let parsed = Summarizer.split("## Key points\n- Keep every finding\n\n## Decisions\nNone made.")
        check(parsed.0 == "Meeting" && parsed.1.contains("## Key points"), "A section heading must not be discarded as the meeting title")
        let titled = Summarizer.split("# Research review\n\n**TL;DR** Approved the plan.")
        check(titled.0 == "Research review" && titled.1.contains("Approved"), "H1 title and summary must be retained")

        let micWords = [TimedWord(text: "We", start: 0, end: 0.2), TimedWord(text: "agreed", start: 0.2, end: 0.5), TimedWord(text: ".", start: 0.5, end: 0.6), TimedWord(text: "Next item", start: 4, end: 5)]
        let callWords = [TimedWord(text: "I will send the draft.", start: 1, end: 2), TimedWord(text: "By Friday.", start: 2.2, end: 3), TimedWord(text: "invalid", start: .nan, end: 10)]
        let segments = NoteProcessor.assemble(microphone: micWords, call: callWords)
        check(segments.count == 3 && segments[0].text == "We agreed.", "Transcript assembly must preserve turns and punctuation")
        check(segments.map(\.speaker) == ["me", "s1", "me"] && segments[1].end == 3, "Call audio must keep one honest channel label and correct times")
        var note = Note(); note.segments = segments; note.duration = 5; note.speakerNames["s1"] = "Call participants"
        note.captureWarning = "Call participants are not diarized."
        let generated = await NoteProcessor.summarize(note) { transcript in
            check(transcript.contains("Me: We agreed.") && transcript.contains("Call participants:"), "Speaker rename must reach the note writer")
            return ("Draft review", "## Decisions\n- Send the draft.\n\n## Action items\n- [ ] Call participants: send by Friday.")
        }
        check(generated.status == "ready" && generated.error == note.captureWarning, "Successful notes must retain capture limitations")
        let writerFailure = NSError(domain: "fixture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Writer unavailable"])
        let failed = await NoteProcessor.summarize(note) { _ in throw writerFailure }
        check(failed.status == "transcribed" && failed.segments == note.segments && failed.error?.contains("Writer unavailable") == true, "Writer failure must preserve transcript and avoid ready status")
        let empty = await NoteProcessor.summarize(note) { _ in ("Empty", " \n") }
        check(empty.status == "transcribed" && empty.error != nil, "Empty writer output must not report success")
        let noTranscript = await NoteProcessor.summarize(Note()) { _ in fatalError("An empty meeting must not call a model") }
        check(noTranscript.status == "failed", "Empty meetings must not be ready")
        do { _ = try await Summarizer.summarize(transcript: " \n"); fatalError("Empty transcript must fail before provider selection") } catch {}
        let neb = NebiusProvider()
        do { _ = try await Summarizer.nebius("Synthetic private transcript", provider: neb); fatalError("Declined cloud send must fail") } catch {}
        let preview = CloudGate.shared.requests.last!
        check(neb.calls == 0, "Declining approval must prevent the provider call")
        check((preview["messages"] as? [[String: String]])?.last?["content"] == "Transcript:\n\nSynthetic private transcript", "Cloud approval must show the full transcript")
        check(preview["model"] as? String == neb.model && preview["endpoint"] as? String == "https://fixture.invalid/v1/chat/completions", "Approval must name the exact model and endpoint")
        check(!CloudGate.shared.scopes.last!.contains("synthetic-key"), "The approval preview and scope must not expose the key")
        CloudGate.shared.allowed = true
        CloudGate.shared.onApproval = { NebiusProvider.apiKey = "replacement-key" }
        let cloudText = try await Summarizer.nebius("Synthetic private transcript", provider: neb)
        check(cloudText.contains("Research review") && neb.calls == 1 && neb.sentKey == "synthetic-key", "Approval must send the captured request with the key snapshotted before approval")
        check(neb.sentMessages.first?.role == "system" && neb.sentMessages.last?.content == "Transcript:\n\nSynthetic private transcript" && neb.tools == nil && !neb.streamed && neb.tokenLimit == 4000, "Meeting summaries must send the reviewed transcript and instructions with no tools")
        CloudGate.shared.onApproval = nil
        let oldArguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        UserDefaults.standard.setVolatileDomain(["nebiusModel": NebiusModels.kimiK3, "nebiusMeetingModel": NebiusModels.lightning], forName: UserDefaults.argumentDomain)
        _ = try await Summarizer.nebius("Synthetic independent meeting writer selection")
        check(CloudGate.shared.requests.last?["model"] as? String == NebiusModels.lightning, "A K3 research selection must use the independently saved NVIDIA meeting model")
        check(UserDefaults.standard.string(forKey: "nebiusModel") == NebiusModels.kimiK3, "Writing a meeting must not change the research brain")
        UserDefaults.standard.setVolatileDomain(["nebiusModel": NebiusModels.kimiK3], forName: UserDefaults.argumentDomain)
        _ = try await Summarizer.nebius("Synthetic default meeting writer selection")
        check(CloudGate.shared.requests.last?["model"] as? String == NebiusModels.nemotron, "A fresh K3 research setup keeps NVIDIA as the meeting default")
        UserDefaults.standard.setVolatileDomain(oldArguments, forName: UserDefaults.argumentDomain)
        let failures: [(String, FixtureModelReply)] = [
            ("empty", FixtureModelReply(text: " \n")),
            ("truncated", FixtureModelReply(finishReason: "length")),
            ("refused", FixtureModelReply(finishReason: "refusal")),
            ("filtered", FixtureModelReply(finishReason: "content_filter")),
            ("unfinished", FixtureModelReply(finishReason: nil)),
            ("tool call", FixtureModelReply(toolCalls: ["unexpected"]))]
        for (label, response) in failures {
            neb.reply = response
            do { _ = try await Summarizer.nebius("Synthetic private transcript", provider: neb); fatalError("\(label) model result must fail") } catch {}
        }
        neb.failure = writerFailure
        let failedCloud = await NoteProcessor.summarize(note) { text in
            let result = try await Summarizer.nebius(text, provider: neb)
            return Summarizer.split(result)
        }
        check(failedCloud.status == "transcribed" && failedCloud.segments == note.segments && failedCloud.error?.contains("Writer unavailable") == true, "A Nebius request failure must preserve the transcript and report the failure")
        let insecure = NebiusProvider(baseURL: "http://fixture.invalid/v1")
        let unrelated = NebiusProvider(model: "another/model")
        let approvalsBefore = CloudGate.shared.requests.count
        for candidate in [insecure, unrelated] {
            do { _ = try await Summarizer.nebius("Synthetic private transcript", provider: candidate); fatalError("Invalid endpoint/model must fail") } catch {}
            check(candidate.calls == 0, "Rejected settings must never send the transcript")
        }
        NebiusProvider.apiKey = nil
        do { _ = try await Summarizer.nebius("Synthetic private transcript", provider: neb); fatalError("No key must fail") } catch {}
        check(CloudGate.shared.requests.count == approvalsBefore, "Invalid settings or a missing key must fail before asking for transcript approval")

        let store = Store(directory: dir.appendingPathComponent("store"))
        try store.saveNote(generated)
        let reopened = Store(directory: store.dir)
        check(reopened.notes.first == generated, "The complete note, renamed speakers, timestamps and warnings must survive saving")
        let broken = Store(directory: dir.appendingPathComponent("broken-store"))
        try FileManager.default.createDirectory(at: broken.dir.appendingPathComponent("notes.json"), withIntermediateDirectories: false)
        do { try broken.saveNote(generated); fatalError("Disk write failure must throw") } catch {}
        check(broken.notes.isEmpty, "A failed write must not publish a successfully saved note")
        let export = NoteProcessor.exportMarkdown(generated)
        check(export.contains("# Draft review") && export.contains("## Action items") && export.contains("[00:01–00:03] Call participants:") && export.contains("Next item") && export.contains("not diarized"), "Export must retain notes, complete transcript, times, speaker names and limitations")

        let micURL = dir.appendingPathComponent("mic.caf"), sysURL = dir.appendingPathComponent("call.caf")
        let micFile = try CafWriter(url: micURL), sysFile = try CafWriter(url: sysURL)
        let tone = (0..<32_000).map { Float(sin(Double($0) * 2 * .pi * 220 / 16_000)) * 0.1 }
        micFile.append(Array(tone.prefix(16_000))); micFile.append(Array(tone.suffix(16_000)))
        sysFile.append(tone)
        let micWriteError = await micFile.finish(), callWriteError = await sysFile.finish()
        check(micWriteError == nil && callWriteError == nil && micFile.frames == 32_000, "CAF writer must drain its queue and count actual written samples")
        let reloaded = try AudioFileLoader.load16kMono(path: micURL.path)
        check(abs(reloaded.count - 32_000) <= 32, "Synthetic CAF duration must survive readback; got \(reloaded.count) samples instead of 32000")
        let emptyAudio = try AudioFileLoader.writeTempCAF([])
        defer { try? FileManager.default.removeItem(at: emptyAudio) }
        let emptySamples = try AudioFileLoader.load16kMono(path: emptyAudio.path)
        check(emptySamples.isEmpty, "Empty audio must round-trip without a crash")
        let stereoURL = dir.appendingPathComponent("stereo48k.caf")
        func writeStereoFixture() throws {
            let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: 2, interleaved: false)!
            let file = try AVAudioFile(forWriting: stereoURL, settings: format.settings)
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 96_000)!
            buffer.frameLength = 96_000
            for channel in 0..<2 { for index in 0..<96_000 { buffer.floatChannelData![channel][index] = Float(sin(Double(index) * 2 * .pi * 220 / 48_000)) * 0.1 } }
            try file.write(from: buffer)
            file.close()
        }
        try writeStereoFixture()
        let converted = try AudioFileLoader.load16kMono(path: stereoURL.path)
        check(abs(converted.count - 32_000) <= 32, "Stereo resampling must drain the entire file; got \(converted.count) samples")
        let engine = FixtureTranscriber([[TimedWord(text: "My synthetic turn.", start: 0, end: 0.8)], [TimedWord(text: "The call turn.", start: 1, end: 1.5)]])
        let processed = try await NoteProcessor.process(note: Note(), micURL: micURL, sysURL: sysURL, engine: engine, vocabulary: ["Squipi"]) { text in
            check(text.contains("My synthetic turn") && text.contains("The call turn"), "Both synthetic channels must reach summarization")
            return ("Synthetic meeting", "Both channels were transcribed by the fixture.")
        }
        check(processed.status == "ready" && processed.duration >= 1.99 && engine.prepared == 1 && engine.vocabulary == ["Squipi"], "Audio pipeline must use actual duration and requested vocabulary")
        var checkpoint: Note?
        let checkpointEngine = FixtureTranscriber([[TimedWord(text: "Keep this before notes.", start: 0, end: 1)], []])
        let checkpointed = try await NoteProcessor.process(note: Note(), micURL: micURL, sysURL: sysURL, engine: checkpointEngine, vocabulary: [], saveTranscript: { checkpoint = $0 }) { _ in
            check(checkpoint?.status == "transcribed" && checkpoint?.segments.first?.text == "Keep this before notes.", "Transcript must be checkpointed before summary starts")
            throw NSError(domain: "fixture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Summary unavailable"])
        }
        check(checkpointed.status == "transcribed" && checkpointed.transcriptionProvider == "Synthetic fixture", "Failed summary preserves transcript and provider provenance")
        let partialEngine = FixtureTranscriber([[TimedWord(text: "Keep my transcript.", start: 0, end: 1)]])
        partialEngine.failOnCall = 2
        let partial = try await NoteProcessor.process(note: Note(), micURL: micURL, sysURL: sysURL, engine: partialEngine, vocabulary: []) { _ in ("Partial meeting", "Only the microphone was transcribed.") }
        check(partial.segments.first?.text == "Keep my transcript." && partial.error?.contains("call transcript could not") == true, "A failing channel must not discard the other transcript or hide the gap")
        let silentURL = dir.appendingPathComponent("silence.caf")
        let silence = try CafWriter(url: silentURL); silence.append(Array(repeating: 0, count: 32_000)); _ = await silence.finish()
        let unusedEngine = FixtureTranscriber([])
        let quiet = try await NoteProcessor.process(note: Note(), micURL: silentURL, sysURL: silentURL, engine: unusedEngine, vocabulary: []) { _ in fatalError("Silence must not call a model") }
        check(quiet.status == "failed" && unusedEngine.prepared == 0, "Silent recordings must not ask for speech permissions or claim ready notes")
        print("Meeting checks passed: synthetic audio/transcripts, no microphone, model, network, or user-store access")
    }
}
