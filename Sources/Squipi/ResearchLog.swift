import Foundation

/// The project's research log: a Markdown file the user can open and keep, plus a JSON index for the model.
/// Lives in the worker's project directory next to the artifacts.
@MainActor
enum ResearchLog {
    static var url: URL { Worker.shared.projectDir.appendingPathComponent("research-log.md") }
    private static var indexURL: URL { Worker.shared.projectDir.appendingPathComponent("research-log.json") }

    static func entries(limit: Int = 20) -> [[String: Any]] {
        guard let data = try? Data(contentsOf: indexURL), let all = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] else { return [] }
        return Array(all.suffix(max(1, limit)).reversed())
    }

    @discardableResult
    static func append(title: String, text: String, kind: String, sources: [String]) throws -> URL {
        try FileManager.default.createDirectory(at: Worker.shared.projectDir, withIntermediateDirectories: true)
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm"
        let stamp = f.string(from: Date())
        var md = "\n## \(title)\n\n*\(kind) · \(stamp)*\n\n\(text)\n"
        if !sources.isEmpty { md += "\nSources:\n" + sources.map { "- \($0)" }.joined(separator: "\n") + "\n" }
        if !FileManager.default.fileExists(atPath: url.path) {
            try "# Research log\n\nKept by Squipi. Every entry was added on request or after an analysis; edit freely.\n".write(to: url, atomically: true, encoding: .utf8)
        }
        let h = try FileHandle(forWritingTo: url)
        defer { try? h.close() }
        try h.seekToEnd()
        try h.write(contentsOf: Data(md.utf8))
        var all = (try? Data(contentsOf: indexURL)).flatMap { (try? JSONSerialization.jsonObject(with: $0)) as? [[String: Any]] } ?? []
        all.append(["title": title, "text": text, "kind": kind, "sources": sources, "at": stamp])
        if let data = try? JSONSerialization.data(withJSONObject: all, options: [.prettyPrinted]) { try? data.write(to: indexURL, options: .atomic) }
        NSLog("LOG %@: %@", kind, title)
        return url
    }
}
