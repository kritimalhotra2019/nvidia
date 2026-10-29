import AppKit
import CryptoKit
import Foundation

/// Optional whole-recording API. It never changes dictation or silently falls back from Apple Speech.
enum NemotronTranscribeAPI {
    static let enginePreference = "meetingTranscriptionEngine"
    static var selected: Bool { UserDefaults.standard.string(forKey: enginePreference) == "nemotron" }
    struct Configuration {
        let endpoint: URL
        let wire: NemotronTranscribeProtocol.Wire
        let key: String
    }
    static func account(endpoint: String) -> String {
        "nemotron-transcribe-v1-" + SHA256.hash(data: Data(endpoint.utf8)).map { String(format: "%02x", $0) }.joined()
    }
    static func configuration() throws -> Configuration {
        let endpoint = try NemotronTranscribeProtocol.endpoint(UserDefaults.standard.string(forKey: "nemotronTranscribeEndpoint") ?? "")
        let wire = NemotronTranscribeProtocol.Wire(rawValue: UserDefaults.standard.string(forKey: "nemotronTranscribeWire") ?? "") ?? .nvidiaVLLM
        guard let key = Keychain.get(account(endpoint: endpoint.absoluteString)), !key.isEmpty else {
            throw NemotronTranscribeProtocol.Failure(message: "Save the transcription provider’s API key in Me → Meeting transcription. This is separate from your Nebius research key. Your recording is preserved.")
        }
        return Configuration(endpoint: endpoint, wire: wire, key: key)
    }

    static func transcribe(samples: [Float], channel: String, noteID: UUID, configuration: Configuration) async throws -> NemotronTranscribeProtocol.Result {
        guard !UserDefaults.standard.bool(forKey: "localOnlyMode") else {
            throw NemotronTranscribeProtocol.Failure(message: "Local-only mode is on. No audio was sent; choose Apple Speech for local transcription.")
        }
        try Task.checkCancellation()
        let lease = try InferenceCoordinator.shared.beginModel()
        defer { InferenceCoordinator.shared.endModel(lease) }
        let audio = try NemotronTranscribeProtocol.wav(samples)
        let duration = Double(samples.count) / 16_000
        // Capture the request before consent. No personal vocabulary or research context is included.
        let request = try NemotronTranscribeProtocol.request(endpoint: configuration.endpoint, wire: configuration.wire, key: configuration.key, audio: audio)
        guard await approve(endpoint: configuration.endpoint, channel: channel, seconds: duration, bytes: audio.count) else {
            throw NemotronTranscribeProtocol.Failure(message: "Audio was not sent. The recording remains on this Mac.")
        }
        try Task.checkCancellation()
        guard !UserDefaults.standard.bool(forKey: "localOnlyMode") else {
            throw NemotronTranscribeProtocol.Failure(message: "Local-only mode is on. No audio was sent; choose Apple Speech for local transcription.")
        }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 300; config.timeoutIntervalForResource = 360
        config.httpCookieStorage = nil; config.urlCache = nil; config.httpMaximumConnectionsPerHost = 1
        let delegate = NoRedirect()
        let session = URLSession(configuration: config, delegate: delegate, delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        await CloudGate.logEgress(decision: "audio-send-once", project: noteID.uuidString,
            scope: configuration.endpoint.absoluteString, purpose: "Nemotron 3.5 Transcribe · \(configuration.endpoint.host ?? "") · \(channel)", bytes: audio.count)
        let (stream, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let tip = status == 401 || status == 403 ? "Check the saved transcription key and provider access." : status == 429 ? "The provider is rate limited; try again later." : "Check the endpoint and provider status."
            throw NemotronTranscribeProtocol.Failure(message: "Transcription API returned HTTP \(status). \(tip) The recording is preserved.")
        }
        guard response.expectedContentLength <= NemotronTranscribeProtocol.maxResponseBytes else {
            throw NemotronTranscribeProtocol.Failure(message: "The transcription response is too large. The recording is preserved.")
        }
        var data = Data()
        for try await byte in stream {
            if data.count % 16_384 == 0 { try Task.checkCancellation() }
            guard data.count < NemotronTranscribeProtocol.maxResponseBytes else { throw NemotronTranscribeProtocol.Failure(message: "The transcription response exceeded its limit.") }
            data.append(byte)
        }
        let result = try NemotronTranscribeProtocol.parse(data, wire: configuration.wire, duration: duration)
        let receipt: [String: Any] = ["at": ISO8601DateFormatter().string(from: Date()), "model": NemotronTranscribeProtocol.modelID,
            "endpoint": configuration.endpoint.absoluteString, "channel": channel, "audio_seconds": duration,
            "audio_sha256": SHA256.hash(data: audio).map { String(format: "%02x", $0) }.joined(),
            "request_format": configuration.wire.rawValue, "transcript_spans": result.words.count, "recording_extent_only": result.approximateTiming]
        let url = await Store.shared.noteDir(noteID).appendingPathComponent("transcribe-\(channel == "microphone" ? "microphone" : "call")-receipt.json")
        try JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted,.sortedKeys]).write(to: url, options: .atomic)
        await MainActor.run {
            UserDefaults.standard.set(configuration.endpoint.absoluteString + "|" + configuration.wire.rawValue, forKey: "nemotronTranscribeVerifiedEndpoint")
        }
        return result
    }

    @MainActor private static func approve(endpoint: URL, channel: String, seconds: Double, bytes: Int) -> Bool {
        guard !UserDefaults.standard.bool(forKey: "localOnlyMode"), NSApp != nil else { return false }
        let alert = NSAlert()
        alert.messageText = "Send this recording for transcription?"
        alert.informativeText = "NVIDIA Nemotron 3.5 Transcribe\n\(endpoint.absoluteString)\n\nThis sends the \(channel) audio (\(Int(seconds)) seconds; \(bytes.formatted()) bytes) to the configured provider. Audio may contain private speech. Your saved Words vocabulary and research history are not added. Provider charges may apply.\n\nThe original recording stays on your Mac. Permission applies only to this upload."
        alert.addButton(withTitle: "Send audio once"); alert.addButton(withTitle: "Keep on this Mac")
        NSApp.activate(ignoringOtherApps: true)
        return alert.runModal() == .alertFirstButtonReturn
    }

    private final class NoRedirect: NSObject, URLSessionTaskDelegate {
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    }
}
