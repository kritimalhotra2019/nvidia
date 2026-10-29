import AVFoundation
import Foundation

/// Squipi talks back with one of macOS's built-in voices. Everything is synthesised on this Mac.
@MainActor
final class PetVoice: NSObject, AVSpeechSynthesizerDelegate {
    static var voices: [String] { Array(Set(AVSpeechSynthesisVoice.speechVoices().filter { SquipiLanguage.current.matches(locale: $0.language) }.map(\.name))).sorted() }
    nonisolated static let defaultVoice = "Samantha"
    private let synth = AVSpeechSynthesizer()
    private var lastQuip = Date.distantPast
    var onTalking: ((Bool) -> Void)?

    var enabled: Bool { UserDefaults.standard.bool(forKey: "voiceOn") }
    var readBack: Bool { UserDefaults.standard.bool(forKey: "voiceReadBack") }
    var voiceName: String { UserDefaults.standard.string(forKey: "voiceName") ?? Self.defaultVoice }

    override init() {
        super.init()
        synth.delegate = self
    }

    static func voice(named name: String, language: SquipiLanguage = .current) -> AVSpeechSynthesisVoice? {
        let voices = AVSpeechSynthesisVoice.speechVoices().filter { language.matches(locale: $0.language) }
        return voices.first { $0.name.caseInsensitiveCompare(name) == .orderedSame && $0.language == language.speechLocale }
            ?? voices.first { $0.name.caseInsensitiveCompare(name) == .orderedSame }
            ?? voices.first { $0.name == language.defaultVoice }
            ?? voices.first { $0.language == language.speechLocale }
            ?? voices.first
    }

    func say(_ text: String, force: Bool = false) {
        guard enabled || force, !text.isEmpty else { return }
        let u = AVSpeechUtterance(string: text)
        guard let selectedVoice = Self.voice(named: voiceName) else { return }
        u.voice = selectedVoice
        u.rate = 0.5
        u.pitchMultiplier = 1.05
        u.volume = 0.9
        synth.stopSpeaking(at: .immediate)
        synth.speak(u)
    }

    func stop() { synth.stopSpeaking(at: .immediate) }
    var isSpeaking: Bool { synth.isSpeaking }

    /// A short remark, not every time, never twice in a row too quickly.
    func quip(_ options: [String], chance: Double = 1.0) {
        guard enabled, Date().timeIntervalSince(lastQuip) > 4, Double.random(in: 0...1) < chance else { return }
        lastQuip = Date()
        say(options.randomElement()!)
    }

    // events
    func woke() { quip([SquipiLanguage.text("Ready when you are.")]) }
    func dictated(_ text: String) {
        if readBack { say(text); return }
        quip([SquipiLanguage.text("Done.")], chance: 0.4)
    }
    func heardNothing() { quip([SquipiLanguage.text("I did not catch that. Please try again.")]) }
    func notesStarted() { say(SquipiLanguage.text("Taking notes.")) }
    func notesReady(title: String) { say(SquipiLanguage.text("Notes are ready.") + " " + title) }
    func grabbed() { quip([SquipiLanguage.text("Careful!")], chance: 0.8) }
    func test() { say(SquipiLanguage.current.speechSample, force: true) }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didStart u: AVSpeechUtterance) { Task { @MainActor in self.onTalking?(true) } }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) { Task { @MainActor in self.onTalking?(false) } }
    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) { Task { @MainActor in self.onTalking?(false) } }
}
