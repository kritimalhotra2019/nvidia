import AVFoundation
import Foundation

/// Records you (mic) and your call (system audio) to two CAF files, then turns them into a speaker-labelled note.
@MainActor
final class MeetingRecorder: ObservableObject {
    @Published var isRecording = false
    @Published var isProcessing = false
    @Published var elapsed: Double = 0
    @Published var currentNoteID: UUID?

    private let mic: AudioRecorder
    private let tap = SystemAudioTap()
    private var micWriter: CafWriter?
    private var sysWriter: CafWriter?
    private var timer: Timer?
    private var startedAt = Date()
    private var processingTask: Task<Void, Never>?
    var onStateChange: ((String) -> Void)?     // pet state
    var engineProvider: () -> Transcriber = { AppleTranscriber() }
    var dictationActive: () -> Bool = { false }

    init(mic: AudioRecorder) { self.mic = mic }

    func start() {
        guard !isRecording, !isProcessing, !dictationActive() else { return }
        var note = Note()
        let dir = Store.shared.noteDir(note.id)
        do {
            micWriter = try CafWriter(url: dir.appendingPathComponent("mic.caf"))
            sysWriter = try CafWriter(url: dir.appendingPathComponent("sys.caf"))
            try Store.shared.saveNote(note)
        } catch { failRecording(note, message: "Could not save recording files: \(error.localizedDescription)"); return }
        let micFile = micWriter, callFile = sysWriter
        mic.onSamples = { s in micFile?.append(s) }
        tap.onSamples = { s in callFile?.append(s) }
        var micStarted = false, callStarted = false
        var warnings: [String] = []
        startedAt = Date(); elapsed = 0
        do { try mic.start(); micStarted = true } catch { warnings.append("Microphone unavailable; your side was not recorded.") }
        do { try tap.start(); callStarted = true } catch {
            NSLog("system audio tap failed: \(error)")
            warnings.append("Couldn't hear the call (system audio permission?); call audio was not recorded.")
        }
        guard micStarted || callStarted else { failRecording(note, message: warnings.joined(separator: " ")); return }
        if callStarted { warnings.append("Call participants share one audio label; individual speakers are not identified automatically.") }
        note.captureWarning = warnings.isEmpty ? nil : warnings.joined(separator: " ")
        note.error = note.captureWarning
        do { try Store.shared.saveNote(note) }
        catch { failRecording(note, message: "Could not save the recording: \(error.localizedDescription)"); return }
        isRecording = true
        currentNoteID = note.id
        onStateChange?("noting")
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self else { return }
            Task { @MainActor in self.elapsed = Date().timeIntervalSince(self.startedAt) }
        }
    }

    private func failRecording(_ source: Note, message: String) {
        _ = mic.stop(); mic.onSamples = nil; tap.stop(); tap.onSamples = nil
        let micFile = micWriter, callFile = sysWriter
        micWriter = nil; sysWriter = nil
        Task { _ = await micFile?.finish(); _ = await callFile?.finish() }
        var note = source; note.status = "failed"; note.error = message
        Store.shared.upsert(note) // preserve an in-memory error even if the disk is unavailable
        isRecording = false; isProcessing = false; currentNoteID = nil
        onStateChange?("error")
    }

    func stop() {
        guard isRecording, let id = currentNoteID else { return }
        timer?.invalidate(); timer = nil
        _ = mic.stop(); mic.onSamples = nil
        tap.stop(); tap.onSamples = nil
        isRecording = false
        let duration = Date().timeIntervalSince(startedAt)
        guard var note = Store.shared.notes.first(where: { $0.id == id }) else { return }
        note.duration = duration
        note.status = "processing"
        do { try Store.shared.saveNote(note) }
        catch { failRecording(note, message: "Could not save the recording: \(error.localizedDescription)"); return }
        onStateChange?("thinking")
        isProcessing = true
        let engine = engineProvider()
        let vocab = Store.shared.vocabulary.words
        guard let micFile = micWriter, let callFile = sysWriter else { failRecording(note, message: "Recording files are missing."); return }
        micWriter = nil; sysWriter = nil
        processingTask = Task { [weak self] in
            let micError = await micFile.finish(), callError = await callFile.finish()
            var n = note
            do {
                if let error = micError ?? callError { throw error }
                n = try await NoteProcessor.process(note: n, micURL: micFile.url, sysURL: callFile.url, engine: engine, vocabulary: vocab,
                    saveTranscript: { saved in try await MainActor.run { try Store.shared.saveNote(saved) } })
            } catch is CancellationError {
                n = Store.shared.notes.first(where: { $0.id == n.id }) ?? n
                n.status = n.segments.isEmpty ? "failed" : "transcribed"
                n.error = "Meeting processing stopped. The recording and any saved transcript remain on this Mac."
            } catch {
                n = Store.shared.notes.first(where: { $0.id == n.id }) ?? n
                n.status = n.segments.isEmpty ? "failed" : "transcribed"
                n.error = error.localizedDescription
            }
            do { try Store.shared.saveNote(n) }
            catch {
                n.status = "failed"; n.error = "Could not save meeting notes: \(error.localizedDescription)"
                Store.shared.upsert(n)
            }
            self?.isProcessing = false
            self?.processingTask = nil
            self?.onStateChange?(n.status == "ready" ? "done" : "error")
        }
    }

    func cancelProcessing() { processingTask?.cancel() }
}

enum NoteProcessor {
    typealias SummaryWriter = (String) async throws -> (title: String, summary: String)
    typealias TranscriptSaver = (Note) async throws -> Void

    static func process(note: Note, micURL: URL, sysURL: URL, engine: Transcriber, vocabulary: [String],
                        saveTranscript: TranscriptSaver = { _ in },
                        writer: SummaryWriter = { try await Summarizer.summarize(transcript: $0) }) async throws -> Note {
        var note = note
        engine.vocabulary = vocabulary
        let mic = try AudioFileLoader.load16kMono(path: micURL.path)
        let sys = try AudioFileLoader.load16kMono(path: sysURL.path)
        note.duration = max(note.duration, Double(max(mic.count, sys.count)) / 16_000)
        guard hasSpeech(mic) || hasSpeech(sys) else {
            note.title = "Quiet meeting"; note.status = "failed"; note.summary = ""; note.segments = []
            note.error = "I didn't hear any speech. No meeting notes were generated."
            return note
        }
        try await engine.prepare()
        note.transcriptionProvider = engine.name

        var micWords: [TimedWord] = [], sysWords: [TimedWord] = []
        var transcriptionWarnings: [String] = []
        if hasSpeech(mic) {
            do { micWords = try await engine.transcribeTimed(mic) }
            catch is CancellationError { throw CancellationError() }
            catch { transcriptionWarnings.append("Your microphone transcript could not be completed: \(error.localizedDescription)") }
        }
        if hasSpeech(sys) {
            do { sysWords = try await engine.transcribeTimed(sys) }
            catch is CancellationError { throw CancellationError() }
            catch { transcriptionWarnings.append("The call transcript could not be completed: \(error.localizedDescription)") }
        }
        if !transcriptionWarnings.isEmpty {
            note.captureWarning = ([note.captureWarning].compactMap { $0 } + transcriptionWarnings).joined(separator: " ")
        }
        note.segments = assemble(microphone: micWords, call: sysWords)
        note.duration = max(note.duration, Double(max(mic.count, sys.count)) / 16_000, note.segments.map(\.end).max() ?? 0)
        if note.segments.isEmpty {
            note.title = "No transcript"; note.status = "failed"; note.summary = ""
            note.error = ([note.captureWarning].compactMap { $0 } + ["Speech recognition returned no transcript."]).joined(separator: " ")
            return note
        }
        note.title = fallbackTitle(note.segments)
        note.status = "transcribed"
        try await saveTranscript(note)
        try Task.checkCancellation()
        return await summarize(note, writer: writer)
    }

    /// Separate the recorded microphone and call channels; do not fabricate identities within call audio.
    static func assemble(microphone: [TimedWord], call: [TimedWord]) -> [Segment] {
        let labeled: [(TimedWord, String)] = microphone.map { ($0, "me") } + call.map { ($0, "s1") }
        var ordered: [(index: Int, word: TimedWord, speaker: String)] = []
        for (index, item) in labeled.enumerated() {
            let word = item.0
            guard !word.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  word.start.isFinite, word.end.isFinite, word.start >= 0, word.end >= word.start else { continue }
            ordered.append((index, word, item.1))
        }
        ordered.sort { a, b in a.word.start == b.word.start ? a.index < b.index : a.word.start < b.word.start }
        var segments: [Segment] = []
        for item in ordered {
            let w = item.word, spk = item.speaker
            if var last = segments.last, last.speaker == spk, w.start - last.end < 1.5 {
                last.text = AppleTranscriber.join([TimedWord(text: last.text, start: last.start, end: last.end), w]); last.end = max(last.end, w.end)
                segments[segments.count - 1] = last
            } else {
                segments.append(Segment(speaker: spk, start: w.start, end: w.end, text: w.text))
            }
        }
        return segments
    }

    static func fallbackTitle(_ segments: [Segment]) -> String {
        let words = segments.first?.text.split(separator: " ").prefix(6).joined(separator: " ") ?? "Meeting"
        let title = words.trimmingCharacters(in: .punctuationCharacters)
        return title.isEmpty ? "Meeting" : title
    }

    static func transcriptText(_ note: Note) -> String {
        note.segments.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
            .map { "\(note.name(for: $0.speaker)): \($0.text)" }.joined(separator: "\n")
    }

    static func exportMarkdown(_ note: Note) -> String {
        var text = "# \(note.title)\n\(note.date.formatted(date: .long, time: .shortened)) · \(timecode(note.duration))\n\n"
        if let provider = note.transcriptionProvider { text += "Transcription: \(provider)\n\n" }
        if let warning = note.error ?? note.captureWarning { text += "Note: \(warning)\n\n" }
        if !note.summary.isEmpty { text += note.summary + "\n\n" }
        text += "## Transcript\n" + note.segments.map {
            "[\(timecode($0.start))–\(timecode($0.end))] \(note.name(for: $0.speaker)): \($0.text)"
        }.joined(separator: "\n")
        return text
    }

    static func timecode(_ seconds: Double) -> String {
        let value = seconds.isFinite ? Int(max(0, min(seconds, 315_360_000))) : 0
        return String(format: "%02d:%02d", value / 60, value % 60)
    }

    /// (Re)write the notes for an existing transcript. Never throws; errors land in note.error.
    static func summarize(_ note: Note, writer: SummaryWriter = { try await Summarizer.summarize(transcript: $0) }) async -> Note {
        var note = note
        note.error = note.captureWarning
        let transcript = transcriptText(note)
        guard !transcript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            note.status = "failed"; note.summary = ""; note.error = "There is no transcript to summarize."; return note
        }
        do {
            let (title, summary) = try await writer(transcript)
            guard !summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw NSError(domain: "Squipi", code: 13, userInfo: [NSLocalizedDescriptionKey: "The note writer returned no meeting notes. The transcript is preserved."])
            }
            note.title = title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? fallbackTitle(note.segments) : title
            note.summary = summary
            note.status = "ready"
        } catch {
            note.status = "transcribed"
            note.error = [note.captureWarning, error.localizedDescription].compactMap { $0 }.joined(separator: " ")
        }
        return note
    }

    static func hasSpeech(_ s: [Float]) -> Bool {
        guard s.count >= 1600 else { return false }
        var peak: Float = 0
        for i in stride(from: 0, to: s.count, by: 8) where s[i].isFinite { peak = max(peak, abs(s[i])) }
        return peak > 0.01
    }

    struct SpeakerSpan { var id: String; var start: Double; var end: Double }
    /// Speaker labels on the system-audio side. Squipi ships no diarizer (speech is Apple's on-device engine only),
    /// so everyone who is not the user is labelled "s1". Returning no spans makes the caller do exactly that.
    static func diarize(_ samples: [Float], threshold: Float = 0.7) async throws -> [SpeakerSpan] {
        []
    }
}
