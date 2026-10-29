import AVFoundation
import Foundation
import Speech

struct TimedWord: Codable { var text: String; var start: Double; var end: Double }

protocol Transcriber: AnyObject {
    var name: String { get }
    var vocabulary: [String] { get set }
    func prepare() async throws
    func transcribe(_ samples16k: [Float]) async throws -> String
    func transcribeTimed(_ samples16k: [Float]) async throws -> [TimedWord]
}

/// Apple speech and dictation models run on this Mac. Never fall back to English or a server.
final class AppleTranscriber: Transcriber {
    let name = "Apple on-device"
    var vocabulary: [String] = []
    let language: SquipiLanguage
    private var locale: Locale?
    private var useDictation = false
    private var prepared = false
    init(language: SquipiLanguage = .current) { self.language = language }

    static func supported(language: SquipiLanguage) async -> (locale: Locale, dictation: Bool)? {
        let speech = await SpeechTranscriber.supportedLocales
        if let match = speech.first(where: { $0.identifier(.bcp47).caseInsensitiveCompare(language.speechLocale) == .orderedSame })
            ?? speech.first(where: { language.matches(locale: $0.identifier(.bcp47)) }) { return (match, false) }
        let dictation = await DictationTranscriber.supportedLocales
        if let match = dictation.first(where: { $0.identifier(.bcp47).caseInsensitiveCompare(language.speechLocale) == .orderedSame })
            ?? dictation.first(where: { language.matches(locale: $0.identifier(.bcp47)) }) { return (match, true) }
        return nil
    }

    func prepare() async throws {
        if prepared { return }
        try await Self.authorize()
        guard let selection = await Self.supported(language: language) else {
            throw NSError(domain: "Squipi", code: 2, userInfo: [NSLocalizedDescriptionKey: "Apple on-device speech recognition is unavailable for \(language.englishName) on this Mac. Typed chat still works. No English or cloud fallback was used."])
        }
        locale = selection.locale; useDictation = selection.dictation
        let module: any SpeechModule = useDictation
            ? DictationTranscriber(locale: selection.locale, preset: .timeIndexedLongDictation)
            : SpeechTranscriber(locale: selection.locale, preset: .transcription)
        if let req = try await AssetInventory.assetInstallationRequest(supporting: [module]) { try await req.downloadAndInstall() }
        prepared = true
    }

    func transcribe(_ samples: [Float]) async throws -> String {
        let words = try await transcribeTimed(samples)
        return Self.join(words)
    }

    func transcribeTimed(_ samples: [Float]) async throws -> [TimedWord] {
        let url = try AudioFileLoader.writeTempCAF(samples)
        defer { try? FileManager.default.removeItem(at: url) }
        return try await transcribeTimed(fileURL: url)
    }

    func transcribeTimed(fileURL: URL) async throws -> [TimedWord] {
        let modelLease = try InferenceCoordinator.shared.beginModel()
        defer { InferenceCoordinator.shared.endModel(modelLease) }
        if !prepared { try await prepare() }
        guard let locale else { throw NSError(domain: "Squipi", code: 3, userInfo: [NSLocalizedDescriptionKey: "Speech language is not ready."]) }
        let module: any SpeechModule
        let collector: Task<[TimedWord], Error>
        if useDictation {
            let transcriber = DictationTranscriber(locale: locale, preset: .timeIndexedLongDictation)
            module = transcriber
            collector = Task {
                var out: [TimedWord] = []
                for try await result in transcriber.results where result.isFinal { Self.append(result.text, to: &out) }
                return out
            }
        } else {
            let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [], attributeOptions: [.audioTimeRange])
            module = transcriber
            collector = Task {
                var out: [TimedWord] = []
                for try await result in transcriber.results where result.isFinal { Self.append(result.text, to: &out) }
                return out
            }
        }
        let analyzer = SpeechAnalyzer(modules: [module])
        if !vocabulary.isEmpty {
            let ctx = AnalysisContext()
            ctx.contextualStrings = [.general: vocabulary]
            try await analyzer.setContext(ctx)
        }
        let file = try AVAudioFile(forReading: fileURL)
        do {
            if let last = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: last)
            } else {
                await analyzer.cancelAndFinishNow()
            }
            return try await collector.value
        } catch {
            collector.cancel()
            await analyzer.cancelAndFinishNow()
            throw error
        }
    }

    private static func append(_ value: AttributedString, to out: inout [TimedWord]) {
        for run in value.runs {
            let text = String(value[run.range].characters).trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { continue }
            let start = run.audioTimeRange.map { CMTimeGetSeconds($0.start) } ?? (out.last?.end ?? 0)
            let duration = run.audioTimeRange.map { CMTimeGetSeconds($0.duration) } ?? 0
            out.append(TimedWord(text: text, start: start, end: start + duration))
        }
    }

    static func join(_ words: [TimedWord]) -> String {
        var s = ""
        for w in words {
            if !s.isEmpty, !w.text.hasPrefix(","), !w.text.hasPrefix("."), !w.text.hasPrefix("?"), !w.text.hasPrefix("!") { s += " " }
            s += w.text
        }
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func authorize() async throws {
        var status = SFSpeechRecognizer.authorizationStatus()
        if status == .notDetermined {
            status = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) } }
        }
        guard status == .authorized else { throw NSError(domain: "Squipi", code: 1, userInfo: [NSLocalizedDescriptionKey: "Speech recognition permission not granted"]) }
    }
}

