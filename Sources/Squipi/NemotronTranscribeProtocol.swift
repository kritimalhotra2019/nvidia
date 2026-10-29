import Foundation

/// Wire formats documented by DeepInfra and NVIDIA's vLLM model card. No speech/model SDK required.
enum NemotronTranscribeProtocol {
    static let modelID = "nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B"
    static let deepInfraEndpoint = "https://api.deepinfra.com/v1/inference/" + modelID
    static let maxDuration: Double = 3_600
    static let maxResponseBytes = 2_097_152
    enum Wire: String, CaseIterable { case deepInfra, nvidiaVLLM }
    struct Word: Equatable { let text: String; let start: Double; let end: Double }
    struct Result { let words: [Word]; let approximateTiming: Bool }
    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func endpoint(_ value: String) throws -> URL {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme == "https", let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else {
            throw Failure(message: "Use a full HTTPS transcription endpoint without credentials, query parameters or fragments.")
        }
        return url
    }

    static func wav(_ samples: [Float]) throws -> Data {
        guard !samples.isEmpty, samples.count <= Int(maxDuration * 16_000), samples.allSatisfy(\.isFinite) else {
            throw Failure(message: "Audio must contain finite samples and be no longer than one hour. The original recording is preserved.")
        }
        let byteCount = samples.count * 2
        var data = Data(capacity: byteCount + 44)
        func ascii(_ value: String) { data.append(contentsOf: value.utf8) }
        func u16(_ value: UInt16) { var x = value.littleEndian; withUnsafeBytes(of: &x) { data.append(contentsOf: $0) } }
        func u32(_ value: UInt32) { var x = value.littleEndian; withUnsafeBytes(of: &x) { data.append(contentsOf: $0) } }
        ascii("RIFF"); u32(UInt32(byteCount + 36)); ascii("WAVEfmt "); u32(16)
        u16(1); u16(1); u32(16_000); u32(32_000); u16(2); u16(16); ascii("data"); u32(UInt32(byteCount))
        for value in samples { u16(UInt16(bitPattern: Int16((max(-1, min(1, value)) * 32767).rounded()))) }
        return data
    }

    static func request(endpoint: URL, wire: Wire, key: String, audio: Data) throws -> URLRequest {
        _ = try self.endpoint(endpoint.absoluteString)
        guard !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              key.rangeOfCharacter(from: .controlCharacters) == nil else { throw Failure(message: "Save a transcription API key for this endpoint in Me.") }
        if key.hasPrefix("nvapi-"), !(endpoint.host == "nvidia.com" || endpoint.host?.hasSuffix(".nvidia.com") == true) {
            throw Failure(message: "An NVIDIA API key can only be sent to an NVIDIA endpoint. This provider requires its own key.")
        }
        guard audio.count > 44, audio.count <= Int(maxDuration * 32_000) + 44 else { throw Failure(message: "The audio upload is empty or too large.") }
        var request = URLRequest(url: endpoint, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 300)
        request.httpMethod = "POST"
        request.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if wire == .deepInfra {
            // DeepInfra's model-specific URL takes multipart 'audio', not the OpenAI 'file' field.
            guard endpoint.host == "api.deepinfra.com", endpoint.path == URL(string: deepInfraEndpoint)!.path else {
                throw Failure(message: "The DeepInfra format uses its documented Nemotron 3.5 Transcribe endpoint. Select NVIDIA vLLM for a compatible deployment.")
            }
            let boundary = "SquipiAudio-" + UUID().uuidString
            var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"meeting.wav\"\r\nContent-Type: audio/wav\r\n\r\n".utf8)
            body.append(audio); body.append(Data("\r\n--\(boundary)--\r\n".utf8))
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        } else {
            // Official NVIDIA audio_url protocol. Plain transcription, no unsupported diarisation claim.
            let body: [String: Any] = ["model": modelID, "stream": false, "temperature": 0,
                "max_tokens": 24_576, "chat_template_kwargs": ["enable_thinking": false],
                "messages": [["role": "user", "content": [
                    ["type": "text", "text": "Audio transcription. Language: as spoken. Wording: verbatim, including fillers and repetitions. Formatting: punctuation and capitalization. Output: transcript only."],
                    ["type": "audio_url", "audio_url": ["url": "data:audio/wav;base64," + audio.base64EncodedString()]]
                ]]]]
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        return request
    }

    static func parse(_ data: Data, wire: Wire, duration: Double) throws -> Result {
        guard data.count <= maxResponseBytes, duration.isFinite, duration > 0, duration <= maxDuration,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw Failure(message: "The transcription API returned an invalid response. The recording is preserved.")
        }
        if wire == .nvidiaVLLM {
            if let reportedModel = object["model"] as? String, reportedModel.caseInsensitiveCompare(modelID) != .orderedSame {
                throw Failure(message: "The endpoint reported a different model than Nemotron 3.5 Transcribe. No transcript was accepted.")
            }
            guard let choices = object["choices"] as? [[String: Any]], let first = choices.first,
                  first["finish_reason"] as? String == "stop",
                  let message = first["message"] as? [String: Any], let text = message["content"] as? String else {
                throw Failure(message: "The transcription API did not finish the transcript. No partial transcript was accepted; the recording is preserved.")
            }
            return try untimed(text, duration: duration)
        }
        if let status = (object["inference_status"] as? [String: Any])?["status"] as? String,
           ["failed", "error", "cancelled"].contains(status.lowercased()) {
            throw Failure(message: "The provider reported failed transcription. The recording is preserved.")
        }
        // Do not create per-word timestamps by distributing words evenly through the recording.
        for field in ["words", "segments"] {
            if let values = object[field] as? [[String: Any]], !values.isEmpty {
                var words: [Word] = []
                for item in values {
                    guard let raw = (item["text"] ?? item["word"]) as? String,
                          let start = item["start"] as? Double, let end = item["end"] as? Double,
                          start.isFinite, end.isFinite, start >= 0, start <= duration, end >= start, end <= duration + 2 else {
                        throw Failure(message: "The API returned invalid transcript timestamps. The recording is preserved.")
                    }
                    let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !text.isEmpty { words.append(Word(text: text, start: start, end: min(end, duration))) }
                }
                if !words.isEmpty { return Result(words: words.sorted { $0.start < $1.start }, approximateTiming: false) }
            }
        }
        return try untimed(object["text"] as? String ?? "", duration: duration)
    }

    private static func untimed(_ value: String, duration: Double) throws -> Result {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { throw Failure(message: "The transcription API returned no words. The recording is preserved.") }
        return Result(words: [Word(text: text, start: 0, end: duration)], approximateTiming: true)
    }
}
