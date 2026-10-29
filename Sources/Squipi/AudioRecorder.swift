import AVFoundation

/// Captures the default mic and accumulates 16 kHz mono Float32 samples. `level` is a smoothed 0...1 loudness.
final class AudioRecorder {
    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var buffer: [Float] = []
    private let lock = NSLock()
    private(set) var level: Float = 0
    /// When set, samples stream here instead of accumulating in memory (meeting mode).
    var onSamples: (([Float]) -> Void)?
    private let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!

    enum RecorderError: Error { case noInput }

    func start() throws {
        lock.lock(); buffer.removeAll(keepingCapacity: true); level = 0; lock.unlock()
        let input = engine.inputNode
        let fmt = input.outputFormat(forBus: 0)
        guard fmt.sampleRate > 0 else { throw RecorderError.noInput }
        converter = AVAudioConverter(from: fmt, to: target)
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: fmt) { [weak self] buf, _ in self?.handle(buf) }
        engine.prepare()
        try engine.start()
    }

    func stop() -> [Float] {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        lock.lock(); defer { lock.unlock() }
        return buffer
    }

    private func handle(_ buf: AVAudioPCMBuffer) {
        guard let converter else { return }
        let ratio = target.sampleRate / buf.format.sampleRate
        let cap = AVAudioFrameCount(Double(buf.frameLength) * ratio) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: cap) else { return }
        var consumed = false
        var err: NSError?
        converter.convert(to: out, error: &err) { _, status in
            if consumed { status.pointee = .noDataNow; return nil }
            consumed = true
            status.pointee = .haveData
            return buf
        }
        guard err == nil, let ch = out.floatChannelData else { return }
        let n = Int(out.frameLength)
        guard n > 0 else { return }
        let samples = UnsafeBufferPointer(start: ch[0], count: n)
        var sum: Float = 0
        for s in samples { sum += s * s }
        let rms = (sum / Float(n)).squareRoot()
        lock.lock()
        if onSamples == nil { buffer.append(contentsOf: samples) }
        level = level * 0.6 + min(1, rms * 10) * 0.4
        lock.unlock()
        onSamples?(Array(samples))
    }
}

enum AudioFileLoader {
    /// Reads any audio file and returns 16 kHz mono Float32 samples.
    static func load16kMono(path: String) throws -> [Float] {
        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        defer { file.close() }
        let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
        guard file.length > 0 else { return [] }
        guard let inBuf = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length)),
              let block = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 16_384),
              let destination = inBuf.floatChannelData else {
            throw NSError(domain: "Squipi", code: 21, userInfo: [NSLocalizedDescriptionKey: "Could not allocate recording buffers."])
        }
        // A read may return fewer frames than requested without reaching EOF. Keep a separate
        // staging buffer so each read appends rather than overwriting the beginning of the audio.
        var loaded: AVAudioFrameCount = 0
        while loaded < inBuf.frameCapacity {
            try file.read(into: block)
            let count = min(block.frameLength, inBuf.frameCapacity - loaded)
            guard count > 0, let source = block.floatChannelData else {
                throw NSError(domain: "Squipi", code: 21, userInfo: [NSLocalizedDescriptionKey: "The recording ended before all its audio frames could be read."])
            }
            for channel in 0..<Int(file.processingFormat.channelCount) {
                destination[channel].advanced(by: Int(loaded)).update(from: source[channel], count: Int(count))
            }
            loaded += count
        }
        inBuf.frameLength = loaded
        // Meeting recordings already have this exact format; preserve their samples directly.
        if file.processingFormat.sampleRate == 16000, file.processingFormat.channelCount == 1,
           let channel = inBuf.floatChannelData?[0] {
            return Array(UnsafeBufferPointer(start: channel, count: Int(inBuf.frameLength)))
        }
        guard let conv = AVAudioConverter(from: file.processingFormat, to: target), let channels = inBuf.floatChannelData,
              let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: 16_384) else {
            throw NSError(domain: "Squipi", code: 21, userInfo: [NSLocalizedDescriptionKey: "Could not convert the recording's audio format."])
        }
        conv.primeMethod = .none
        var offset: AVAudioFrameCount = 0
        var samples: [Float] = []
        while true {
            var err: NSError?
            let before = offset
            let result = conv.convert(to: out, error: &err) { requested, status in
                guard offset < inBuf.frameLength else { status.pointee = .endOfStream; return nil }
                let count = min(requested, inBuf.frameLength - offset)
                guard let part = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: count), let destination = part.floatChannelData else {
                    status.pointee = .noDataNow; return nil
                }
                part.frameLength = count
                for channel in 0..<Int(file.processingFormat.channelCount) {
                    destination[channel].update(from: channels[channel].advanced(by: Int(offset)), count: Int(count))
                }
                offset += count; status.pointee = .haveData
                return part
            }
            if let err { throw err }
            if let channel = out.floatChannelData?[0], out.frameLength > 0 {
                samples.append(contentsOf: UnsafeBufferPointer(start: channel, count: Int(out.frameLength)))
            }
            if result == .endOfStream { return samples }
            if result == .error || (out.frameLength == 0 && offset == before) {
                throw NSError(domain: "Squipi", code: 21, userInfo: [NSLocalizedDescriptionKey: "Audio conversion stopped before the recording ended."])
            }
        }
    }

    /// Writes 16 kHz mono samples to a temporary CAF file (for engines that want a file).
    static func writeTempCAF(_ samples: [Float]) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("squipi-\(UUID().uuidString).caf")
        let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
        let file = try AVAudioFile(forWriting: url, settings: fmt.settings, commonFormat: .pcmFormatFloat32, interleaved: false)
        defer { file.close() }
        guard !samples.isEmpty else { return url }
        guard let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(samples.count)) else { return url }
        buf.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { src in buf.floatChannelData![0].update(from: src.baseAddress!, count: samples.count) }
        try file.write(from: buf)
        return url
    }
}
