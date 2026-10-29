import AVFoundation
import Speech
import SwiftUI

/// Runs only generated speech through the same voice selector and transcriber as chat.
/// No microphone, user recordings, cloud calls, or automatic asset downloads.
@MainActor final class SpeechDiagnostics: ObservableObject {
    static let shared = SpeechDiagnostics()
    @Published var running = false
    @Published var lines: [String] = []
    @Published var resultPath = ""
    private var task: Task<Void, Never>?
    func start() {
        guard !running else { return }
        running = true; lines = []; resultPath = ""
        task = Task {
            defer { running = false }
            let root = Store.shared.dir.appendingPathComponent("speech-checks/\(UUID().uuidString)")
            do { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }
            catch { lines = [error.localizedDescription]; return }
            let speechInstalled = await SpeechTranscriber.installedLocales
            let dictationInstalled = await DictationTranscriber.installedLocales
            var rows: [[String: Any]] = []
            @MainActor func save() {
                let document: [String: Any] = ["test": "synthetic speech roundtrip, not microphone accuracy", "authorization": SFSpeechRecognizer.authorizationStatus().rawValue,
                    "speech_supported": speechSupported, "dictation_supported": dictationSupported, "results": rows]
                if let data = try? JSONSerialization.data(withJSONObject: document, options: [.prettyPrinted, .sortedKeys]) {
                    let path = root.appendingPathComponent("results.json")
                    try? data.write(to: path, options: .atomic); resultPath = path.path
                }
            }
            let speechSupported = await SpeechTranscriber.supportedLocales.map { $0.identifier(.bcp47) }
            let dictationSupported = await DictationTranscriber.supportedLocales.map { $0.identifier(.bcp47) }
            for language in SquipiLanguage.released {
                if Task.isCancelled { break }
                lines.append("\(language.label): checking…")
                var row: [String: Any] = ["language": language.rawValue, "reference": language.speechSample]
                do {
                    guard let voice = PetVoice.voice(named: language.defaultVoice, language: language) else {
                        throw NSError(domain: "Squipi", code: 1, userInfo: [NSLocalizedDescriptionKey: "No matching installed voice. Add one in macOS Accessibility → Read & Speak."])
                    }
                    row["voice"] = voice.name; row["voice_locale"] = voice.language
                    let file = root.appendingPathComponent(language.rawValue + ".caf")
                    row["audio_seconds"] = try await render(language.speechSample, voice: voice, to: file)
                    row["audio_path"] = file.path
                    guard let selected = await AppleTranscriber.supported(language: language) else {
                        row["recognition"] = "Unavailable on this Mac"; rows.append(row); save()
                        lines[lines.count - 1] = "\(language.label): voice rendered; on-device recognition unavailable."
                        continue
                    }
                    row["recognizer"] = selected.dictation ? "Apple Dictation" : "Apple Speech"
                    row["recognition_locale"] = selected.locale.identifier(.bcp47)
                    let installed = selected.dictation ? dictationInstalled : speechInstalled
                    guard installed.contains(where: { $0.identifier(.bcp47) == selected.locale.identifier(.bcp47) }) else {
                        row["recognition"] = "Model not installed"; rows.append(row); save()
                        lines[lines.count - 1] = "\(language.label): voice rendered; select this language to prepare its speech model."
                        continue
                    }
                    guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
                        throw NSError(domain: "Squipi", code: 2, userInfo: [NSLocalizedDescriptionKey: "Speech Recognition permission is not granted."])
                    }
                    let engine = AppleTranscriber(language: language)
                    let words = try await engine.transcribeTimed(fileURL: file)
                    let transcript = AppleTranscriber.join(words)
                    row["transcript"] = transcript; row["recognition"] = transcript.isEmpty ? "Empty transcript" : "Transcript produced; review accuracy"
                    lines[lines.count - 1] = "\(language.label): \(transcript.isEmpty ? "empty transcript" : transcript)"
                } catch {
                    row["error"] = error.localizedDescription
                    lines[lines.count - 1] = "\(language.label): \(error.localizedDescription)"
                }
                rows.append(row); save()
            }
        }
    }
    func stop() { task?.cancel() }

    private func render(_ text: String, voice: AVSpeechSynthesisVoice, to url: URL) async throws -> Double {
        let synth = AVSpeechSynthesizer()
        let utterance = AVSpeechUtterance(string: text); utterance.voice = voice
        let writer = SpeechFileWriter(url: url)
        return try await withCheckedThrowingContinuation { continuation in
            let deadline = Task { @MainActor in
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                if writer.finish() {
                    synth.stopSpeaking(at: .immediate)
                    continuation.resume(throwing: NSError(domain: "Squipi", code: 3, userInfo: [NSLocalizedDescriptionKey: "Speech synthesis timed out."]))
                }
            }
            synth.write(utterance) { buffer in
                if let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 { writer.write(pcm) }
                else if writer.finish() {
                    deadline.cancel()
                    if writer.seconds > 0 { continuation.resume(returning: writer.seconds) }
                    else { continuation.resume(throwing: NSError(domain: "Squipi", code: 4, userInfo: [NSLocalizedDescriptionKey: "Voice produced no audio."])) }
                }
            }
        }
    }
}

struct SpeechDiagnosticsView: View {
    @ObservedObject private var diagnostics = SpeechDiagnostics.shared
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(diagnostics.running ? "Checking speech…" : "Check English speech") { diagnostics.start() }.disabled(diagnostics.running)
            Text("Uses synthetic audio on this Mac. Does not record your microphone or download models. Results distinguish unavailable models from transcription errors.").font(.caption).foregroundStyle(Color.petMuted)
            ForEach(Array(diagnostics.lines.enumerated()), id: \.offset) { _, text in Text(text).font(.caption).textSelection(.enabled) }
            if !diagnostics.resultPath.isEmpty { Text(diagnostics.resultPath).font(.caption2).textSelection(.enabled) }
        }
    }
}
