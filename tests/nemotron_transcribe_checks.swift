import Foundation

@main struct NemotronProtocolChecks {
    static func main() throws {
        var count = 0
        func check(_ condition: @autoclosure () -> Bool, _ message: String) {
            precondition(condition(), message); count += 1
        }
        func rejects(_ label: String, _ body: () throws -> Void) {
            do { try body(); preconditionFailure(label) } catch { count += 1 }
        }
        for value in ["http://example.org/v1", "https://u:p@example.org/v1", "https://example.org/v1?key=x", "https://example.org/v1#key", "file:///tmp/audio", ""] {
            rejects("unsafe URL") { _ = try NemotronTranscribeProtocol.endpoint(value) }
        }
        let url = try NemotronTranscribeProtocol.endpoint("https://speech.example.org/v1/chat/completions")
        check(url.host == "speech.example.org", "valid endpoint")
        let wav = try NemotronTranscribeProtocol.wav([0,1,-1,0.5])
        check(wav.count == 52, "PCM length")
        check(String(data: wav.prefix(4), encoding: .utf8) == "RIFF", "WAV header")
        check(Array(wav[44..<52]) == [0,0,255,127,1,128,0,64], "PCM values")
        rejects("non-finite") { _ = try NemotronTranscribeProtocol.wav([.nan]) }
        rejects("empty") { _ = try NemotronTranscribeProtocol.wav([]) }
        let deep = try NemotronTranscribeProtocol.endpoint(NemotronTranscribeProtocol.deepInfraEndpoint)
        let multipart = try NemotronTranscribeProtocol.request(endpoint: deep, wire: .deepInfra, key: "fixture-only", audio: wav)
        check(multipart.value(forHTTPHeaderField: "Authorization") == "Bearer fixture-only", "separate bearer")
        check(String(decoding: multipart.httpBody!, as: UTF8.self).contains("name=\"audio\""), "DeepInfra audio field")
        rejects("DeepInfra format cannot send keys to custom endpoint") { _ = try NemotronTranscribeProtocol.request(endpoint: url, wire: .deepInfra, key: "fixture", audio: wav) }
        let request = try NemotronTranscribeProtocol.request(endpoint: url, wire: .nvidiaVLLM, key: "fixture", audio: wav)
        let body = try JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
        check(body["model"] as? String == NemotronTranscribeProtocol.modelID, "exact model requested")
        check(String(decoding: request.httpBody!, as: UTF8.self).contains("audio_url"), "official audio URL format")
        check(body["temperature"] as? Int == 0, "transcription thinking off")
        rejects("header injection") { _ = try NemotronTranscribeProtocol.request(endpoint: url, wire: .nvidiaVLLM, key: "bad\r\nkey", audio: wav) }
        rejects("NVIDIA secret must not go to DeepInfra") { _ = try NemotronTranscribeProtocol.request(endpoint: deep, wire: .deepInfra, key: "nvapi-fixture", audio: wav) }
        func json(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
        let timed = try NemotronTranscribeProtocol.parse(json(["text":"Hello world", "words":[["text":"Hello", "start":0.0, "end":0.5],["text":"world", "start":0.5, "end":1.0]]]), wire:.deepInfra, duration:1)
        check(timed.words.count == 2 && !timed.approximateTiming, "provider timing preserved")
        let whole = try NemotronTranscribeProtocol.parse(json(["text":"Hello world"]),wire:.deepInfra,duration:2)
        check(whole.words.count == 1 && whole.approximateTiming && whole.words[0].end == 2, "no fabricated per-word timing")
        rejects("bad timing") { _ = try NemotronTranscribeProtocol.parse(json(["words":[["text":"x", "start":2, "end":1]]]),wire:.deepInfra,duration:3) }
        rejects("empty transcript") { _ = try NemotronTranscribeProtocol.parse(json(["text":"  "]),wire:.deepInfra,duration:3) }
        rejects("truncated chat reply") { _ = try NemotronTranscribeProtocol.parse(json(["choices":[["finish_reason":"length","message":["content":"unfinished"]]]]),wire:.nvidiaVLLM,duration:3) }
        rejects("wrong model") { _ = try NemotronTranscribeProtocol.parse(json(["model":"different-model", "choices":[["finish_reason":"stop","message":["content":"words"]]]]),wire:.nvidiaVLLM,duration:3) }
        let complete = try NemotronTranscribeProtocol.parse(json(["choices":[["finish_reason":"stop","message":["content":"Completed words."]]]]),wire:.nvidiaVLLM,duration:3)
        check(complete.words.first?.text == "Completed words.", "complete reply")
        print("Nemotron Transcribe protocol: \(count) checks passed. No network, credentials, audio capture or model calls.")
    }
}
