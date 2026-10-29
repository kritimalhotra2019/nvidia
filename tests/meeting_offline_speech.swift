import Foundation
import Speech

/// Optional integration probe: never asks for permissions, installs assets, opens a microphone,
/// accesses Store, or invokes a summarization model. Existing on-device speech assets only.
@main struct OfflineMeetingSpeech {
    static func main() async throws {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else {
            print("SKIPPED: Speech Recognition is not already authorized for this test process. No permission requested.")
            return
        }
        let installed = await SpeechTranscriber.installedLocales
        guard installed.contains(where: { $0.identifier(.bcp47).caseInsensitiveCompare("en-US") == .orderedSame }) else {
            print("SKIPPED: English on-device speech assets are not installed. No download requested.")
            return
        }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("squipi-synthetic-speech-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let audio = directory.appendingPathComponent("fixture.caf")
        let synth = Process()
        synth.executableURL = URL(fileURLWithPath: "/usr/bin/say")
        synth.arguments = ["-o", audio.path, "The research meeting is on Friday. Please send the draft report tomorrow."]
        try synth.run(); synth.waitUntilExit()
        guard synth.terminationStatus == 0 else { throw NSError(domain: "fixture", code: 1, userInfo: [NSLocalizedDescriptionKey: "Local speech synthesis failed."]) }
        // Deliberately skip prepare(), which may otherwise request permissions or download assets.
        let words = try await AppleTranscriber().transcribeTimed(fileURL: audio)
        let transcript = AppleTranscriber.join(words)
        guard transcript.lowercased().contains("friday"), transcript.lowercased().contains("report") else {
            throw NSError(domain: "fixture", code: 2, userInfo: [NSLocalizedDescriptionKey: "Synthetic transcript missed expected keywords: \(transcript)"])
        }
        print("PASSED: Installed on-device speech transcribed a synthetic TTS fixture: \(transcript)")
    }
}
